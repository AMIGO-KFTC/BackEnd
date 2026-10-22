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
        {"doc": "definition", "fields": {"name": "{task_name}", "period": "상시(연중)", "note": "공지·보도자료 게시와 승인, 메뉴 구성 관리"}},
        {"doc": "manual", "fields": {"name": "웹 접근성 품질 관리", "period": "분기별(1·4·7·10월) 점검, 11월 인증 갱신", "note": "자체 점검 후 업체에 수정 요청"}},
        {"doc": "minutes", "fields": {"name": "홈페이지 리뉴얼 추진", "period": "2026.10 ~ 2027.01", "note": "11월 이사회 예산 승인 필요"}},
        {"doc": "mail", "fields": {"name": "개인정보처리방침 개정 반영", "period": "2026.10", "note": "10월 15일까지 게시"}},
    ],
    # [인사발령] C 안의 '주요 협업관계 및 협의사항'(프로젝트 카드 안에 들어간다). 협의 상대는 팀 또는 개인
    "stakeholders": [
        {"doc": "mail", "fields": {"party": "재무팀 박지훈 차장", "project": "홈페이지 리뉴얼", "discussed": "리뉴얼 예산(3억 원) 협의", "upcoming": "11월 이사회 승인 후 계약 일정 협의"}},
        {"doc": "definition", "fields": {"party": "정보보호팀 이수진 대리", "project": "개인정보처리방침 개정 반영", "discussed": "", "upcoming": "개정 방침 게시 전 보안 점검"}},
        {"doc": "manual", "fields": {"party": "IT지원팀 최영호 과장", "project": "홈페이지 리뉴얼", "discussed": "서버·CMS 계정 발급과 권한 승인", "upcoming": "신규 서버 구성 시 계정 재발급 협의"}},
        {"doc": "minutes", "fields": {"party": "(주)한빛웹솔루션 정다은 PM", "project": "홈페이지 리뉴얼", "discussed": "장애 대응 시간(평균 2시간 → 1시간 이내) 개선 요청 중", "upcoming": "리뉴얼 입찰 참여 여부 확인"}},
        {"doc": "mail", "fields": {"party": "법무팀", "project": "개인정보처리방침 개정 반영", "discussed": "개정 방침 문구 검토", "upcoming": "검토 결과 회신 후 게시"}},
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
                "collaboration": "재무팀과 지급 요청 마감을 매월 5영업일로 합의",
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
                "collaboration": "",
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
                "collaboration": "총무팀과 결제 일정 협의",
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
                "collaboration": "",
                "future": "",
                "references": "",
            },
        },
    ],
    # [인사발령] C. 비정기 업무(프로젝트성)
    "irregular": [
        {
            "doc": "minutes",
            "fields": {
                "name": "홈페이지 리뉴얼",
                "overview": "2019년에 구축한 홈페이지가 노후화되었고 모바일 방문이 전체의 62%로 늘었습니다.",
                "goals": "2027년 1월 반응형 새 홈페이지 오픈",
                "progress": "제안요청서(RFP) 초안 80% 작성, 요구사항 정의서 보완 중 (일정: 10월 입찰 공고 → 11월 업체 선정 → 12월 착수)",
                "decisions": "기존 CMS 를 유지하고 화면만 개편합니다(데이터 이관 위험 감소).",
                "key_points": "이사회 승인 전에는 계약을 체결할 수 없습니다.",
                "future": "",
            },
        },
        {
            "doc": "mail",
            "fields": {
                "name": "개인정보처리방침 개정 반영",
                "overview": "개인정보 보호법 시행령 개정",
                "goals": "10월 15일까지 개정 방침 게시",
                "progress": "법무팀 검토 중 (10/10 검토 완료 → 10/15 게시)",
                "decisions": "",
                "key_points": "게시 기한을 넘기면 과태료 대상이 될 수 있습니다.",
                "future": "법무팀 검토 결과 회신 대기 후 게시",
            },
        },
    ],
    # [인사발령 D / 장기휴가 C] 시스템 접근 권한
    "systems": [
        {"doc": "accounts", "fields": {"name": "CMS(콘텐츠관리시스템)", "purpose": "게시물 등록·승인, 메뉴 관리", "access": "관리자", "how_to_get": "전자결재 'IT 권한신청서' 제출 → IT지원팀 승인"}},
        {"doc": "accounts", "fields": {"name": "Google Analytics", "purpose": "방문 통계 분석", "access": "편집자", "how_to_get": "기존 담당자가 계정 권한을 직접 이관"}},
        {"doc": "manual", "fields": {"name": "웹방화벽 관리 콘솔", "purpose": "차단 정책 확인", "access": "조회", "how_to_get": "정보보호팀에 메일로 요청"}},
        {"doc": "accounts", "fields": {"name": "도메인·SSL 인증서 관리", "purpose": "인증서 갱신, 도메인 연장", "access": "관리자", "how_to_get": ""}},
    ],
    # [인사발령] E. 부서별 추가 자료
    "dept_notes": [],
    # [장기휴가] A. 부재 중 처리할 단위 업무
    "leave_units": [
        {"doc": "definition", "fields": {"name": "{task_name}", "period": "휴가 기간 중 상시(주 1회 점검)", "note": ""}},
        {"doc": "definition", "fields": {"name": "유지보수 대금 지급 요청", "period": "매월 5영업일 이내", "note": "재무팀 지급 마감 준수"}},
    ],
    # [장기휴가] B. 세부 업무
    "leave_tasks": [
        {
            "doc": "definition",
            "fields": {
                "name": "유지보수 실적 검수 및 대금 지급 요청",
                "overview": "(주)한빛웹솔루션의 월간 유지보수 실적을 검수하고 재무팀에 대금 지급을 요청합니다.",
                "schedule": "매월 5영업일 이내(부재 기간 중 11월 지급 건 1회)",
                "method": "업체 실적보고서 접수 → 장애·요청 처리 건 대조 → 검수 확인서 결재 → 재무팀 지급 요청",
                "key_points": "장애 대응 시간이 계약 기준(1시간)을 넘은 건은 지체상금을 반영해야 합니다.",
                "emergency": "",
                "pending": "11월 실적보고서 접수 후 검수 필요",
                "references": "계약서 별첨 SLA 표",
            },
        },
        {
            "doc": "manual",
            "fields": {
                "name": "주간 게시물 현황 점검",
                "overview": "게시판별 미승인 게시물과 게시 기간이 지난 공지를 정리합니다.",
                "schedule": "매주 월요일 오전",
                "method": "게시판별 미승인 게시물 확인 → 게시 기간 지난 공지 내리기 → 결과를 팀 메신저에 공유",
                "key_points": "부서장 승인이 없는 게시물은 게시할 수 없습니다.",
                "emergency": "게시 오류가 나면 (주)한빛웹솔루션 정다은 PM(02-555-0182)에게 문의하면 당일 조치됩니다.",
                "pending": "",
                "references": "홈페이지 운영 매뉴얼 v3.2",
            },
        },
    ],
}

