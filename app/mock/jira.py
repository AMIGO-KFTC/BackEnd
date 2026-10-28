"""가상 Jira 데이터: 실제 Jira API 대신 프로젝트·스프린트·에픽·이슈 예시로, 지금 업무(프로젝트)가 어디까지 왔는지를 보여 준다.

인수인계 자료로 가져올 수 있는 것
  - 이슈(스토리·작업·버그): 설명·상태·담당자·댓글·하위 작업
  - 에픽: 에픽 설명 + 하위 이슈 목록과 진행률(완료 스토리 포인트 / 전체)
  - 프로젝트 진행 현황(id 'project:<키>'): 현재 스프린트 진척, 에픽별 진행률, 담당자별 남은 일, 막힌 이슈
실제 Jira 를 연동할 때는 search_issues / get_issue / list_projects 를 REST API(/rest/api/3/search?jql=..., /rest/agile/1.0/board/...) 호출로 바꾸면 된다.
모든 이름·날짜·수치는 예시 값이다.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

# 가상 Jira 사이트(app/routers/mock_jira.py, /jira)로 연결된다. 다른 주소에서 띄울 때는 AMIGO_MOCK_JIRA_URL 로 바꾼다.
BASE_URL = os.environ.get("AMIGO_MOCK_JIRA_URL", "http://localhost:8000/jira").rstrip("/")
TODAY = date(2026, 10, 25)  # 예시 데이터의 기준일(스프린트 남은 날 계산)

STATUSES = ["할 일", "진행 중", "검토 중", "완료"]
STATUS_CATEGORY = {"할 일": "todo", "진행 중": "progress", "검토 중": "progress", "완료": "done"}
TYPES = ["에픽", "스토리", "작업", "버그", "하위 작업"]
PRIORITIES = ["가장 높음", "높음", "보통", "낮음"]


@dataclass
class Sprint:
    name: str
    start: str
    end: str
    goal: str
    state: str = "진행 중"  # 진행 중 | 완료 | 예정


@dataclass
class Project:
    key: str
    name: str
    lead: str
    team: str
    description: str
    sprints: list[Sprint] = field(default_factory=list)

    @property
    def active_sprint(self) -> Sprint | None:
        return next((s for s in self.sprints if s.state == "진행 중"), None)


@dataclass
class Issue:
    key: str
    type: str
    summary: str
    status: str
    priority: str = "보통"
    assignee: str = ""
    reporter: str = ""
    epic: str = ""  # 에픽 키
    parent: str = ""  # 하위 작업의 상위 이슈 키
    sprint: str = ""
    points: int = 0
    due: str = ""
    labels: list[str] = field(default_factory=list)
    created: str = ""
    updated: str = ""
    description: str = ""
    comments: list[tuple[str, str, str]] = field(default_factory=list)  # (작성자, 날짜, 내용)
    blocked: str = ""  # 막힌 이유(있으면 '막힘' 표시)

    @property
    def project(self) -> str:
        return self.key.split("-", 1)[0]

    @property
    def url(self) -> str:
        return f"{BASE_URL}/browse/{self.key}"

    @property
    def category(self) -> str:
        return STATUS_CATEGORY.get(self.status, "todo")


PROJECTS: list[Project] = [
    Project(
        "AMG", "AMIGO 인수인계 서비스", "김미고", "디지털본부 IT아미고팀",
        "AI 가 업무 자료를 읽고 인수인계서를 만들어 주는 사내 서비스. 2026년 12월 전사 확대 오픈이 목표.",
        [
            Sprint("AMG 스프린트 13", "2026-10-06", "2026-10-17", "팀장 검토 화면·전송 기능 마무리", "완료"),
            Sprint("AMG 스프린트 14", "2026-10-20", "2026-10-31", "외부 연동(메일·컨플루언스·지라) 1차 오픈, 문서 품질 회귀 검증"),
            Sprint("AMG 스프린트 15", "2026-11-03", "2026-11-14", "전사 확대 오픈 준비(부하 테스트·운영 매뉴얼)", "예정"),
        ],
    ),
    Project(
        "INFRA", "IT 인프라 운영", "김미고", "디지털본부 IT아미고팀",
        "서버·네트워크·백업·VPN 등 사내 인프라 운영 과제와 장애·변경 작업 관리.",
        [Sprint("INFRA 2026-10월", "2026-10-01", "2026-10-31", "VPN 펌웨어 검증, 노후 스위치 교체 준비, 하반기 보안 점검 조치")],
    ),
    Project(
        "AIDEV", "AI 플랫폼", "최고미", "디지털본부 AI개발팀",
        "임베딩·평가 파이프라인·GPU 자원 등 사내 AI 플랫폼 개발과 운영.",
        [Sprint("AIDEV 스프린트 21", "2026-10-13", "2026-10-31", "임베딩 모델 교체 검증, 평가 데이터셋 확장")],
    ),
    Project(
        "WEB", "기관 홈페이지 리뉴얼", "김민수", "디지털전략부 웹서비스팀",
        "기관 홈페이지 전면 개편(2026.10 ~ 2027.01). 웹 접근성 인증 갱신과 개인정보처리방침 개정 반영 포함.",
        [Sprint("WEB 2026-10월", "2026-10-01", "2026-10-31", "RFP 확정, 접근성 인증 갱신 준비")],
    ),
]


def _i(key, type_, summary, status, *, prio="보통", who="", by="", epic="", parent="", sprint="", pts=0, due="", labels=(), created="", updated="", desc="", comments=(), blocked=""):
    return Issue(key, type_, summary, status, prio, who, by, epic, parent, sprint, pts, due, list(labels), created, updated, desc, list(comments), blocked)


S14, S15 = "AMG 스프린트 14", "AMG 스프린트 15"
INF, AID, WEBS = "INFRA 2026-10월", "AIDEV 스프린트 21", "WEB 2026-10월"

ISSUES: list[Issue] = [
    # ================================================================ AMG — AMIGO 인수인계 서비스
    _i("AMG-1", "에픽", "외부 연동(메일·컨플루언스·지라) 1차 오픈", "진행 중", prio="높음", who="최환", by="김미고", due="2026-10-31", labels=["연동"], created="2026-09-15", updated="2026-10-24",
       desc="인수인계 자료로 메일·컨플루언스·지라를 가져올 수 있게 한다. 1차는 가상 데이터로 화면 흐름을 완성하고, 2차(11월)에 실제 API 를 붙인다.\n\n완료 기준: 세 연동 모두 고르기 → 등록 → 근거 표시까지 동작"),
    _i("AMG-2", "에픽", "인수인계서 생성 품질 개선", "진행 중", prio="높음", who="김민준", by="김미고", due="2026-11-14", labels=["품질", "AI"], created="2026-09-01", updated="2026-10-23",
       desc="부서별 특이사항 반영, 근거 번호 정확도, 빈 칸 질문 우선순위를 개선한다. 평가 점수 85점 → 90점 목표."),
    _i("AMG-3", "에픽", "팀장 검토·전송 기능", "완료", prio="보통", who="김민준", by="김미고", due="2026-10-17", labels=["관리자"], created="2026-08-25", updated="2026-10-17",
       desc="팀장이 팀원 인수인계서를 검토하고, 인계자가 인수자에게 문서를 전송하는 기능."),
    _i("AMG-4", "에픽", "전사 확대 오픈 준비", "할 일", prio="가장 높음", who="김미고", by="김미고", due="2026-12-01", labels=["오픈"], created="2026-10-10", updated="2026-10-22",
       desc="12월 전사 확대 오픈을 위한 부하 테스트, 운영 매뉴얼, 사용자 교육, 장애 대응 체계 정비."),
    _i("AMG-11", "스토리", "메일함에서 업무 메일 검색·선택해 자료로 등록", "완료", who="최환", by="김미고", epic="AMG-1", sprint=S14, pts=5, created="2026-10-06", updated="2026-10-21",
       desc="제목·관련 사람 이름으로 메일을 찾아 등록 예정에 담고, 분석 시작 때 함께 등록한다. 메일 캡처(PNG) 올리기 포함.",
       comments=[("김미고", "2026-10-21", "시연 확인했습니다. 캡처 기능 반응 좋아요.")]),
    _i("AMG-12", "스토리", "컨플루언스 페이지 검색·선택 화면", "완료", who="최환", by="김미고", epic="AMG-1", sprint=S14, pts=5, created="2026-10-06", updated="2026-10-22"),
    _i("AMG-13", "스토리", "지라 이슈·에픽·진행 현황을 인수인계 자료로 가져오기", "진행 중", prio="높음", who="최환", by="김미고", epic="AMG-1", sprint=S14, pts=8, due="2026-10-30", labels=["연동", "지라"], created="2026-10-20", updated="2026-10-25",
       desc="지라 보드처럼 보이는 고르기 창에서 이슈·에픽·프로젝트 진행 현황을 골라 자료로 등록한다.\n- 에픽은 하위 이슈와 진행률을 함께 가져온다\n- 프로젝트 진행 현황은 스프린트 진척·담당자별 남은 일을 요약한다",
       comments=[("김미고", "2026-10-23", "후임자가 '지금 어디까지 했는지'를 바로 알 수 있게 진행 현황 요약이 꼭 들어가면 좋겠습니다."), ("최환", "2026-10-24", "프로젝트 진행 현황을 별도 항목으로 고를 수 있게 했습니다. 내일 리뷰 요청드릴게요.")]),
    _i("AMG-14", "작업", "연동 자료 근거 표시(출처 링크) 점검", "할 일", who="김민준", by="최환", epic="AMG-1", sprint=S14, pts=3, created="2026-10-20", updated="2026-10-20"),
    _i("AMG-15", "버그", "컨플루언스 페이지 표가 문서에서 깨지는 문제", "검토 중", prio="높음", who="최환", by="김민준", epic="AMG-1", sprint=S14, pts=2, created="2026-10-22", updated="2026-10-24",
       desc="병합 셀이 있는 표를 가져오면 열이 밀린다. 표를 Markdown 으로 바꿀 때 병합 셀을 풀어 쓰도록 수정.", comments=[("김민준", "2026-10-24", "수정본 확인 중입니다.")]),
    _i("AMG-16", "작업", "실제 메일 서버(IMAP) 연동 검토", "할 일", prio="낮음", who="최환", by="김미고", epic="AMG-1", sprint=S15, pts=5, created="2026-10-20", updated="2026-10-20",
       blocked="정보보호팀 메일 서버 접근 승인 대기(10/22 신청)"),
    _i("AMG-21", "스토리", "부서별 특이사항을 질문 우선순위에 반영", "완료", who="김민준", by="김미고", epic="AMG-2", sprint="AMG 스프린트 13", pts=5, created="2026-09-22", updated="2026-10-15"),
    _i("AMG-22", "스토리", "근거 번호가 다른 장 자료를 가리키는 오류 줄이기", "진행 중", prio="높음", who="김민준", by="김미고", epic="AMG-2", sprint=S14, pts=8, created="2026-10-08", updated="2026-10-24",
       desc="회귀 평가셋 120문항 기준 근거 정확도 91% → 96% 목표. 현재 94%.", comments=[("임윤희", "2026-10-23", "평가 파이프라인에 근거 정확도 지표 추가해 두었습니다.")]),
    _i("AMG-23", "작업", "모델 교체 전 회귀 평가 실행", "검토 중", who="임윤희", by="김민준", epic="AMG-2", sprint=S14, pts=3, created="2026-10-14", updated="2026-10-23"),
    _i("AMG-24", "버그", "장기휴가 인수인계서에 휴가 기간이 두 번 나옴", "완료", prio="보통", who="김민준", by="최환", epic="AMG-2", sprint=S14, pts=1, created="2026-10-20", updated="2026-10-21"),
    _i("AMG-25", "스토리", "빈 칸 질문을 한 번에 3개까지 묶어 묻기", "할 일", who="김민준", by="김미고", epic="AMG-2", sprint=S15, pts=5, created="2026-10-16", updated="2026-10-16"),
    _i("AMG-31", "스토리", "팀장 검토 화면(팀원 인수인계 현황)", "완료", who="김민준", by="김미고", epic="AMG-3", sprint="AMG 스프린트 13", pts=8, created="2026-09-08", updated="2026-10-10"),
    _i("AMG-32", "스토리", "인수자에게 인수인계서 전송·읽음 표시", "완료", who="최환", by="김미고", epic="AMG-3", sprint="AMG 스프린트 13", pts=5, created="2026-09-08", updated="2026-10-16"),
    _i("AMG-41", "작업", "동시 사용자 300명 부하 테스트", "할 일", prio="가장 높음", who="박서준", by="김미고", epic="AMG-4", sprint=S15, pts=5, due="2026-11-12", created="2026-10-15", updated="2026-10-15",
       desc="AI 분석 동시 실행 30건, 화면 동시 접속 300명 기준. 응답 3초 이내 목표."),
    _i("AMG-42", "작업", "운영 매뉴얼·장애 대응 연락망 작성", "진행 중", who="최환", by="김미고", epic="AMG-4", sprint=S14, pts=3, due="2026-10-31", created="2026-10-15", updated="2026-10-24",
       desc="배포 절차, 장애 등급별 연락 순서, AI 비용 한도 초과 시 조치를 정리한다. 초안 60% 작성."),
    _i("AMG-43", "작업", "부서별 사용자 교육 일정 잡기", "할 일", prio="보통", who="정하은", by="김미고", epic="AMG-4", sprint=S15, pts=2, created="2026-10-18", updated="2026-10-18"),
    _i("AMG-44", "작업", "AI 사용 비용 월 한도 알림 설정", "검토 중", who="임윤희", by="김미고", epic="AMG-4", sprint=S14, pts=2, created="2026-10-16", updated="2026-10-23"),
    _i("AMG-45", "하위 작업", "운영 매뉴얼 - 배포 절차 장", "완료", who="최환", parent="AMG-42", sprint=S14, created="2026-10-16", updated="2026-10-22"),
    _i("AMG-46", "하위 작업", "운영 매뉴얼 - 장애 등급별 연락망", "진행 중", who="최환", parent="AMG-42", sprint=S14, created="2026-10-16", updated="2026-10-24"),
    # ================================================================ INFRA — IT 인프라 운영
    _i("INFRA-1", "에픽", "VPN 장비 펌웨어 업데이트(9.4.2)", "진행 중", prio="높음", who="최환", by="김미고", due="2026-12-12", labels=["VPN", "보안"], created="2026-10-02", updated="2026-10-23",
       desc="제조사 보안 권고(CVE)에 따라 VPN 장비 펌웨어를 9.4.2 로 올린다. 서비스 중단 30분 이내."),
    _i("INFRA-2", "에픽", "노후 L2 스위치 12대 교체", "할 일", prio="보통", who="박서준", by="김미고", due="2027-03-31", labels=["네트워크"], created="2026-10-05", updated="2026-10-20",
       desc="도입 8년이 지난 L2 스위치 12대를 층별로 나눠 주말에 교체한다. 예산 확보 완료."),
    _i("INFRA-3", "에픽", "하반기 정기 보안 점검 조치", "진행 중", prio="가장 높음", who="박서준", by="이수진", due="2026-11-20", labels=["보안점검"], created="2026-09-28", updated="2026-10-24",
       desc="하반기 점검 지적 14건 조치. 11월 20일까지 결과 회신."),
    _i("INFRA-11", "작업", "검증 장비에 펌웨어 9.4.2 적용·기능 확인", "완료", who="최환", epic="INFRA-1", sprint=INF, pts=3, created="2026-10-02", updated="2026-10-16"),
    _i("INFRA-12", "작업", "운영 장비 적용 일정 업체 협의(시큐어넷)", "진행 중", who="최환", epic="INFRA-1", sprint=INF, pts=2, created="2026-10-10", updated="2026-10-23",
       desc="12월 둘째 주 토요일 오전 7시 가협의. 엔지니어 현장 대기 확정 필요.", comments=[("최환", "2026-10-23", "업체에서 12/12(토) 가능 회신. 팀장 결재 후 확정 예정.")]),
    _i("INFRA-13", "작업", "적용 전 설정 백업·롤백 절차서 작성", "할 일", who="최환", epic="INFRA-1", sprint=INF, pts=2, created="2026-10-10", updated="2026-10-10"),
    _i("INFRA-21", "작업", "교체 대상 스위치 포트 매핑 표 작성", "진행 중", who="박서준", epic="INFRA-2", sprint=INF, pts=3, created="2026-10-06", updated="2026-10-22", desc="12대 중 5대 완료."),
    _i("INFRA-22", "작업", "스위치 구매 요청 결재", "할 일", prio="높음", who="박서준", epic="INFRA-2", pts=1, due="2026-11-07", created="2026-10-06", updated="2026-10-06"),
    _i("INFRA-31", "작업", "계정 잠금 정책 변경(5회 실패 시 잠금)", "할 일", prio="높음", who="박서준", epic="INFRA-3", sprint=INF, pts=3, created="2026-10-01", updated="2026-10-18",
       blocked="업무 영향 검토로 12월 예외 승인(정보보호팀)"),
    _i("INFRA-32", "작업", "웹 서버 불필요 서비스 비활성화", "완료", who="박서준", epic="INFRA-3", sprint=INF, pts=2, created="2026-10-01", updated="2026-10-12"),
    _i("INFRA-33", "작업", "관리자 페이지 접근 IP 제한", "검토 중", who="최환", epic="INFRA-3", sprint=INF, pts=2, created="2026-10-01", updated="2026-10-24"),
    _i("INFRA-34", "작업", "DB 계정 비밀번호 주기 변경 증적 정리", "완료", who="박서준", epic="INFRA-3", sprint=INF, pts=1, created="2026-10-01", updated="2026-10-14"),
    _i("INFRA-41", "버그", "야간 배치 급여 작업 간헐 실패", "진행 중", prio="가장 높음", who="김민준", by="최환", sprint=INF, pts=3, labels=["배치", "장애"], created="2026-10-19", updated="2026-10-24",
       desc="10/19, 10/23 두 차례 DB 연결 타임아웃으로 실패. 재처리로 복구. 연결 풀 설정 점검 중.", comments=[("최환", "2026-10-24", "24일 급여 배치 전 수동 확인하기로 인사팀과 협의했습니다.")]),
    _i("INFRA-42", "작업", "백업 용량 증설 견적 요청", "완료", who="최환", sprint=INF, pts=1, created="2026-10-12", updated="2026-10-20"),
    # ================================================================ AIDEV — AI 플랫폼
    _i("AIDEV-1", "에픽", "다국어 임베딩 모델 교체", "진행 중", prio="높음", who="임윤희", by="최고미", due="2026-11-21", labels=["임베딩"], created="2026-09-20", updated="2026-10-24",
       desc="검색 정확도 5% 향상 목표. 교체 시 전체 재적재(약 6시간) 필요."),
    _i("AIDEV-2", "에픽", "평가 데이터셋 확장(부서 특이사항 200문항)", "진행 중", who="임윤희", by="최고미", due="2026-12-15", labels=["평가"], created="2026-10-01", updated="2026-10-22"),
    _i("AIDEV-11", "작업", "후보 모델 3종 검색 정확도 비교", "완료", who="임윤희", epic="AIDEV-1", sprint=AID, pts=5, created="2026-09-22", updated="2026-10-14"),
    _i("AIDEV-12", "작업", "재적재 시간대·공지 협의(IT아미고팀)", "진행 중", who="임윤희", epic="AIDEV-1", sprint=AID, pts=2, created="2026-10-14", updated="2026-10-23"),
    _i("AIDEV-13", "작업", "GPU 메모리 사용량 측정", "검토 중", who="한태양", epic="AIDEV-1", sprint=AID, pts=3, created="2026-10-14", updated="2026-10-22"),
    _i("AIDEV-21", "스토리", "부서 특이사항 문항 120개 작성", "완료", who="송지민", epic="AIDEV-2", sprint=AID, pts=5, created="2026-10-01", updated="2026-10-20"),
    _i("AIDEV-22", "스토리", "남은 80문항 부서 검토 받기", "할 일", who="송지민", epic="AIDEV-2", sprint=AID, pts=5, created="2026-10-01", updated="2026-10-01"),
    _i("AIDEV-31", "작업", "GPU 주간 배정표 자동 공지", "진행 중", who="한태양", sprint=AID, pts=3, created="2026-10-15", updated="2026-10-24"),
    # ================================================================ WEB — 기관 홈페이지 리뉴얼
    _i("WEB-1", "에픽", "홈페이지 리뉴얼 RFP·사업자 선정", "진행 중", prio="높음", who="김민수", by="김민수", due="2026-12-20", labels=["리뉴얼"], created="2026-10-01", updated="2026-10-23",
       desc="RFP 작성 → 11월 이사회 예산 승인(3억 원) → 12월 입찰·사업자 선정."),
    _i("WEB-2", "에픽", "웹 접근성 품질인증 갱신", "진행 중", who="이서연", by="김민수", due="2026-11-30", labels=["접근성"], created="2026-09-15", updated="2026-10-20"),
    _i("WEB-11", "작업", "RFP 초안 작성", "검토 중", who="김민수", epic="WEB-1", sprint=WEBS, pts=5, created="2026-10-02", updated="2026-10-23", comments=[("박지훈", "2026-10-22", "예산 항목은 재무팀 양식으로 바꿔 주세요.")]),
    _i("WEB-12", "작업", "이사회 예산 승인 자료 준비", "할 일", prio="높음", who="김민수", epic="WEB-1", sprint=WEBS, pts=3, due="2026-11-05", created="2026-10-02", updated="2026-10-02"),
    _i("WEB-21", "작업", "3분기 자체 점검 오류 수정 요청", "완료", who="이서연", epic="WEB-2", sprint=WEBS, pts=2, created="2026-09-16", updated="2026-10-10"),
    _i("WEB-22", "작업", "인증 갱신 심사 신청서 제출", "진행 중", who="이서연", epic="WEB-2", sprint=WEBS, pts=2, due="2026-10-31", created="2026-10-12", updated="2026-10-20"),
    _i("WEB-31", "버그", "모바일 메인 배너 겹침", "할 일", prio="보통", who="박준호", by="이서연", sprint=WEBS, pts=1, created="2026-10-21", updated="2026-10-21"),
]

DEFAULT_PROJECT_KEYS = {p.key for p in PROJECTS}  # 기본(팀을 모를 때) 프로젝트

from . import team_jira  # noqa: E402  팀별 프로젝트·이슈를 더한다(IT개발팀·디지털사업팀·법무팀·카드운영팀·기획조정팀)

PROJECTS += team_jira.PROJECTS
ISSUES += team_jira.ISSUES
TEAM_PROJECT_KEYS = team_jira.TEAM_PROJECT_KEYS
_BY_KEY = {i.key: i for i in ISSUES}
_PROJECTS = {p.key: p for p in PROJECTS}


def visible_keys(team: str = "") -> set[str]:
    """사용자의 팀이 보는 프로젝트 키. 팀별 데이터가 있는 팀은 그 팀 프로젝트, 아니면 기본 프로젝트."""
    key = TEAM_PROJECT_KEYS.get((team or "").strip())
    return {key} if key else set(DEFAULT_PROJECT_KEYS)


# ---------------------------------------------------------------- 계산
def children(epic_key: str) -> list[Issue]:
    return [i for i in ISSUES if i.epic == epic_key]


def subtasks(issue_key: str) -> list[Issue]:
    return [i for i in ISSUES if i.parent == issue_key]


def _progress(issues: list[Issue]) -> dict[str, Any]:
    """완료 비율: 스토리 포인트가 있으면 포인트 기준, 없으면 이슈 수 기준."""
    counts = {s: sum(1 for i in issues if i.status == s) for s in STATUSES}
    total_pts = sum(i.points for i in issues)
    done_pts = sum(i.points for i in issues if i.category == "done")
    if total_pts:
        percent = round(done_pts * 100 / total_pts)
    else:
        percent = round(counts["완료"] * 100 / len(issues)) if issues else 0
    return {"total": len(issues), "done": counts["완료"], "counts": counts, "points": total_pts, "done_points": done_pts, "percent": percent}


def _days_left(end: str) -> int:
    y, m, d = map(int, end.split("-"))
    return (date(y, m, d) - TODAY).days


def issue_summary(issue: Issue) -> dict[str, Any]:
    out = {
        "key": issue.key, "project": issue.project, "type": issue.type, "summary": issue.summary, "status": issue.status, "category": issue.category,
        "priority": issue.priority, "assignee": issue.assignee, "reporter": issue.reporter, "epic": issue.epic, "parent": issue.parent,
        "epic_summary": _BY_KEY[issue.epic].summary if issue.epic in _BY_KEY else "", "sprint": issue.sprint, "points": issue.points,
        "due": issue.due, "labels": list(issue.labels), "created": issue.created, "updated": issue.updated, "blocked": issue.blocked, "url": issue.url,
        "comment_count": len(issue.comments),
    }
    if issue.type == "에픽":
        out["progress"] = _progress(children(issue.key))
    return out


def project_summary(project: Project) -> dict[str, Any]:
    issues = [i for i in ISSUES if i.project == project.key]
    sprint = project.active_sprint
    sprint_issues = [i for i in issues if sprint and i.sprint == sprint.name]
    epics = [i for i in issues if i.type == "에픽"]
    open_by = {}
    for i in issues:
        if i.type != "에픽" and i.category != "done" and i.assignee:
            open_by[i.assignee] = open_by.get(i.assignee, 0) + 1
    return {
        "key": project.key, "name": project.name, "lead": project.lead, "team": project.team, "description": project.description,
        "url": f"{BASE_URL}/projects/{project.key}",
        "sprint": {"name": sprint.name, "start": sprint.start, "end": sprint.end, "goal": sprint.goal, "days_left": _days_left(sprint.end), **_progress(sprint_issues)} if sprint else None,
        "next_sprint": next(({"name": s.name, "start": s.start, "end": s.end, "goal": s.goal} for s in project.sprints if s.state == "예정"), None),
        "epics": [{"key": e.key, "summary": e.summary, "status": e.status, "assignee": e.assignee, "due": e.due, **_progress(children(e.key))} for e in epics],
        "progress": _progress([i for i in issues if i.type != "에픽"]),
        "open_by_assignee": dict(sorted(open_by.items(), key=lambda kv: -kv[1])),
        "blocked": [{"key": i.key, "summary": i.summary, "assignee": i.assignee, "reason": i.blocked} for i in issues if i.blocked and i.category != "done"],
        "issue_count": len(issues),
    }


# ---------------------------------------------------------------- 조회
def list_projects(team: str = "") -> list[dict[str, Any]]:
    keys = visible_keys(team)
    return [project_summary(p) for p in PROJECTS if p.key in keys]


def get_project(key: str) -> Project | None:
    return _PROJECTS.get(key.upper())


def get_issue(key: str) -> Issue | None:
    return _BY_KEY.get(key.upper())


def _terms(query: str) -> list[str]:
    return [t for t in re.split(r"\s+", query.strip().lower()) if t]


def search_issues(query: str = "", project: str = "", status: str = "", assignee: str = "", type_: str = "", limit: int = 100, team: str = "") -> dict[str, Any]:
    """검색어(띄어쓰기로 여러 개, 모두 포함)·프로젝트·상태·담당자·유형으로 거른 이슈(최근 수정 순)."""
    terms = _terms(query)
    rows = []
    keys = visible_keys(team)
    for issue in ISSUES:
        if issue.project not in keys:
            continue
        if project and issue.project != project.upper():
            continue
        if status and issue.status != status:
            continue
        if assignee and issue.assignee != assignee:
            continue
        if type_ and issue.type != type_:
            continue
        hay = " ".join([issue.key, issue.summary, issue.description, " ".join(issue.labels), issue.assignee, issue.reporter, *(c[2] for c in issue.comments)]).lower()
        if terms and not all(t in hay for t in terms):
            continue
        rows.append(issue)
    rows.sort(key=lambda i: (i.updated, i.key), reverse=True)
    assignees = sorted({i.assignee for i in ISSUES if i.assignee and i.project in keys and (not project or i.project == project.upper())})
    return {"query": query.strip(), "total": len(rows), "issues": [issue_summary(i) for i in rows[: max(1, limit)]], "statuses": STATUSES, "types": TYPES, "assignees": assignees}


def move_issue(issue: Issue, status: str) -> Issue:
    """보드에서 카드를 다른 열로 옮긴다(가상 데이터라 서버 메모리에만 반영되고, 서버를 다시 켜면 처음 상태로 돌아간다)."""
    if status not in STATUSES:
        raise ValueError(f"알 수 없는 상태입니다: {status}")
    issue.status = status
    issue.updated = TODAY.isoformat()
    return issue


def issue_detail(issue: Issue) -> dict[str, Any]:
    return {
        **issue_summary(issue),
        "description": issue.description,
        "comments": [{"author": a, "date": d, "body": b} for a, d, b in issue.comments],
        "children": [issue_summary(c) for c in children(issue.key)] if issue.type == "에픽" else [],
        "subtasks": [issue_summary(s) for s in subtasks(issue.key)],
    }


# ---------------------------------------------------------------- 인수인계 자료(Markdown)
def _row(issue: Issue) -> str:
    extra = f" · 막힘: {issue.blocked}" if issue.blocked and issue.category != "done" else ""
    return f"| {issue.key} | {issue.type} | {issue.summary} | {issue.status} | {issue.assignee or '-'} | {issue.points or '-'} | {issue.due or '-'}{extra} |"


TABLE_HEAD = ["| 키 | 유형 | 요약 | 상태 | 담당자 | 포인트 | 기한 |", "|---|---|---|---|---|---|---|"]


def issue_markdown(issue: Issue) -> str:
    project = _PROJECTS.get(issue.project)
    lines = [
        f"# [{issue.key}] {issue.summary}",
        "",
        f"- 출처: 지라 · {project.name if project else issue.project} ({issue.project})",
        f"- 유형: {issue.type} · 상태: {issue.status} · 우선순위: {issue.priority}",
        f"- 담당자: {issue.assignee or '없음'} · 보고자: {issue.reporter or '-'}",
    ]
    if issue.epic in _BY_KEY:
        lines.append(f"- 에픽: {issue.epic} {_BY_KEY[issue.epic].summary}")
    if issue.sprint:
        lines.append(f"- 스프린트: {issue.sprint}")
    if issue.points:
        lines.append(f"- 스토리 포인트: {issue.points}")
    if issue.due:
        lines.append(f"- 기한: {issue.due}")
    if issue.labels:
        lines.append(f"- 라벨: {', '.join(issue.labels)}")
    lines.append(f"- 생성: {issue.created} · 최종 수정: {issue.updated}")
    if issue.blocked and issue.category != "done":
        lines.append(f"- 막힘: {issue.blocked}")
    if issue.description:
        lines += ["", "## 설명", issue.description]
    if issue.type == "에픽":
        kids = children(issue.key)
        p = _progress(kids)
        lines += ["", "## 진행 현황", f"하위 이슈 {p['total']}개 중 {p['done']}개 완료 · 스토리 포인트 {p['done_points']}/{p['points']} ({p['percent']}%)",
                  " · ".join(f"{s} {n}" for s, n in p["counts"].items()), "", "## 하위 이슈", *TABLE_HEAD, *(_row(c) for c in kids)]
    subs = subtasks(issue.key)
    if subs:
        lines += ["", "## 하위 작업", *TABLE_HEAD, *(_row(s) for s in subs)]
    if issue.comments:
        lines += ["", "## 댓글", *(f"- {d} {a}: {b}" for a, d, b in issue.comments)]
    return "\n".join(lines) + "\n"


def project_markdown(project: Project) -> str:
    """프로젝트 진행 현황: 후임자가 '지금 어디까지 왔는지'를 한눈에 보도록 요약한다."""
    s = project_summary(project)
    lines = [
        f"# {project.name} ({project.key}) 진행 현황",
        "",
        f"- 출처: 지라 · 프로젝트 {project.key} · 기준일 {TODAY.isoformat()}",
        f"- 프로젝트 리더: {project.lead} · 담당 조직: {project.team}",
        f"- 개요: {project.description}",
        f"- 전체 진행률: 이슈 {s['progress']['total']}개 중 {s['progress']['done']}개 완료 · 스토리 포인트 {s['progress']['done_points']}/{s['progress']['points']} ({s['progress']['percent']}%)",
    ]
    if s["sprint"]:
        sp = s["sprint"]
        lines += ["", "## 현재 스프린트", f"- {sp['name']} ({sp['start']} ~ {sp['end']}, 남은 날 {sp['days_left']}일)", f"- 목표: {sp['goal']}",
                  f"- 진척: 스토리 포인트 {sp['done_points']}/{sp['points']} ({sp['percent']}%) · " + " · ".join(f"{k} {v}" for k, v in sp["counts"].items())]
        lines += ["", *TABLE_HEAD, *(_row(i) for i in ISSUES if i.project == project.key and i.sprint == sp["name"])]
    if s["next_sprint"]:
        n = s["next_sprint"]
        lines += ["", "## 다음 스프린트", f"- {n['name']} ({n['start']} ~ {n['end']}): {n['goal']}"]
    if s["epics"]:
        lines += ["", "## 에픽별 진행률", "| 에픽 | 요약 | 상태 | 담당자 | 기한 | 진행률 |", "|---|---|---|---|---|---|"]
        lines += [f"| {e['key']} | {e['summary']} | {e['status']} | {e['assignee'] or '-'} | {e['due'] or '-'} | {e['percent']}% ({e['done']}/{e['total']}) |" for e in s["epics"]]
    if s["open_by_assignee"]:
        lines += ["", "## 담당자별 남은 이슈", *(f"- {name}: {count}건" for name, count in s["open_by_assignee"].items())]
    if s["blocked"]:
        lines += ["", "## 막힌 이슈", *(f"- {b['key']} {b['summary']} ({b['assignee']}): {b['reason']}" for b in s["blocked"])]
    return "\n".join(lines) + "\n"


def resolve(item_id: str) -> tuple[str, str, str] | None:
    """고른 항목 ID(이슈 키 또는 'project:<키>') → (자료 이름, URL, Markdown). 없으면 None."""
    if item_id.lower().startswith("project:"):
        project = get_project(item_id.split(":", 1)[1])
        if project is None:
            return None
        return f"지라 · {project.name} 진행 현황", f"{BASE_URL}/projects/{project.key}", project_markdown(project)
    issue = get_issue(item_id)
    if issue is None:
        return None
    return f"지라 · [{issue.key}] {issue.summary}", issue.url, issue_markdown(issue)
