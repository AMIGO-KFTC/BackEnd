"""API 요청/응답 스키마."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

LOGIN_ID_PATTERN = r"^[A-Za-z0-9._@-]{3,50}$"


class _Stripped(BaseModel):
    """문자열 필드 앞뒤 공백 제거(비밀번호는 입력 그대로 비교하므로 제외)."""

    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, value, info: ValidationInfo):
        return value.strip() if isinstance(value, str) and info.field_name != "password" else value


# ---------------------------------------------------------------- 로그인 · 프로필 · 단위업무


class Result(BaseModel):
    """성공/실패를 화면에 바로 띄울 수 있는 공통 응답. 실패(4xx)도 같은 모양으로 온다(success=false, message, detail)."""

    success: bool = True
    message: str = ""


class SignupIn(_Stripped):
    login_id: str = Field(pattern=LOGIN_ID_PATTERN, description="아이디(영문·숫자·._@- 3~50자)")
    password: str = Field(min_length=8, max_length=128, description="비밀번호(8자 이상)")
    name: str = Field(min_length=1, max_length=50, description="이름")


class LoginIn(_Stripped):
    login_id: str = Field(min_length=1, max_length=50, description="아이디")
    password: str = Field(min_length=1, max_length=128, description="비밀번호")


class ProfileIn(_Stripped):
    department: str = Field(min_length=1, max_length=100, description="부서")
    position: str = Field(min_length=1, max_length=50, description="직위")
    team: str = Field(default="", max_length=100, description="팀")
    name: str = Field(min_length=1, max_length=50, description="이름")


class ProfileOut(BaseModel):
    department: str
    position: str
    team: str
    name: str


class UserOut(BaseModel):
    id: int
    login_id: str
    name: str
    profile: ProfileOut | None


class LoginOut(Result):
    name: str
    user: UserOut
    token: str = Field(description="로그인 세션 토큰. 쿠키(amigo_session)로도 내려가며, 쿠키를 못 쓰는 클라이언트는 Authorization: Bearer 로 보낸다")
    expires_at: datetime


class MeOut(Result):
    user: UserOut


class ProfileResult(Result):
    profile: ProfileOut


HandoverMode = Literal["reassignment", "leave"]
DATE_PATTERN = r"^(\d{4}-\d{2}-\d{2})?$"


class UnitTaskIn(_Stripped):
    name: str = Field(min_length=1, max_length=100, description="단위업무명")
    description: str = Field(default="", max_length=2000, description="업무 설명(선택)")
    mode: HandoverMode = Field(default="reassignment", description="인계 유형: reassignment=인사발령(6개 장), leave=장기휴가(3개 장)")
    successor: str = Field(default="", max_length=100, description="인수자(선택)")
    handover_date: str = Field(default="", pattern=DATE_PATTERN, description="인계 예정일 YYYY-MM-DD(선택)")


class UnitTasksIn(BaseModel):
    tasks: list[UnitTaskIn] = Field(min_length=1, max_length=30, description="등록할 단위업무 목록")


class UnitTaskUpdate(_Stripped):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    mode: HandoverMode | None = None
    successor: str | None = Field(default=None, max_length=100)
    handover_date: str | None = Field(default=None, pattern=DATE_PATTERN)


class UnitTaskOut(BaseModel):
    id: str
    name: str
    description: str
    sort_order: int
    session_id: str = Field(description="이 단위업무의 인수인계 세션 ID. 질의응답·문서는 /api/sessions/{session_id}/... 를 그대로 쓴다")
    mode: str = Field(description="인계 유형: reassignment(인사발령) | leave(장기휴가)")
    successor: str
    handover_date: str
    stage: str
    status: str
    file_count: int
    document_version: int
    created_at: datetime


class UnitTaskResult(Result):
    task: UnitTaskOut


class UnitTasksResult(Result):
    tasks: list[UnitTaskOut]




class SessionCreate(BaseModel):
    owner_name: str = Field(min_length=1, max_length=50, description="인계자 성명")
    organization: str = Field(default="", max_length=100, description="소속 조직")
    position: str = Field(default="", max_length=50, description="직책")
    duties: str = Field(default="", max_length=2000, description="담당 업무 개요")
    successor: str = Field(default="", max_length=100, description="인수자")
    handover_date: str = Field(default="", max_length=20, description="인계 예정일(YYYY-MM-DD)")
    title: str = Field(default="", max_length=200)
    mode: Literal["reassignment", "leave"] = Field(default="reassignment", description="인수인계 유형: reassignment=인사발령, leave=장기휴가")
    task_name: str = Field(default="", max_length=200, description="단위 업무명(업무 단위별로 인수인계서 1건)")

    @field_validator("owner_name", "organization", "position", "duties", "successor", "handover_date", "title", "task_name")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    name: str
    url: str
    link_type: str
    extension: str
    size: int
    status: str
    error: str
    chunk_count: int
    warnings: list[str]
    added_stage: str
    created_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    kind: str
    content: str
    meta: dict[str, Any]
    created_at: datetime


class Progress(BaseModel):
    message: str
    current: int
    total: int


class SessionSummary(BaseModel):
    id: str
    title: str
    owner_name: str
    organization: str
    position: str
    mode: str = "reassignment"
    task_name: str = ""
    stage: str
    status: str
    document_version: int
    created_at: datetime
    updated_at: datetime


class Usage(BaseModel):
    """Claude API 사용량. cost_usd 는 모델 단가로 계산한 추정치(실제 청구액은 Anthropic Console 에서 확인)."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0


