"""가상 Confluence(사내 위키) 데이터: 실제 Confluence API 대신 검색 화면과 인수인계에 쓸 예시 페이지.

기관 홈페이지 운영 업무(디지털전략부 웹서비스팀)와 관련된 페이지를 흉내 낸다.
실제 Confluence 를 연동할 때는 search_pages / get_page 를 REST API(/rest/api/content/search?cql=...) 호출로 바꾸면 된다.
모든 이름·연락처·날짜·금액은 예시 값이다.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

# 가상 컨플루언스 사이트(app/routers/mock_wiki.py, /wiki)로 연결된다. 다른 주소에서 띄울 때는 AMIGO_MOCK_WIKI_URL 로 바꾼다.
BASE_URL = os.environ.get("AMIGO_MOCK_WIKI_URL", "http://localhost:8000/wiki").rstrip("/")

SPACES: dict[str, str] = {
    "WEB": "웹서비스팀",
    "DSD": "디지털전략부",
    "SEC": "정보보호팀",
    "FIN": "재무팀",
    "HR": "인사·총무",
}


@dataclass
class Page:
    id: str
    space: str
    title: str
    body: str  # Markdown
    labels: list[str] = field(default_factory=list)
    author: str = "김민수"
    updated_at: str = "2026-10-01"
    version: int = 1
    ancestors: list[str] = field(default_factory=list)  # 상위 페이지 제목(경로 표시용)

    @property
    def url(self) -> str:
        return f"{BASE_URL}/pages/viewpage.action?pageId={self.id}"

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "space_key": self.space,
            "space_name": SPACES.get(self.space, self.space),
            "labels": list(self.labels),
            "author": self.author,
            "updated_at": self.updated_at,
            "version": self.version,
            "ancestors": list(self.ancestors),
            "url": self.url,
            "excerpt": _excerpt(self.body, [], 160),
        }

    def detail(self) -> dict[str, Any]:
        return {**self.summary(), "body": self.body}

    def to_markdown(self) -> str:
        """인수인계 자료(지식베이스)로 넣을 수 있게 페이지를 Markdown 으로 바꾼다."""
        path = " > ".join([SPACES.get(self.space, self.space), *self.ancestors, self.title])
        head = [
            f"# {self.title}",
            "",
            f"- 출처: 컨플루언스 · {path}",
            f"- 작성자: {self.author} · 최종 수정: {self.updated_at} (v{self.version})",
        ]
        if self.labels:
            head.append(f"- 라벨: {', '.join(self.labels)}")
        return "\n".join(head) + "\n\n" + self.body.strip() + "\n"


PAGES: list[Page] = [
    Page(
        id="1001",
        space="WEB",
        title="기관 홈페이지 운영 매뉴얼",
        labels=["홈페이지", "운영", "매뉴얼"],
        updated_at="2026-09-10",
        version=14,
        ancestors=["웹서비스팀 업무"],
        body="""## 1. 개요
기관 홈페이지(www.example.go.kr)의 콘텐츠 게시, 메뉴 관리, 장애 대응, 유지보수 업체 관리 절차를 정리한 문서입니다.
담당: 웹서비스팀 김민수 과장(정) · 이서연 대리(부)

## 2. 콘텐츠 게시 절차
1. 담당 부서가 게시판 '게시요청'에 요청서 등록(제목·본문·첨부·게시 희망일)
2. 웹서비스팀 검수: 오탈자, 개인정보 포함 여부, 웹 접근성(대체텍스트·표 제목)
3. 팀장 승인(전자결재) — 긴급 공지는 팀장 구두 승인 후 선게시, 사후 결재
4. CMS 에서 게시 후 요청 부서에 완료 통보
보도자료는 홍보팀 요청 → 당일 10시 게시가 원칙입니다.

## 3. 메뉴·배너 관리
- 메뉴 구조 변경은 분기 1회 부서 의견 수렴 후 일괄 반영
- 메인 배너는 최대 5개, 교체 시 모바일 미리보기 확인 필수

