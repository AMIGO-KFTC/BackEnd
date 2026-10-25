"""가상 응답 모드에서 AI 가 '분석해 낸 것처럼' 돌려줄 예시 시나리오(기관 홈페이지 운영 업무).

인수인계서 양식(장·필드 키)은 AI 저장소의 amigo_agent.template.SLOTS(노션 '인수인계서 형식' 템플릿)를 그대로 따른다.
값이 비어 있는 필드는 자료에 없는 내용이라 GAPS 의 질문으로 이어진다.
모든 이름·연락처는 예시 값이다.
"""

from __future__ import annotations

# 항목의 근거로 붙일 자료 종류. 업로드한 파일 중 확장자가 맞는 것을 고르고, 없으면 아무 파일이나 돌려 쓴다.
DOC_HINTS = {
    "definition": ((".pdf",), "p.1"),
    "manual": ((".hwpx", ".hwp", ".docx"), ""),
    "minutes": ((".docx", ".hwpx", ".pdf"), ""),
    "accounts": ((".xlsx", ".csv"), "[계정 목록]"),
    "mail": ((".eml", ".msg"), ""),
    "plan": ((".pptx", ".pdf"), "슬라이드 3"),
}

ITEMS: dict[str, list[dict]] = {
    # [인사발령] A. 업무 단위 요약 + 일정 명세표
    "overview": [
        {"doc": "definition", "fields": {"name": "{task_name}", "period": "상시(연중)", "note": "정상 운영 중(공지·보도자료 게시와 메뉴 관리)"}},
        {"doc": "manual", "fields": {"name": "웹 접근성 품질 관리", "period": "분기별(1·4·7·10월) 점검, 11월 인증 갱신", "note": "3분기 자체 점검 완료, 11월 인증 갱신 준비 중"}},
        {"doc": "minutes", "fields": {"name": "홈페이지 리뉴얼 추진", "period": "2026.10 ~ 2027.01", "note": "RFP 초안 작성 중, 11월 이사회 예산 승인 대기"}},
        {"doc": "mail", "fields": {"name": "개인정보처리방침 개정 반영", "period": "2026.10", "note": "개정안 검토 완료, 10월 15일 게시 예정"}},
    ],
    # [인사발령] C 안의 '추진 경과'(프로젝트 카드 안에 시기 | 주요 추진 내용 | 비고 표로 들어간다. 상태는 내부 값)
    "milestones": [
        {"doc": "minutes", "fields": {"when": "2026.08", "content": "리뉴얼 기본계획 수립", "note": "", "status": "완료", "project": "홈페이지 리뉴얼"}},
        {"doc": "minutes", "fields": {"when": "2026.09~10", "content": "제안요청서(RFP) 작성", "note": "초안 80%, 요구사항 정의서 보완 중", "status": "진행", "project": "홈페이지 리뉴얼"}},
        {"doc": "minutes", "fields": {"when": "2026.11", "content": "입찰 공고·업체 선정", "note": "이사회 예산 승인 후", "status": "계획", "project": "홈페이지 리뉴얼"}},
        {"doc": "mail", "fields": {"when": "2026.10.10", "content": "법무팀 개정안 검토", "note": "", "status": "진행", "project": "개인정보처리방침 개정 반영"}},
        {"doc": "mail", "fields": {"when": "2026.10.15", "content": "개정 방침 게시", "note": "게시 기한", "status": "계획", "project": "개인정보처리방침 개정 반영"}},
    ],
    # [인사발령] C 안의 '주요 협업관계'(정기업무와 같은 형식: 외부 소속 | 이름 | 주요역할 | 연락처 | 비고, 내부 소속 | 이름 | 주요역할 | 비고)
    "stakeholders": [
        {"doc": "mail", "fields": {"org": "재무팀", "name": "박지훈 차장", "role": "리뉴얼 예산(3억 원) 협의, 11월 이사회 승인 후 계약 일정 협의", "phone": "", "note": "", "scope": "내부", "project": "홈페이지 리뉴얼"}},
        {"doc": "manual", "fields": {"org": "IT지원팀", "name": "최영호 과장", "role": "서버·CMS 계정 발급과 권한 승인", "phone": "", "note": "신규 서버 구성 시 계정 재발급 필요", "scope": "내부", "project": "홈페이지 리뉴얼"}},
        {"doc": "minutes", "fields": {"org": "(주)한빛웹솔루션", "name": "정다은 PM", "role": "장애 대응 시간 개선 요청 중, 리뉴얼 입찰 참여 여부 확인", "phone": "02-555-0182", "note": "", "scope": "외부", "project": "홈페이지 리뉴얼"}},
        {"doc": "definition", "fields": {"org": "정보보호팀", "name": "이수진 대리", "role": "개정 방침 게시 전 보안 점검", "phone": "", "note": "", "scope": "내부", "project": "개인정보처리방침 개정 반영"}},
        {"doc": "mail", "fields": {"org": "법무팀", "name": "법무팀", "role": "개정 방침 문구 검토", "phone": "", "note": "검토 결과 회신 후 게시", "scope": "내부", "project": "개인정보처리방침 개정 반영"}},
    ],
    # [인사발령] B. 정기업무
    "regular": [
        {
            "doc": "definition",
            "fields": {
                "name": "유지보수 실적 검수 및 대금 지급 요청",
                "overview": "(주)한빛웹솔루션의 월간 유지보수 실적을 검수하고 재무팀에 대금 지급을 요청합니다.",
                "timing": "매월 5영업일 이내",
                "procedure": "업체 실적보고서 접수 → 장애·요청 처리 건 대조 → 검수 확인서 결재 → 재무팀 지급 요청",
                "key_points": "",
                "decisions": "2026년 7월부터 장애 대응 시간을 검수 항목에 넣었습니다(계약 기준 1시간).",
                "future": "",
                "references": "계약서 별첨 SLA 표를 먼저 읽어 두면 검수가 빠릅니다.",
            },
        },
        {
            "doc": "manual",
            "fields": {
                "name": "웹 접근성 자체 점검",
                "overview": "전 페이지를 점검해 오류를 업체에 수정 요청합니다.",
                "timing": "분기별(1·4·7·10월)",
                "procedure": "자동 점검 도구로 전 페이지 점검 → 오류 목록 정리 → 업체 수정 요청 → 재점검",
                "key_points": "대체텍스트 누락과 키보드 접근 오류는 인증 심사에서 바로 감점됩니다.",
                "decisions": "",
                "future": "자동 점검 결과를 매 분기 비교해 추이를 남겨 주세요.",
                "references": "",
            },
        },
        {
            "doc": "accounts",
            "fields": {
                "name": "SSL 인증서 갱신",
                "overview": "홈페이지 보안 인증서를 만료 전에 갱신합니다.",
                "timing": "매년 12월 초(현재 인증서 만료 2026-12-15)",
                "procedure": "총무팀에 법인카드 결제 요청 → 인증서 발급 → 업체에 서버 적용 요청 → 만료일 확인",
                "key_points": "만료 2주 전까지 시작하지 않으면 방문자 브라우저에 보안 경고가 뜹니다.",
                "decisions": "2025년부터 보안 정책에 따라 1년 단위로 갱신합니다.",
                "future": "",
                "references": "",
            },
        },
        {
            "doc": "manual",
            "fields": {
                "name": "주간 게시물 현황 점검",
                "overview": "게시판별 미승인 게시물과 게시 기간이 지난 공지를 정리합니다.",
                "timing": "매주 월요일 오전",
                "procedure": "게시판별 미승인 게시물 확인 → 게시 기간 지난 공지 내리기 → 결과를 팀 메신저에 공유",
                "key_points": "부서장 승인이 없는 게시물은 게시할 수 없습니다.",
                "decisions": "공지 게시 기간 기본값을 30일로 정했습니다.",
                "future": "",
                "references": "",
            },
        },
    ],
    # [인사발령] B 안의 '주요 협업관계'(정기업무 카드 안에 소속 | 이름 | 주요역할 | 연락처 | 비고(유의사항) 표로 들어간다)
    "partners": [
        {"doc": "mail", "fields": {"org": "재무팀", "name": "박지훈 차장", "role": "유지보수 대금 지급 처리", "phone": "내선 2345", "note": "지급 요청 마감은 매월 5영업일", "scope": "내부", "task": "유지보수 실적 검수 및 대금 지급 요청"}},
        {"doc": "definition", "fields": {"org": "(주)한빛웹솔루션", "name": "정다은 PM", "role": "월간 실적보고서 제출", "phone": "02-555-0182", "note": "", "scope": "외부", "task": "유지보수 실적 검수 및 대금 지급 요청"}},
        {"doc": "accounts", "fields": {"org": "총무팀", "name": "한소영 주임", "role": "인증서 비용 법인카드 결제", "phone": "내선 1120", "note": "결제 요청은 만료 3주 전까지", "scope": "내부", "task": "SSL 인증서 갱신"}},
    ],
    # [인사발령] C. 비정기 업무(프로젝트성)
    "irregular": [
        {
            "doc": "minutes",
            "fields": {
                "name": "홈페이지 리뉴얼",
                "overview": "2019년에 구축한 홈페이지가 노후화되고 모바일 방문이 전체의 62%로 늘어 2027년 1월 반응형 새 홈페이지 오픈을 목표로 추진함.",
                "decisions": "기존 CMS 를 유지하고 화면만 개편하기로 결정함(데이터 이관 위험 감소).",
                "key_points": "이사회 승인 전에는 계약을 체결할 수 없음.",
            },
        },
        {
            "doc": "mail",
            "fields": {
                "name": "개인정보처리방침 개정 반영",
                "overview": "개인정보 보호법 시행령 개정에 따라 10월 15일까지 개정 방침을 게시함.",
                "decisions": "",
                "key_points": "게시 기한을 넘기면 과태료 대상이 될 수 있음.",
            },
        },
    ],
    # [인사발령 D / 장기휴가 C] 기타 자료 — 시스템 접근 권한
    "systems": [
        {"doc": "accounts", "fields": {"name": "CMS(콘텐츠관리시스템)", "purpose": "게시물 등록·승인, 메뉴 관리", "access": "관리자", "how_to_get": "전자결재 'IT 권한신청서' 제출 → IT지원팀 승인"}},
        {"doc": "accounts", "fields": {"name": "Google Analytics", "purpose": "방문 통계 분석", "access": "편집자", "how_to_get": "기존 담당자가 계정 권한을 직접 이관"}},
        {"doc": "manual", "fields": {"name": "웹방화벽 관리 콘솔", "purpose": "차단 정책 확인", "access": "조회", "how_to_get": "정보보호팀에 메일로 요청"}},
        {"doc": "accounts", "fields": {"name": "도메인·SSL 인증서 관리", "purpose": "인증서 갱신, 도메인 연장", "access": "관리자", "how_to_get": ""}},
    ],
    # [인사발령] D. 기타 자료 — 부서별 추가 자료
    "dept_notes": [],
    # [장기휴가] A. 세부 업무
    "leave_tasks": [
        {
            "doc": "definition",
            "fields": {
                "name": "유지보수 실적 검수 및 대금 지급 요청",
                "task_type": "정기",
                "schedule": "매월 5영업일 이내(부재 기간 중 11월 지급 건 1회)",
                "method": "업체 실적보고서 접수 → 장애·요청 처리 건 대조 → 검수 확인서 결재 → 재무팀 지급 요청",
                "cautions": "",
                "references": "계약서 별첨 SLA 표",
            },
        },
        {
            "doc": "manual",
            "fields": {
                "name": "주간 게시물 현황 점검",
                "task_type": "정기",
                "schedule": "매주 월요일 오전",
                "method": "게시판별 미승인 게시물 확인 → 게시 기간 지난 공지 내리기 → 결과를 팀 메신저에 공유",
                "cautions": "부서장 승인이 없는 게시물은 게시하지 않음. 게시 오류가 나면 (주)한빛웹솔루션 정다은 PM에게 문의(당일 조치)",
                "references": "홈페이지 운영 매뉴얼 v3.2",
            },
        },
    ],
    # [장기휴가] B. 주요 연락망
    "contacts": [
        {"doc": "mail", "fields": {"org": "(주)한빛웹솔루션", "name": "정다은 PM", "role": "게시 오류·장애 대응", "phone": "02-555-0182", "note": "당일 조치 요청", "scope": "외부"}},
        {"doc": "mail", "fields": {"org": "재무팀", "name": "박지훈 차장", "role": "유지보수 대금 지급", "phone": "", "note": "", "scope": "내부"}},
    ],
}