class UsageTotal(Usage):
    """서버 전체(모든 세션) 누적 사용량과 예산."""

    sessions: int = 0
    budget_usd: float = 0.0  # 0 이면 제한 없음
    remaining_usd: float | None = None


class SessionDetail(SessionSummary):
    duties: str
    successor: str
    handover_date: str
    progress: Progress
    error: str
    engine: str
    slots: dict[str, Any]
    gaps: list[dict[str, Any]]
    open_gap_count: int
    question_count: int
    sources: list[SourceOut]
    last_message_id: int
    usage: Usage


class StateOut(BaseModel):
    """폴링용 응답: 세션 상태 + 커서 이후의 새 메시지."""

    session: SessionDetail
    messages: list[MessageOut]
    usage_total: UsageTotal


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class LinksIn(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=20)
    link_type: Literal["auto", "confluence", "nanumi", "web"] = "auto"


class DocumentOut(BaseModel):
    """현재 인수인계서(마지막 버전). 사용자가 직접 고쳤으면 고친 문서가 온다."""

    markdown: str
    version: int
    origin: str = Field(default="ai", description="ai(AI 작성) | user(직접 수정) | restore(이전 버전 복원)")
    origin_label: str = "AI 작성"
    author_name: str = Field(default="", description="직접 수정·복원한 사람(AI 작성이면 빈 값)")
    updated_at: datetime | None = None
    version_count: int = Field(default=0, description="지금까지 저장된 버전 수")


class DocumentSaveIn(BaseModel):
    markdown: str = Field(min_length=1, max_length=500_000, description="화면에서 고친 인수인계서 전체(Markdown)")
    base_version: int = Field(ge=0, description="수정을 시작할 때 불러온 version. 그사이 새 버전이 생겼으면 409")
    note: str = Field(default="", max_length=300, description="수정 메모(선택)")

    @field_validator("markdown")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("빈 문서는 저장할 수 없습니다.")
        return value


class DocumentRestoreIn(BaseModel):
    base_version: int | None = Field(default=None, ge=0, description="복원을 누를 때 보고 있던 현재 version(주면 그사이 바뀌었는지 검사)")
    note: str = Field(default="", max_length=300)