## 4. 장애 대응
- 모니터링 알림(문자)을 받으면 유지보수 업체(한빛웹솔루션 정다은 PM, 02-1234-5678)에 즉시 통보
- 계약 기준 장애 대응 시간은 1시간, 초과 시 월간 검수에 반영
- 장애 보고서는 발생 당일 접수, '장애 이력' 페이지에 기록

## 5. 정기 업무 달력
| 월 | 업무 |
|---|---|
| 매월 5영업일 | 유지보수 실적 검수 · 대금 지급 요청 |
| 1·4·7·10월 | 웹 접근성 자체 점검 |
| 11월 | 웹 접근성 품질인증 갱신 심사 |
| 12월 초 | SSL 인증서 갱신(만료 12월 15일) |
""",
    ),
    Page(
        id="1002",
        space="WEB",
        title="유지보수 계약 및 월간 검수 절차 (한빛웹솔루션)",
        labels=["유지보수", "계약", "검수", "SLA"],
        updated_at="2026-07-03",
        version=6,
        ancestors=["웹서비스팀 업무"],
        body="""## 계약 개요
- 업체: (주)한빛웹솔루션 (PM 정다은, 02-1234-5678, pm@hanbitweb.example)
- 기간: 2026-01-01 ~ 2026-12-31 (연 단위 갱신, 12월 재계약 협의)
- 월 유지보수비: 4,500,000원 (부가세 별도)

## SLA (계약서 별첨 3)
| 항목 | 기준 |
|---|---|
| 장애 대응 착수 | 통보 후 1시간 이내 |
| 장애 복구 | 4시간 이내 (중대 장애 2시간) |
| 일반 수정 요청 | 3영업일 이내 |
| 월간 실적보고서 | 익월 3영업일까지 제출 |

## 월간 검수 절차
1. 업체 실적보고서 접수(장애·요청 처리 건)
2. 장애 이력·요청 게시판과 대조, SLA 초과 건 사유 확인
3. 검수 확인서 작성 → 팀장 결재
4. 재무팀(박지훈 차장)에 대금 지급 요청 — 매월 5영업일 이내
2026년 7월부터 장애 대응 시간을 검수 항목에 추가했습니다.
""",
    ),
    Page(
        id="1003",
        space="WEB",
        title="웹 접근성 품질인증 갱신 안내",
        labels=["웹접근성", "품질인증", "점검"],
        updated_at="2026-10-06",
        version=9,
        ancestors=["웹서비스팀 업무", "웹 접근성"],
        body="""## 인증 현황
- 인증 기관: 한국웹접근성인증평가원(예시)
- 현재 인증서 번호: WA-2025-11-0342, 유효기간 2025-11-20 ~ 2026-11-19
- 갱신 심사 신청: 매년 10월 말까지, 심사 11월 둘째 주

## 분기 자체 점검
자동 점검 도구(예: OpenWAX)로 전 페이지를 점검하고 오류 목록을 업체에 전달합니다.
대체텍스트 누락과 키보드 접근 오류는 심사에서 바로 감점되므로 우선 수정합니다.

| 분기 | 오류 수 | 비고 |
|---|---|---|
| 2026 1Q | 42 | 이미지 대체텍스트 집중 보완 |
| 2026 2Q | 36 | |
| 2026 3Q | 31 | 외부 위젯 2건 제공사 확인 |
| 2026 4Q | 34 | 10/9 기준 28건 수정 완료 |
""",
    ),
    Page(
        id="1004",
        space="WEB",
        title="홈페이지 시스템 접근 권한 및 계정 목록",
        labels=["계정", "권한", "시스템"],
        updated_at="2026-10-01",
        version=11,
        ancestors=["웹서비스팀 업무"],
        body="""> 비밀번호는 이 페이지에 적지 않습니다. 계정 발급·회수는 IT지원팀(최영호 과장) 전자결재로 신청합니다.

