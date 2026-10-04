# AMIGO BackEnd — FastAPI API 서버

AI 기반 인수인계서 자동 작성 시스템의 **API 서버**입니다.
화면(FrontEnd)의 요청을 받아 파일을 안전하게 저장하고 RAG 모듈에 넘겨 지식베이스를 만들며,
AI 에이전트를 백그라운드에서 실행하고, 세션·자료·대화 기록을 DB(SQLite/PostgreSQL)에 저장합니다.

```
FrontEnd(React, :5173) ──/api──▶ BackEnd(이 저장소, :8000)
                                   ├─ amigo_rag  : 파싱·청킹·ChromaDB 검색 (../RAG)
                                   ├─ amigo_agent: LangGraph STAGE 1~4 (../AI)
                                   └─ data/      : app.db · uploads/ · rag/ · agent_checkpoints.sqlite
```

## 실행

작업 폴더에 네 저장소를 나란히 받아 둡니다: `workspace/{RAG,AI,BackEnd,FrontEnd}`

```bash
cd BackEnd
scripts/setup.sh          # 가상환경 + ../RAG, ../AI 를 editable 설치 + .env 생성
scripts/dev.sh            # http://localhost:8000/docs  (Swagger)
pytest -q                 # API 통합 테스트(오프라인 엔진)
```

Windows(PowerShell):

```powershell
cd BackEnd
python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt; copy .env.example .env
uvicorn app.main:app --reload --port 8000
```

`.env` 에 `ANTHROPIC_API_KEY` 를 넣으면 Claude 로, 없으면 규칙 기반 오프라인 엔진으로 동작합니다.
`AMIGO_FRONTEND_DIST=../FrontEnd/dist` 를 지정하면 빌드된 화면까지 8000 포트 하나로 제공합니다.

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/health` | 서버 상태, AI 엔진(claude/offline), 모델 |
| GET | `/api/config` | 업로드 제한·허용 확장자, 링크 유형, **인수인계서 양식(슬롯)**, 단계 정의 |
| POST | `/api/sessions` | 세션 생성(인계자 기초 정보: 성명·소속·직책·담당 업무·인수자·인계일) |
| GET | `/api/sessions` | 세션 목록(`X-User-Id` 헤더 기준) |
| GET | `/api/sessions/{id}` | 세션 상세: 단계·상태·진행률·슬롯 충족 현황·공백·자료 목록 |
| GET | `/api/sessions/{id}/state?after=<메시지ID>` | **폴링용**: 세션 상세 + 커서 이후 새 메시지 |
| DELETE | `/api/sessions/{id}` | 세션 삭제(업로드 파일·지식베이스·대화 상태 포함) |
| POST | `/api/sessions/{id}/upload` | 파일 업로드(multipart `files`, 여러 개) → 백그라운드 적재 |
| POST | `/api/sessions/{id}/links` | 링크 등록 `{"urls": [...], "link_type": "auto"}` (컨플루언스/나누미/웹) |
| GET / DELETE | `/api/sessions/{id}/sources[/{source_id}]` | 자료 목록 / 삭제 |
| POST | `/api/sessions/{id}/analyze` | STAGE 1 분석 시작(다시 분석) |
| POST | `/api/sessions/{id}/chat` | 답변·검토 의견 `{"text": "..."}` |
| POST | `/api/sessions/{id}/skip` | 현재 질문 건너뛰기 |
| POST | `/api/sessions/{id}/generate` | 지금 바로 STAGE 4 문서 생성 |
| POST | `/api/sessions/{id}/retry` | 오류로 멈춘 작업 재시도 |
| GET | `/api/sessions/{id}/document` | 생성된 인수인계서(Markdown) |
| GET | `/api/sessions/{id}/download?format=pdf\|docx\|md` | 인수인계서 내려받기 |

### 비동기 처리와 폴링 규약

분석·답변 처리·문서 생성은 수십 초가 걸릴 수 있어 **202 Accepted 를 즉시 반환**하고 스레드 풀에서 실행합니다.
화면은 `GET /state?after=<마지막 메시지 ID>` 를 1초 간격으로 호출해 다음을 받습니다.

- `session.status`: `idle` → `running`(AI 처리 중) → `waiting`(사용자 입력 대기) / `error`(메시지에 재시도 안내)
- `session.stage`: `setup` → `analyzing`(STAGE 1) → `summary`(2) → `qna`(3) → `composing` → `review`(4)
- `session.progress`: `{"message": "'협업 관계' 분석 중", "current": 3, "total": 6}`
- `session.slots`: 슬롯별 항목·충족 수준(분석 중에도 슬롯이 끝날 때마다 갱신)
- `messages`: AI 메시지(`summary`/`question`/`confirm`/`info`/`document`/`error`)와 `meta`(빠른 답장 버튼, gap_id 등)

세션당 AI 작업은 한 번에 하나만 실행되며 처리 중 요청은 `409` 로 거절합니다.
분석이 끝난 뒤(질의응답·검토 단계) 업로드한 자료는 적재가 끝나면 자동으로 에이전트에 전달되어 **재분석**됩니다.

## DB 스키마 (SQLAlchemy)

| 테이블 | 주요 컬럼 |
|---|---|
| `sessions` | id, user_id, 인계자 기초 정보, **stage, status, progress**, error, engine, slots_json, gaps_json, question_count, document_md, document_version |
| `sources` | id(=RAG source_id), session_id, kind(file/link), name, stored_path/url, link_type, size, status(pending/processing/ready/failed), error, chunk_count, warnings |
| `messages` | id(증가 커서), session_id, role(user/assistant/system), kind, content, meta_json, created_at |

에이전트 내부 상태(LangGraph)는 `data/agent_checkpoints.sqlite` 에 따로 저장되어 서버를 재시작해도 대화를 이어 갑니다.
`AMIGO_DATABASE_URL` 로 PostgreSQL 을 쓸 수 있습니다(드라이버 `psycopg` 추가 설치).

## 파일 처리와 보안

- 저장 위치: `data/uploads/<세션ID>/<source_id><확장자>` — 사용자가 보낸 파일명은 **표시용으로만** 쓰고 경로에 쓰지 않습니다.
- 확장자 허용 목록(RAG 가 읽을 수 있는 형식), 크기 제한(`AMIGO_MAX_UPLOAD_MB`), 빈 파일 거절을 **스트리밍 저장 중** 검사합니다.
- 링크 수집은 RAG 의 SSRF 방지 검사(링크로컬·루프백 차단, 허용 도메인)를 거칩니다.
- `X-User-Id` 는 프로토타입용 사용자 구분입니다. 실제 서비스에서는 사내 SSO/인증으로 교체해야 합니다.

## 문서 내보내기

`app/services/exporters.py` 가 Markdown 을 Word/PDF 로 변환합니다.
Word 는 '맑은 고딕'을 동아시아 글꼴로 지정하고, PDF 는 시스템의 한글 TTF(나눔고딕·맑은 고딕·애플고딕 등)를 찾아 포함합니다.
서버에 한글 글꼴이 없으면 `AMIGO_PDF_FONT` 로 지정하거나 `fonts-nanum` 패키지를 설치하세요.