class DocumentVersionSummary(BaseModel):
    version: int
    origin: str = Field(description="ai | user | restore")
    origin_label: str
    ai_version: int = Field(description="AI 가 매긴 초안 번호(origin=ai 일 때, 아니면 0)")
    based_on: int = Field(description="직접 수정·복원의 기준 버전(origin=ai 면 0)")
    note: str
    author_name: str
    char_count: int
    is_current: bool
    created_at: datetime


class DocumentVersionOut(DocumentVersionSummary):
    markdown: str


class DocumentVersionsOut(Result):
    session_id: str
    current_version: int
    versions: list[DocumentVersionSummary] = Field(description="최신 버전부터")


class DocumentSaved(Result):
    saved: bool = Field(description="새 버전을 만들었는지(내용이 같으면 false)")
    document: DocumentVersionOut


class MyDocument(BaseModel):
    session_id: str
    task_id: str | None = Field(description="단위업무로 만든 문서면 단위업무 ID")
    title: str
    task_name: str
    stage: str
    version: int
    origin: str
    origin_label: str
    version_count: int
    updated_at: datetime


class MyDocumentsOut(Result):
    documents: list[MyDocument]


class GanttEdit(BaseModel):
    name: str = Field(min_length=1, max_length=300, description="월별 일정표의 단위 업무명(첫 열)")
    nth: int = Field(default=0, ge=0, le=100, description="이름이 같은 행이 여럿일 때 몇 번째 행인지(0부터)")
    months: list[int] = Field(max_length=12, description="막대가 칠해진 달(1~12)")

    @field_validator("months")
    @classmethod
    def _valid_months(cls, value: list[int]) -> list[int]:
        if any(m < 1 or m > 12 for m in value):
            raise ValueError("월은 1~12 사이여야 합니다.")
        return sorted(set(value))


class Accepted(BaseModel):
    ok: bool = True
    message: MessageOut | None = None


class FileUploadResult(Result):
    task_id: str
    session_id: str
    files: list[SourceOut]
    analysis_scheduled: bool = Field(description="적재가 끝나면 AI 분석(또는 재분석)이 자동으로 시작되는지")


# ---------------------------------------------------------------- 대화 진행 상태 · 기록


class CurrentQuestion(BaseModel):
    message_id: int
    kind: str = Field(description="question(질문) | confirm(정리한 답변 확인)")
    content: str
    gap_id: str = ""
    slot: str = ""
    number: int = 0
    quick_replies: list[str] = []
    asked_at: datetime


class QuestionStats(BaseModel):
    asked: int = Field(description="지금까지 던진 질문 수")
    answered: int = Field(description="답변을 받아 정리한 빈 항목 수(확인 대기 포함)")
    skipped: int
    remaining: int = Field(description="아직 묻지 않은 빈 항목 수")
    total: int = Field(description="분석에서 찾은 빈 항목 수")


class ConversationProgress(BaseModel):
    """사용자가 이 대화를 어디까지 진행했는지."""

    session_id: str
    task_id: str | None = Field(description="단위업무 ID(단위업무로 만든 대화일 때)")
    title: str
    stage: str = Field(description="setup → analyzing → summary → qna → composing → review")
    stage_number: int = Field(description="화면의 STAGE 번호(0=자료 준비, 1~4)")
    stage_label: str
    status: str = Field(description="idle | running(AI 처리 중) | waiting(사용자 입력 대기) | error")
    next_action: str = Field(
        description="upload_files | processing_files | start_analysis | ai_working | answer_question | confirm_answer | review_document | retry"
    )
    next_action_label: str = Field(description="화면에 그대로 띄울 다음 할 일 안내")
    progress_message: str
    current_question: CurrentQuestion | None
    questions: QuestionStats
    file_count: int
    document_version: int
    message_count: int
    last_message: MessageOut | None
    last_read_message_id: int
    unread_count: int = Field(description="마지막으로 읽은 뒤 AI·시스템이 보낸 메시지 수")
    started_at: datetime
    last_activity_at: datetime