| 시스템 | 용도 | 계정 | 발급 절차 |
|---|---|---|---|
| CMS 관리자 | 콘텐츠 게시·메뉴 관리 | kim.minsu, lee.seoyeon(2026-09-29 발급) | IT지원팀, 당일 |
| 웹서버 SSH | 로그 확인·인증서 적용 | 개인 계정 | 정보보호팀 승인 후 2~3일 |
| 모니터링 포털 | 장애 알림 수신자 관리 | 팀 공용 | 운영관리 포털, 팀장 승인 |
| 통계(Analytics) | 방문자 통계 | 팀 공용 뷰어 | 웹서비스팀 내부 |
| 인증서 관리 콘솔 | SSL 인증서 발급·갱신 | kim.minsu | 총무팀 결제 후 발급 |

기존 담당자 계정은 인사발령일(2026-10-30)에 회수되며, 모니터링 알림 수신자는 후임자로 변경 신청이 필요합니다.
""",
    ),
    Page(
        id="1005",
        space="DSD",
        title="홈페이지 리뉴얼 TF 주간회의록 2026-09-22",
        labels=["리뉴얼", "회의록", "TF"],
        author="강민지",
        updated_at="2026-09-22",
        version=2,
        ancestors=["홈페이지 리뉴얼 TF", "회의록"],
        body="""- 일시: 2026-09-22 10:00, 회의실 B
- 참석: 강민지(간사), 김민수, 이서연, 최영호(IT지원팀), 이수진(정보보호팀)

## 논의 사항
1. 추진 일정: 2026.10 설계 → 11월 이사회 예산 승인 → 12월 입찰 공고 → 2027.01 착수
2. 예산: 총 3억 원(콘텐츠 이관 6천만 원, 디자인·개발 2억 1천만 원, 인프라 3천만 원). 재무팀 검토 중
3. 인프라: 클라우드 전환 시 계정·권한 체계 재설계(IT지원팀), 개인정보 영향평가 필요 여부 확인(정보보호팀)
4. 현행 CMS 구조도·메뉴 트리는 유지보수 업체가 컨플루언스에 등록 예정

## 결정 사항
- 설계 검토 회의에 IT지원팀·정보보호팀 참석
- 제안요청서(RFP) 초안은 10월 24일 회의에서 검토
""",
    ),
    Page(
        id="1006",
        space="DSD",
        title="홈페이지 리뉴얼 예산 산출 근거",
        labels=["리뉴얼", "예산"],
        updated_at="2026-09-18",
        version=3,
        ancestors=["홈페이지 리뉴얼 TF"],
        body="""11월 이사회 안건으로 올릴 리뉴얼 예산(3억 원)의 산출 근거입니다. 재무팀(박지훈 차장) 검토 의견은 10월 말 회신 예정.

| 항목 | 금액 | 산출 근거 |
|---|---|---|
| 콘텐츠 이관 | 60,000,000 | 게시물 약 12,000건 × 건당 5,000원 |
| 디자인·개발 | 210,000,000 | 유사 기관 3곳 평균 |
| 인프라(클라우드 1년) | 30,000,000 | IT지원팀 견적 |

재무팀 1차 검토(10/15): 콘텐츠 이관 비용 산출 근거 보완 요청 → 게시물 건수 재집계 필요.
""",
    ),
    Page(
        id="1007",
        space="SEC",
        title="개인정보처리방침 개정 게시 전 보안 점검 체크리스트",
        labels=["개인정보", "보안점검", "체크리스트"],
        author="이수진",
        updated_at="2026-10-07",
        version=4,
        ancestors=["정보보호 업무", "홈페이지 보안"],
        body="""개인정보처리방침을 개정해 홈페이지에 게시하기 전 정보보호팀이 확인하는 항목입니다. 점검은 게시 하루 전까지 요청합니다.

- [ ] 수집 항목이 실제 입력 폼(회원가입·민원 신청)과 일치하는가
- [ ] 보유 기간이 내부 관리계획과 일치하는가
- [ ] 제3자 제공 항목에 제공받는 자·목적·항목·보유 기간이 모두 있는가
- [ ] 개정 이력(시행일)이 방침 하단에 기재되어 있는가
- [ ] 법무팀 검토 의견이 반영되었는가

