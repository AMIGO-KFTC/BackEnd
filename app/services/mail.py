"""메일 조회 API 연동.

업무 자료를 올릴 때 사내 메일함에서 업무 메일을 찾아 골라 가져올 수 있게 한다. 지금은 실제 메일 서버 대신
**가상 메일 조회 API**(MockMailProvider)를 붙여 두었다. 실제 메일 시스템(Exchange·사내 메일 API 등)을 붙일 때는
MailProvider 와 같은 메서드(search · get)를 가진 클래스를 만들어 Services.mail 만 바꾸면 된다.

가져온 메일은 표준 .eml(MIME, 첨부 포함)로 만들어 업로드 파일과 똑같이 저장·적재한다. RAG 의 메일 파서가
보낸 사람·받는 사람·일시·제목을 출처 표기로 쓰고, 지원 형식의 첨부파일도 함께 읽는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, formataddr
from typing import Protocol

KST = timezone(timedelta(hours=9))
MAIL_DOMAIN = "amigo.example"  # 가상 메일함 주소(예시 도메인)


@dataclass(frozen=True)
class MailAddress:
    name: str
    email: str


@dataclass(frozen=True)
class MailAttachment:
    name: str
    content_type: str
    content: bytes

    @property
    def size(self) -> int:
        return len(self.content)


@dataclass(frozen=True)
class MailMessage:
    id: str
    folder: str  # inbox(받은 메일함) | sent(보낸 메일함)
    subject: str
    sender: MailAddress
    to: tuple[MailAddress, ...]
    cc: tuple[MailAddress, ...]
    sent_at: datetime
    body: str
    attachments: tuple[MailAttachment, ...] = ()
    important: bool = False

    @property
    def snippet(self) -> str:
        return re.sub(r"\s+", " ", self.body).strip()[:120]


@dataclass
class MailQuery:
    q: str = ""
    sender: str = ""
    folder: str = ""  # 비우면 전체
    date_from: datetime | None = None
    date_to: datetime | None = None
    has_attachments: bool | None = None
    keywords: list[str] = field(default_factory=list)  # 단위업무명·설명에서 뽑은 낱말(관련도 정렬용)
    page: int = 1
    size: int = 20


@dataclass
class MailPage:
    items: list[tuple[MailMessage, int]]  # (메일, 관련도 점수)
    total: int


class MailProvider(Protocol):
    name: str

    def search(self, owner: MailAddress, query: MailQuery) -> MailPage: ...

    def get(self, owner: MailAddress, message_id: str) -> MailMessage | None: ...


def owner_address(login_id: str, name: str) -> MailAddress:
    local = re.sub(r"[^A-Za-z0-9._-]+", ".", login_id).strip(".") or "user"
    return MailAddress(name, f"{local}@{MAIL_DOMAIN}")


def task_keywords(*texts: str) -> list[str]:
    """단위업무명·설명에서 메일 관련도를 매길 낱말을 뽑는다(두 글자 이상, 흔한 낱말 제외)."""
    stop = {"관리", "업무", "운영", "담당", "관련", "기타", "및", "등"}
    words: list[str] = []
    for text in texts:
        for word in re.split(r"[\s,·/()\[\]:;.~\-]+", text or ""):
            word = word.strip()
            if len(word) >= 2 and word not in stop and word not in words:
                words.append(word)
    return words


def score(message: MailMessage, keywords: list[str]) -> int:
    haystack = " ".join([message.subject, message.body, message.sender.name, *(a.name for a in message.attachments)])
    return sum(haystack.count(k) + (2 if k in message.subject else 0) for k in keywords)


def build_eml(message: MailMessage) -> bytes:
    """메일을 표준 .eml 로 만든다(본문 + 첨부). RAG 메일 파서가 그대로 읽는다."""
    eml = EmailMessage()
    eml["Message-ID"] = f"<{message.id}@{MAIL_DOMAIN}>"
    eml["Subject"] = message.subject
    eml["From"] = formataddr((message.sender.name, message.sender.email))
    eml["To"] = ", ".join(formataddr((a.name, a.email)) for a in message.to)
    if message.cc:
        eml["Cc"] = ", ".join(formataddr((a.name, a.email)) for a in message.cc)
    eml["Date"] = format_datetime(message.sent_at)
    eml["X-AMIGO-Mail-Source"] = "mock"
    eml.set_content(message.body)
    for att in message.attachments:
        maintype, _, subtype = att.content_type.partition("/")
        if maintype == "text":
            eml.add_attachment(att.content.decode("utf-8"), subtype=subtype or "plain", filename=att.name)
        else:
            eml.add_attachment(att.content, maintype=maintype, subtype=subtype or "octet-stream", filename=att.name)
    return bytes(eml)


def eml_file_name(message: MailMessage) -> str:
    subject = re.sub(r"[\[\]]", "", message.subject).strip() or "메일"
    return f"메일_{message.sent_at.astimezone(KST):%Y%m%d}_{subject[:80]}.eml"


# ---------------------------------------------------------------------------- 가상 메일함

_P = MailAddress  # 짧게
TEAM = _P("웹서비스팀", f"webservice@{MAIL_DOMAIN}")
FIN = _P("박지영 대리(재무팀)", f"jy.park@{MAIL_DOMAIN}")
VENDOR = _P("이정훈 PM((주)한빛웹솔루션)", "pm.lee@hanbit-web.example")
VENDOR_DESK = _P("한빛웹솔루션 기술지원", "support@hanbit-web.example")
SEC = _P("최수연 차장(정보보호팀)", f"sy.choi@{MAIL_DOMAIN}")
PR = _P("오세라 주임(홍보실)", f"sera.oh@{MAIL_DOMAIN}")
CERT = _P("웹접근성 인증기관", "cert@wa-cert.example")
HEAD = _P("강태호 부장(디지털전략부)", f"th.kang@{MAIL_DOMAIN}")
GA = _P("윤서진 과장(총무팀)", f"sj.yoon@{MAIL_DOMAIN}")
CS = _P("정다은 대리(고객지원팀)", f"de.jung@{MAIL_DOMAIN}")
AUDIT = _P("감사실", f"audit@{MAIL_DOMAIN}")
SUCCESSOR = _P("이하늘 대리(웹서비스팀)", f"hn.lee@{MAIL_DOMAIN}")
HR = _P("인사팀", f"hr@{MAIL_DOMAIN}")
BUDGET = _P("한도윤 차장(기획예산팀)", f"dy.han@{MAIL_DOMAIN}")
CLUB = _P("사내 동호회", f"club@{MAIL_DOMAIN}")
ME = None  # 메일함 주인(조회하는 사용자)으로 바뀐다


def _att(name: str, text: str) -> MailAttachment:
    content_type = "text/csv" if name.endswith(".csv") else "text/markdown" if name.endswith(".md") else "text/plain"
    return MailAttachment(name, content_type, text.encode("utf-8"))


# (id, 며칠 전, 시각, 폴더, 보낸 사람, 받는 사람, 참조, 제목, 본문, 첨부, 중요)
_SAMPLES = [
    ("m-0001", 2, "09:12", "inbox", FIN, [ME], [TEAM], "[요청] 9월 홈페이지 유지보수 대금 지급 서류 제출",
     "김 과장님, 안녕하세요. 재무팀 박지영입니다.\n\n9월분 홈페이지 유지보수 대금(월 4,400,000원, 부가세 포함) 지급을 위해 아래 서류를 10월 20일까지 보내 주세요.\n"
     "1. 검수확인서(담당자·팀장 서명)\n2. 세금계산서(업체 발행)\n3. 월간 작업 결과 보고서\n\n"
     "매월 20일까지 서류가 오면 그달 말일에 지급됩니다. 기한을 넘기면 다음 달로 미뤄지니 참고 부탁드립니다.",
     [_att("유지보수_검수확인서_양식.txt", "검수확인서\n계약명: 기관 홈페이지 유지보수\n검수 기간: 2026-09-01 ~ 2026-09-30\n검수 결과: 적합 / 부적합\n검수자: (서명)\n확인자(팀장): (서명)\n")], True),
    ("m-0002", 4, "17:40", "inbox", VENDOR, [ME], [], "홈페이지 유지보수 9월 작업 결과 보고",
     "과장님, 한빛웹솔루션 이정훈입니다.\n\n9월 유지보수 작업 결과를 보고드립니다. 정기 점검 4회, 콘텐츠 반영 17건, 장애 조치 1건(9/18 메인 배너 미노출)을 처리했습니다.\n"
     "자세한 내역은 첨부 파일을 확인해 주세요. 검수 확인 후 세금계산서를 발행하겠습니다.",
     [_att("9월_작업내역.csv", "일자,구분,내용,처리시간(h)\n2026-09-03,정기점검,서버 자원·로그 점검,2\n2026-09-10,콘텐츠,공지사항 5건 게시,1\n2026-09-18,장애,메인 배너 미노출 조치(캐시 초기화),3\n2026-09-24,정기점검,백업 복구 시험,2\n")], False),
    ("m-0003", 5, "10:05", "inbox", SEC, [ME, TEAM], [], "[필독] 4분기 웹 취약점 점검 일정 안내",
     "정보보호팀입니다.\n\n4분기 대외 홈페이지 웹 취약점 점검을 11월 3일~7일에 실시합니다. 점검 기간에는 관리자 페이지 접근이 제한될 수 있습니다.\n"
     "점검 결과 '상' 등급 취약점은 2주 안에, '중' 등급은 한 달 안에 조치 결과를 회신해야 합니다. 조치 담당자를 10월 24일까지 알려 주세요.",
     [], True),
    ("m-0004", 6, "08:51", "inbox", PR, [ME], [], "보도자료 홈페이지 게시 요청 (10/15 오전 10시)",
     "과장님, 홍보실 오세라입니다.\n\n10월 15일 오전 10시에 '디지털 금융 인프라 고도화' 보도자료를 배포합니다. 같은 시각에 홈페이지 '알림마당 > 보도자료'에 게시 부탁드립니다.\n"
     "엠바고가 걸려 있어 10시 이전에는 공개되면 안 됩니다. 예약 게시 기능을 써 주세요.",
     [_att("보도자료_디지털금융인프라.txt", "[보도자료] 디지털 금융 인프라 고도화 추진\n배포일시: 2026-10-15 10:00 (엠바고)\n문의: 홍보실 오세라 주임\n")], False),
    ("m-0005", 9, "14:20", "inbox", CERT, [ME], [], "웹 접근성 품질인증 갱신 심사 신청 안내",
     "귀 기관의 웹 접근성 품질인증 유효기간이 2026년 12월 31일에 만료됩니다.\n\n갱신 심사를 받으려면 만료 60일 전(11월 1일)까지 신청서를 제출해 주세요. 심사는 신청 후 약 4주가 걸리며, 심사 수수료는 사이트 규모에 따라 다릅니다.\n"
     "신청서 양식을 첨부합니다.",
     [_att("품질인증_갱신신청서_양식.txt", "웹 접근성 품질인증 갱신 신청서\n기관명:\n사이트 주소:\n담당자(성명·연락처):\n이전 인증번호:\n")], True),
    ("m-0006", 12, "16:33", "sent", ME, [TEAM], [HEAD], "웹 접근성 3분기 자체 점검 결과 공유",
     "팀원 여러분,\n\n3분기 웹 접근성 자체 점검 결과를 공유합니다. 33개 검사 항목 중 30개 준수, 3개 미흡(대체 텍스트 누락, 표 제목 누락, 키보드 초점 이동)입니다.\n"
     "미흡 항목은 한빛웹솔루션에 수정을 요청했고 10월 말까지 반영 예정입니다. 점검은 매 분기 마지막 달 셋째 주에 진행합니다.",
     [_att("웹접근성_점검결과_3분기.csv", "항목,결과,조치\n적절한 대체 텍스트 제공,미흡,배너 이미지 12건 수정 요청\n표의 구성,미흡,공지 표 제목 추가\n키보드 사용 보장,미흡,메뉴 초점 순서 수정\n")], False),
    ("m-0007", 26, "07:58", "inbox", VENDOR_DESK, [ME], [VENDOR], "긴급 장애 조치 결과 보고 (메인 배너 미노출)",
     "9월 18일 07시 20분경 메인 화면 배너가 표시되지 않는 장애가 발생해 조치 결과를 알려 드립니다.\n\n원인: CDN 캐시 갱신 오류\n조치: 캐시 초기화 후 정상화(07:52)\n재발 방지: 배포 후 캐시 자동 갱신 스크립트 적용\n\n"
     "장애 발생 시 연락처: 기술지원 24시간 상황실 1588-0000(가상 번호)",
     [], False),
    ("m-0008", 14, "11:02", "inbox", HEAD, [ME], [], "홈페이지 개편 2027 사업계획 초안 검토 요청",
     "김 과장, 2027년 홈페이지 개편 사업계획 초안을 보냅니다. 예산은 3억 원 수준으로 잡았고, 상반기 착수가 목표입니다.\n"
     "모바일 화면 개선과 검색 기능 고도화를 핵심으로 넣었으니, 현행 운영 측면에서 빠진 것이 있는지 10월 말까지 의견 주세요.",
     [_att("홈페이지_개편_사업계획_초안.md", "# 2027 홈페이지 개편 사업계획(초안)\n\n- 예산: 3억 원\n- 기간: 2027.03 ~ 2027.09\n- 핵심: 모바일 화면 개선, 통합검색 고도화, 접근성 인증 유지\n")], False),
    ("m-0009", 18, "15:47", "inbox", GA, [ME], [], "2027년 홈페이지 유지보수 계약 갱신 일정 및 준비 서류",
     "총무팀 윤서진입니다.\n\n현재 (주)한빛웹솔루션과 맺은 홈페이지 유지보수 계약이 12월 31일에 끝납니다. 2027년 계약은 수의계약 한도를 넘으므로 제한경쟁 입찰로 진행해야 합니다.\n"
     "11월 7일까지 과업지시서·예정가격 산출 근거를 보내 주시면 입찰 공고를 준비하겠습니다.",
     [], True),
    ("m-0010", 21, "09:30", "inbox", SEC, [TEAM], [], "홈페이지 관리자 계정 비밀번호 변경 주기 안내",
     "홈페이지 CMS 관리자 계정은 90일마다 비밀번호를 바꿔야 합니다. 퇴직·전보자 계정은 발령일에 바로 잠가 주세요.\n"
     "관리자 계정 목록은 분기마다 정보보호팀에 제출해야 합니다.",
     [], False),
    ("m-0011", 3, "13:15", "inbox", CS, [ME], [], "홈페이지 '자주 묻는 질문' 메뉴 수정 요청",
     "고객지원팀 정다은입니다. 지난달 바뀐 수수료 기준이 '자주 묻는 질문'에 아직 예전 내용으로 남아 있어 문의가 많습니다.\n"
     "수정할 문구를 정리해 보내니 이번 주 안에 반영 부탁드립니다.",
     [_att("FAQ_수정문구.txt", "Q. 수수료는 얼마인가요?\nA. 2026년 9월부터 건당 300원에서 250원으로 낮아졌습니다.\n")], False),
    ("m-0012", 1, "18:05", "inbox", VENDOR, [ME], [], "SSL 인증서 만료 예정 안내 (11/20)",
     "홈페이지 SSL 인증서가 11월 20일에 만료됩니다. 갱신 비용은 유지보수 계약 범위에 포함되어 있으며, 발급 신청에 기관 담당자 승인이 필요합니다.\n"
     "11월 10일까지 승인 메일 부탁드립니다.",
     [], True),
    ("m-0013", 30, "10:40", "inbox", AUDIT, [ME], [HEAD], "웹사이트 개인정보 처리방침 점검 결과 회신 요청",
     "감사실입니다. 홈페이지 개인정보 처리방침 정기 점검 결과, 개정 이력 표기와 위탁업체 목록 갱신이 필요합니다.\n"
     "조치 결과를 10월 31일까지 회신해 주세요.",
     [], False),
    ("m-0014", 7, "10:12", "sent", ME, [VENDOR], [], "Re: 10월 정기 점검 일정 협의",
     "이정훈 PM님,\n\n10월 정기 점검은 매주 수요일 오전 7시~8시로 진행해 주세요. 점검 전날 오후 5시까지 점검 공지를 홈페이지에 올리겠습니다.\n"
     "웹 취약점 점검 기간(11/3~7)에는 배포를 멈춰 주세요.",
     [], False),
    ("m-0015", 0, "09:45", "inbox", SUCCESSOR, [ME], [], "인수인계 관련 질문드립니다",
     "과장님, 이하늘입니다. 홈페이지 업무를 받기 전에 몇 가지 여쭙니다.\n1. 유지보수 대금 서류는 매달 언제 준비하나요?\n2. 웹 접근성 인증 갱신은 누가 신청하나요?\n3. CMS 관리자 계정은 어떻게 넘겨받나요?",
     [], False),
    ("m-0016", 8, "17:00", "inbox", HR, [ME], [], "10월 정기 인사발령 안내",
     "10월 정기 인사발령 내용을 알려 드립니다. 발령일은 10월 30일이며, 전보 대상자는 발령일 전까지 업무 인수인계를 마쳐 주세요.",
     [], False),
    ("m-0017", 10, "08:30", "inbox", HEAD, [TEAM], [], "주간회의 안건 취합 (10/6)",
     "이번 주 주간회의 안건을 목요일 오후 3시까지 보내 주세요. 웹서비스팀은 유지보수 계약 갱신, 웹 접근성 인증, 4분기 보안 점검 준비 상황을 보고해 주시면 됩니다.",
     [], False),
    ("m-0018", 16, "14:55", "inbox", VENDOR, [ME], [], "CMS 버전 업그레이드 제안서",
     "현재 쓰는 CMS 4.2 버전이 2027년 6월에 기술 지원이 끝납니다. 업그레이드 방안과 일정을 제안서로 정리했습니다. 검토 부탁드립니다.",
     [_att("CMS_업그레이드_제안서.txt", "CMS 업그레이드 제안\n- 현행: 4.2 (지원 종료 2027-06)\n- 목표: 5.1\n- 예상 기간: 6주\n- 영향: 관리자 화면 변경, 플러그인 3종 교체\n")], False),
    ("m-0019", 11, "16:10", "inbox", BUDGET, [ME], [], "2027년 예산 요구서 작성 요청 (웹서비스 운영비)",
     "기획예산팀입니다. 2027년 웹서비스 운영비 예산 요구서를 10월 25일까지 제출해 주세요. 유지보수비·인증 수수료·SSL 인증서 비용을 나눠 적어 주시면 됩니다.",
     [_att("예산요구서_양식.csv", "비목,2026년 예산,2027년 요구액,산출 근거\n유지보수비,52800000,,\n접근성 인증 수수료,,,\nSSL 인증서,,,\n")], False),
    ("m-0020", 20, "11:25", "inbox", SEC, [TEAM], [], "웹 방화벽 정책 변경 완료 안내",
     "요청하신 웹 방화벽 정책(관리자 페이지 사내망 접근만 허용) 변경을 마쳤습니다. 외부에서 관리자 페이지에 접속해야 하면 VPN 을 써 주세요.",
     [], False),
    ("m-0021", 13, "12:00", "inbox", CLUB, [ME], [], "사내 동호회 가을 행사 안내",
     "가을 산행 행사를 10월 25일(토)에 엽니다. 참가 신청은 사내 게시판에서 받습니다.",
     [], False),
    ("m-0022", 15, "09:05", "inbox", CERT, [ME], [], "모바일 앱 접근성 점검 요청 회신",
     "요청하신 모바일 웹 접근성 점검은 품질인증 갱신 심사와 함께 받을 수 있습니다. 함께 신청하면 심사 기간이 1주 늘어납니다.",
     [], False),
]


class MockMailProvider:
    """가상 메일 조회 API. 사용자마다 같은 예시 메일함을 돌려준다(날짜는 오늘 기준으로 맞춘다)."""

    name = "mock"

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now

    def _messages(self, owner: MailAddress) -> list[MailMessage]:
        today = (self._now or datetime.now(KST)).astimezone(KST)
        out = []
        for mid, days, hhmm, folder, sender, to, cc, subject, body, attachments, important in _SAMPLES:
            hour, minute = (int(x) for x in hhmm.split(":"))
            sent_at = (today - timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            if sent_at > today:  # 오늘 메일이 아직 오지 않은 시각이면 하루 앞당긴다
                sent_at -= timedelta(days=1)
            out.append(
                MailMessage(
                    id=mid,
                    folder=folder,
                    subject=subject,
                    sender=sender or owner,
                    to=tuple(a or owner for a in to),
                    cc=tuple(a or owner for a in cc),
                    sent_at=sent_at,
                    body=body,
                    attachments=tuple(attachments),
                    important=important,
                )
            )
        return out

    def get(self, owner: MailAddress, message_id: str) -> MailMessage | None:
        return next((m for m in self._messages(owner) if m.id == message_id), None)

    def search(self, owner: MailAddress, query: MailQuery) -> MailPage:
        terms = [t.lower() for t in query.q.split() if t]
        sender = query.sender.strip().lower()
        hits: list[tuple[MailMessage, int]] = []
        for m in self._messages(owner):
            if query.folder and m.folder != query.folder:
                continue
            if query.date_from and m.sent_at < query.date_from:
                continue
            if query.date_to and m.sent_at > query.date_to:
                continue
            if query.has_attachments is not None and bool(m.attachments) != query.has_attachments:
                continue
            if sender and sender not in f"{m.sender.name} {m.sender.email}".lower():
                continue
            text = " ".join([m.subject, m.body, m.sender.name, m.sender.email, *(a.name for a in m.attachments)]).lower()
            if any(t not in text for t in terms):
                continue
            hits.append((m, score(m, query.keywords) if query.keywords else 0))
        hits.sort(key=lambda h: (h[1], h[0].sent_at), reverse=True)  # 관련도(단위업무 낱말) → 최신순
        start = (query.page - 1) * query.size
        return MailPage(items=hits[start : start + query.size], total=len(hits))