class ConversationsResult(Result):
    conversations: list[ConversationProgress]


class MessagesPage(BaseModel):
    messages: list[MessageOut] = Field(description="오래된 것부터 순서대로")
    has_more_before: bool = Field(description="이보다 오래된 메시지가 더 있는지(before=messages[0].id 로 이어서 조회)")


class ConversationResult(Result):
    progress: ConversationProgress
    history: MessagesPage


class ReadIn(BaseModel):
    message_id: int = Field(ge=0, description="여기까지 읽음(이 ID 이하의 메시지)")


# ---------------------------------------------------------------- 근거 원문


class ChunkOut(BaseModel):
    chunk_id: str
    index: int = Field(description="자료 안에서의 순서(0부터)")
    text: str
    citation: str = Field(description="사람이 읽는 출처 표기. 예: 업무정의서.pdf p.3")
    page: int | None = None
    sheet: str = ""
    section: str = ""


class ChunksOut(BaseModel):
    source_id: str
    source_name: str
    chunks: list[ChunkOut]
    total: int = Field(description="이 자료의 전체 조각 수")


class CitationOut(BaseModel):
    number: int = Field(description="인수인계서 본문의 [번호]")
    label: str
    quote: str = Field(description="근거로 쓴 문장 일부")
    kind: Literal["document", "answer"] = Field(description="document=올린 자료, answer=인계자 답변")
    source_id: str
    source_name: str = Field(description="올린 자료 이름(답변이거나 자료가 삭제됐으면 빈 값)")
    chunk_id: str
    text: str = Field(description="근거가 나온 조각 원문(찾지 못하면 quote 와 같음)")
    slot: str = Field(description="이 근거가 처음 쓰인 장(slot 키)")
    item_title: str


class CitationsOut(BaseModel):
    document_version: int
    citations: list[CitationOut]


class ReindexResult(Result):
    source_count: int = Field(description="다시 적재하는 자료 수")


# ---------------------------------------------------------------- 메일 조회(가상 메일 조회 API)


class MailAddressOut(BaseModel):
    name: str
    email: str


class MailAttachmentOut(BaseModel):
    name: str
    size: int
    content_type: str


class MailSummary(BaseModel):
    id: str
    folder: str = Field(description="inbox(받은 메일함) | sent(보낸 메일함)")
    subject: str
    sender: MailAddressOut
    to: list[MailAddressOut]
    sent_at: datetime
    snippet: str = Field(description="본문 앞부분(목록 미리보기)")
    has_attachments: bool
    attachment_count: int
    important: bool
    relevance: int = Field(description="단위업무와의 관련도 점수(task_id·session_id 를 주었을 때, 높을수록 관련)")
    recommended: bool = Field(description="단위업무와 관련 있어 보이는 메일")
    imported: bool = Field(description="이미 이 업무 자료로 가져온 메일")


class MailDetail(MailSummary):
    cc: list[MailAddressOut]
    body: str
    attachments: list[MailAttachmentOut]


class MailListOut(Result):
    provider: str = Field(description="연결된 메일 조회 API(mock = 가상 메일함)")
    mailbox: MailAddressOut
    messages: list[MailSummary]
    total: int
    page: int
    size: int
    has_next: bool
    keywords: list[str] = Field(description="관련도 정렬에 쓴 단위업무 낱말")


class MailImportIn(BaseModel):
    message_ids: list[str] = Field(min_length=1, max_length=20, description="가져올 메일 ID(목록의 id)")


class MailImportResult(Result):
    session_id: str
    task_id: str | None = None
    files: list[SourceOut] = Field(description="새로 등록한 자료(.eml)")
    skipped: list[str] = Field(description="이미 가져와서 건너뛴 메일 제목")
    analysis_scheduled: bool