2026년 10월 개정: 제3자 제공 항목 추가(법무팀 오승현 검토, 10/14 회신).
""",
    ),
    Page(
        id="1008",
        space="SEC",
        title="홈페이지 취약점 점검(모의해킹) 연간 계획",
        labels=["보안", "취약점", "모의해킹"],
        author="이수진",
        updated_at="2026-09-01",
        version=2,
        ancestors=["정보보호 업무", "홈페이지 보안"],
        body="""- 주기: 연 1회(11월 둘째 주), 외부 전문업체 수행
- 대상: 기관 홈페이지, 민원 신청 시스템
- 웹서비스팀 협조 사항: 유지보수 업체 담당자 연락처 공유, 점검 기간 중 배포 중지, 결과 보고서 기반 조치 계획 수립(30일 이내)
- 2025년 결과: 중위험 2건(세션 만료 시간, 파일 업로드 확장자 검증) → 2026-01 조치 완료
""",
    ),
    Page(
        id="1009",
        space="WEB",
        title="SSL 인증서 갱신 절차",
        labels=["SSL", "인증서", "정기업무"],
        updated_at="2025-12-10",
        version=5,
        ancestors=["웹서비스팀 업무", "정기 업무"],
        body="""- 현재 인증서 만료: 2026-12-15 (와일드카드, 1년 단위 갱신 — 2025년 보안 정책에 따라 1년으로 변경)
- 비용: 약 600,000원, 총무팀(윤태호) 법인카드 결제

## 절차
1. 11월 말: 인증 기관 견적서 수령 → 총무팀에 결제 요청(결제 1주 전)
2. 12월 초: 인증서 발급(인증서 관리 콘솔) 후 유지보수 업체에 서버 적용 요청 — 평일 야간(22시 이후) 작업
3. 적용 후 브라우저에서 만료일 확인, '시스템 접근 권한 및 계정 목록' 페이지의 만료일 갱신

만료 2주 전까지 시작하지 않으면 방문자 브라우저에 보안 경고가 표시됩니다.
""",
    ),
    Page(
        id="1010",
        space="WEB",
        title="장애 이력 2026",
        labels=["장애", "이력"],
        author="이서연",
        updated_at="2026-10-02",
        version=18,
        ancestors=["웹서비스팀 업무"],
        body="""| 일자 | 현상 | 원인 | 대응 시간 | 조치 |
|---|---|---|---|---|
| 2026-03-14 | 메인 페이지 응답 지연 | 트래픽 급증(채용공고) | 40분 | 캐시 설정 조정 |
| 2026-06-02 | 로그인 오류 | 인증 서버 점검 누락 | 1시간 30분 | 점검 공지 절차 보완 |
| 2026-09-12 | 게시판 첨부 업로드 실패 | 디스크 용량 부족 | 2시간 10분 (SLA 초과) | 용량 증설, 야간 당직 교대 조정 |
| 2026-10-02 | 첨부파일 다운로드 오류 | 스토리지 마운트 해제 | 28분 | 마운트 재연결, 자동 복구 스크립트 추가 |
""",
    ),
    Page(
        id="1011",
        space="HR",
        title="인수인계서 양식 및 제출 안내 (2026년 하반기 인사발령)",
        labels=["인수인계", "양식", "인사발령"],
        author="강민지",
        updated_at="2026-10-13",
        version=1,
        ancestors=["인사 안내"],
        body="""- 인사발령일: 2026-10-30
- 인수인계서 제출 기한: 2026-10-28 (부서장 확인 후 인사팀 제출)
- 구성: 업무 단위 요약, 정기 업무, 비정기 업무, 협업 관계, 시스템 접근 권한, 부서별 추가 자료
- AMIGO(인수인계 도우미)로 작성한 PDF 도 인정됩니다.
""",
    ),
    Page(
        id="1012",
        space="FIN",
        title="용역 대금 지급 요청 절차",
        labels=["대금지급", "재무"],
        author="박지훈",
        updated_at="2026-04-20",
        version=3,
        ancestors=["재무 업무 안내"],
        body="""유지보수·용역 대금은 다음 순서로 요청합니다.