# 자료에 없어 인계자에게 물어볼 내용(양식 순서: 장 → 세부업무 → 표 행). item 이 None 이면 답변으로 새 항목을 만든다.
# 질문 종류(노션 10.25): gate=예/아니요로 먼저 묻기(ask: 예면 follow 로 이어서 묻고 아니요면 '해당 없음' / confirm: 아니요면 follow),
# aux=보조질문, picker=주기 선택·달력 기간 선택.
GAPS: list[dict] = [
    {"slot": "regular", "item": "유지보수 실적 검수 및 대금 지급 요청", "field": "key_points", "priority": 1, "gate": "ask",
     "description": "'유지보수 실적 검수 및 대금 지급 요청'의 중점 관리사항 정보 없음",
     "question": "'유지보수 실적 검수 및 대금 지급 요청' — 첨부자료에서는 특별한 관리사항이 확인되지 않았습니다. 이 업무를 수행하면서 반복적으로 발생하는 문제나 후임자가 특히 주의해야 할 사항이 있나요?",
     "follow": "'유지보수 실적 검수 및 대금 지급 요청'의 중점 관리사항에 대해 여쭤볼게요. 업무의 성패를 좌우하는 요소는 무엇이며, 어떤 기준이나 지표를 중점적으로 확인해야 하나요?"},
    {"slot": "partners", "item": "정다은 PM", "field": "note", "priority": 2, "gate": "ask",
     "description": "'유지보수 실적 검수 및 대금 지급 요청'의 주요 협업관계 유의사항 확인",
     "question": "'유지보수 실적 검수 및 대금 지급 요청' 업무를 하면서 협업할 때 유의해야 할 사항이 있는 사람(부서)이 있나요?",
     "follow": "(주)한빛웹솔루션 정다은 PM과 실적 검수를 할 때 어떤 점을 유의해야 하나요?"},
    {"slot": "regular", "item": "유지보수 실적 검수 및 대금 지급 요청", "field": "future", "priority": 2, "gate": "ask", "aux": True,
     "description": "'유지보수 실적 검수 및 대금 지급 요청'의 향후 과제 및 발전 방향 정보 없음",
     "question": "'유지보수 실적 검수 및 대금 지급 요청' — 이 업무의 향후 과제나 발전 방향이 있나요?",
     "follow": "'유지보수 실적 검수 및 대금 지급 요청'의 향후 과제 및 발전 방향에 대해 여쭤볼게요. 현재 진행 중이거나 해결되지 않은 과제는 무엇이며, 앞으로 어떤 방향으로 개선하면 좋을까요?"},
    {"slot": "regular", "item": "웹 접근성 자체 점검", "field": "decisions", "priority": 2, "gate": "ask",
     "description": "'웹 접근성 자체 점검'의 주요 의사결정 및 판단 근거 정보 없음",
     "question": "'웹 접근성 자체 점검' — 이 업무를 수행하면서 주요하게 결정한 사항이 있나요?",
     "follow": "'웹 접근성 자체 점검'의 주요 의사결정 및 판단 근거에 대해 여쭤볼게요. 과거에 중요하게 결정한 사항은 무엇이며, 어떤 이유와 기준으로 결정했나요?"},
    {"slot": "regular", "item": "웹 접근성 자체 점검", "field": "references", "priority": 3, "aux": True,
     "description": "'웹 접근성 자체 점검'의 기타(참고자료 등) 정보 없음",
     "question": "'웹 접근성 자체 점검'의 기타(참고자료 등)에 대해 여쭤볼게요. 보면 좋은 법령이나 참고자료가 있나요?"},
    {"slot": "regular", "item": "주간 게시물 현황 점검", "field": "timing", "priority": 2, "picker": "period",
     "description": "'주간 게시물 현황 점검'의 수행 시기 확인",
     "question": "'주간 게시물 현황 점검'의 수행 시기를 알려 주세요. 이 업무는 얼마나 자주 하나요? 정기적으로 하지 않는 업무라면 달력에서 수행 기간을 골라 주세요."},
    {"slot": "milestones", "item": "제안요청서(RFP) 작성", "field": "note", "priority": 1, "gate": "confirm",
     "description": "'홈페이지 리뉴얼'의 현재 진행 단계 확인",
     "question": "자료상으로는 '홈페이지 리뉴얼' 프로젝트가 현재 '제안요청서(RFP) 작성(2026.09~10)' 단계로 보입니다. 현재 진행 상태가 맞나요?",
     "follow": "'홈페이지 리뉴얼' 프로젝트의 실제 현재 진행 상황을 알려 주시겠어요? 지금 어느 단계이고 다음에 무엇을 하나요?"},
    {"slot": "stakeholders", "item": "정다은 PM", "field": "note", "priority": 1, "gate": "ask",
     "description": "'홈페이지 리뉴얼'의 주요 협업관계 유의사항 확인",
     "question": "'홈페이지 리뉴얼' 프로젝트를 하면서 협업할 때 유의해야 할 사항이 있는 사람(부서)이 있나요?",
     "follow": "'홈페이지 리뉴얼' 프로젝트에서 누구(소속·이름)와 협업할 때 어떤 점을 유의해야 하나요?"},
    {"slot": "irregular", "item": "개인정보처리방침 개정 반영", "field": "decisions", "priority": 2, "gate": "ask",
     "description": "'개인정보처리방침 개정 반영'의 주요 의사결정 및 판단 근거 정보 없음",
     "question": "'개인정보처리방침 개정 반영' — 이 프로젝트를 추진하면서 주요하게 결정한 사항이 있나요?",
     "follow": "'개인정보처리방침 개정 반영'의 주요 의사결정 및 판단 근거에 대해 여쭤볼게요. 프로젝트 추진 과정에서 중요한 의사결정은 무엇이었으며, 어떤 이유와 기준으로 결정했나요?"},
    {"slot": "leave_tasks", "item": "유지보수 실적 검수 및 대금 지급 요청", "field": "cautions", "priority": 2, "aux": True,
     "description": "'유지보수 실적 검수 및 대금 지급 요청'의 주의사항 및 후속조치 정보 없음",
     "question": "'유지보수 실적 검수 및 대금 지급 요청'의 주의사항 및 후속조치에 대해 여쭤볼게요. 인수자가 업무를 처리할 때 알고 있으면 좋을 사항이 무엇이 있나요?"},
]

# 데모 계정에 미리 넣어 두는 답변(시드 데이터)
DEMO_ANSWERS = [
    "정보보호팀과는 웹방화벽 차단 정책 변경을 협의했고, 연락처는 내선 2345 입니다.",
    "장애 처리 건수와 계약 SLA(1시간) 초과 건을 꼭 대조합니다. 초과 건이 있으면 지체상금을 반영해야 해요.",
]