# 자료에 없어 인계자에게 물어볼 내용(이 순서대로 묻는다). item 이 None 이면 답변으로 새 항목을 만든다.
GAPS: list[dict] = [
    {"slot": "stakeholders", "item": "정보보호팀 이수진 대리", "field": "discussed", "priority": 1,
     "description": "'정보보호팀 이수진 대리'와 협의한 내용 정보 없음",
     "question": "자료에 있는 '정보보호팀 이수진 대리'와는 어떤 내용을 협의하셨나요? 협의한 결과도 알려 주세요."},
    {"slot": "leave_tasks", "item": "유지보수 실적 검수 및 대금 지급 요청", "field": "emergency", "priority": 1,
     "description": "'유지보수 실적 검수 및 대금 지급 요청'의 비상 대응 및 협조사항 정보 없음",
     "question": "휴가 중 '유지보수 실적 검수 및 대금 지급 요청'에서 문제가 생기면 어떻게 대응하고 누구에게 문의하면 되나요?"},
    {"slot": "regular", "item": "유지보수 실적 검수 및 대금 지급 요청", "field": "key_points", "priority": 1,
     "description": "'유지보수 실적 검수 및 대금 지급 요청'의 중점관리사항 정보 없음",
     "question": "'유지보수 실적 검수 및 대금 지급 요청'을 할 때 놓치면 문제가 되는 점이 있나요? 검수에서 꼭 확인하시는 항목을 알려 주세요."},
    {"slot": "irregular", "item": "홈페이지 리뉴얼", "field": "future", "priority": 1,
     "description": "'홈페이지 리뉴얼'의 향후 추진과제 및 발전 방향 정보 없음",
     "question": "'홈페이지 리뉴얼' 사업에서 아직 결정되지 않았거나 인수자가 이어서 추진해야 할 일은 무엇인가요?"},
    {"slot": "systems", "item": "도메인·SSL 인증서 관리", "field": "how_to_get", "priority": 2,
     "description": "'도메인·SSL 인증서 관리'의 신청·이관 방법 정보 없음",
     "question": "'도메인·SSL 인증서 관리' 권한은 인수자가 어떻게 받으면 되나요? (ID/PW 는 적지 마세요)"},
    {"slot": "regular", "item": "웹 접근성 자체 점검", "field": "decisions", "priority": 2,
     "description": "'웹 접근성 자체 점검'의 주요 의사결정 및 판단 근거 정보 없음",
     "question": "지난 '웹 접근성 자체 점검'에서 내리신 주요 결정이 있다면 알려 주시겠어요? 예를 들어 점검 범위를 바꾼 이유요."},
    {"slot": "dept_notes", "item": None, "field": "detail", "priority": 3, "new_title": "부서 추가 자료",
     "description": "부서별 추가 자료 정보 없음",
     "question": "소속 부서만의 추가 관리 사항(외주 관리, 라이선스 등)이 있나요? 없으면 건너뛰어 주세요."},
]

# 데모 계정에 미리 넣어 두는 답변(시드 데이터)
DEMO_ANSWERS = [
    "정보보호팀과는 웹방화벽 차단 정책 변경을 협의했고, 연락처는 내선 2345 입니다.",
    "장애 처리 건수와 계약 SLA(1시간) 초과 건을 꼭 대조합니다. 초과 건이 있으면 지체상금을 반영해야 해요.",
]