1. 검수 확인서(팀장 결재) + 업체 세금계산서 + 실적보고서를 전자결재에 첨부
2. 재무팀 접수 후 5영업일 이내 지급(매월 5영업일 안에 요청해야 당월 처리)
3. 지급 완료는 전자결재 알림으로 통보

문의: 재무팀 박지훈 차장(내선 2345)
""",
    ),
]

DEFAULT_SPACES = set(SPACES)  # 기본(팀을 모를 때) 스페이스

from . import team_wiki  # noqa: E402  팀별 스페이스·페이지를 더한다(IT업무운영팀·디지털사업팀·법무팀·페이먼트사업팀·기획조정팀)

SPACES.update(dict(team_wiki.TEAM_SPACES.values()))
PAGES += team_wiki.PAGES
_BY_ID = {p.id: p for p in PAGES}


def visible_spaces(team: str = "") -> dict[str, str]:
    """사용자의 팀이 보는 스페이스. 팀별 데이터가 있는 팀은 그 팀 스페이스, 아니면 기본 스페이스."""
    if team == "*":  # 가상 위키 화면(/wiki)은 로그인 없이 모든 스페이스를 보여 준다
        return dict(SPACES)
    own = team_wiki.TEAM_SPACES.get((team or "").strip())
    return {own[0]: own[1]} if own else {k: v for k, v in SPACES.items() if k in DEFAULT_SPACES}


def _terms(query: str) -> list[str]:
    return [t for t in re.split(r"\s+", query.strip().lower()) if t]


def _plain(markdown: str) -> str:
    text = re.sub(r"^#+\s*", "", markdown, flags=re.MULTILINE)
    text = re.sub(r"[|>*`\-\[\]]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _excerpt(markdown: str, terms: list[str], width: int = 180) -> str:
    """첫 번째로 검색어가 나오는 자리를 중심으로 본문 일부를 돌려준다(검색어가 없으면 앞부분)."""
    text = _plain(markdown)
    lower = text.lower()
    start = 0
    hit = min((lower.find(t) for t in terms if lower.find(t) >= 0), default=-1)
    if hit > width // 3:
        start = hit - width // 3
    snippet = text[start : start + width]
    return ("…" if start > 0 else "") + snippet + ("…" if start + width < len(text) else "")


def search_pages(query: str, space: str = "", limit: int = 20, team: str = "") -> dict[str, Any]:
    """검색어(띄어쓰기로 여러 개)를 모두 포함하는 페이지를 제목 일치 → 최근 수정 순으로 돌려준다."""
    terms = _terms(query)
    rows: list[tuple[int, Page]] = []
    visible = visible_spaces(team)
    for page in PAGES:
        if page.space not in visible or (space and page.space != space):
            continue
        hay_title, hay_body, hay_labels = page.title.lower(), page.body.lower(), " ".join(page.labels).lower()
        if terms and not all(t in hay_title or t in hay_body or t in hay_labels for t in terms):
            continue
        score = sum(3 for t in terms if t in hay_title) + sum(2 for t in terms if t in hay_labels) + sum(hay_body.count(t) for t in terms)
        rows.append((score, page))
    rows.sort(key=lambda r: (r[0], r[1].updated_at), reverse=True)
    results = []
    for score, page in rows[: max(1, limit)]:
        item = page.summary()
        item["excerpt"] = _excerpt(page.body, terms)
        item["matched_in"] = [k for k, hay in (("title", page.title.lower()), ("labels", " ".join(page.labels).lower()), ("body", page.body.lower())) if any(t in hay for t in terms)]
        item["score"] = score
        results.append(item)
    return {"query": query.strip(), "terms": terms, "total": len(rows), "results": results, "spaces": [{"key": k, "name": v} for k, v in visible.items()]}


def get_page(page_id: str) -> Page | None:
    return _BY_ID.get(page_id)
