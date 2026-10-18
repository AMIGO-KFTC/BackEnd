"""가상 Mattermost(메신저) 데이터: 실제 Mattermost API 대신 화면과 인수인계에 쓸 '최근 대화' 예시.

데모 계정(김민수 · 디지털전략부 웹서비스팀 과장)이 기관 홈페이지 운영 업무로 주고받은 채널·개인 대화를 흉내 낸다.
실제 Mattermost 를 연동할 때는 이 모듈의 list_conversations / get_conversation 만 API 호출로 바꾸면 된다.
모든 이름·연락처·날짜는 예시 값이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ME = "kim.minsu"

# Mattermost 사용자(username → 표시 이름). 소속은 참여자 목록에 같이 보여 준다.
USERS: dict[str, dict[str, str]] = {
    "kim.minsu": {"name": "김민수", "org": "디지털전략부 웹서비스팀"},
    "lee.seoyeon": {"name": "이서연", "org": "디지털전략부 웹서비스팀"},
    "park.jihoon": {"name": "박지훈", "org": "재무팀"},
    "choi.youngho": {"name": "최영호", "org": "IT지원팀"},
    "lee.sujin": {"name": "이수진", "org": "정보보호팀"},
    "jung.daeun": {"name": "정다은", "org": "(주)한빛웹솔루션"},
    "han.jiwoo": {"name": "한지우", "org": "홍보팀"},
    "oh.seunghyun": {"name": "오승현", "org": "법무팀"},
    "yoon.taeho": {"name": "윤태호", "org": "총무팀"},
    "kang.minji": {"name": "강민지", "org": "디지털전략부"},
}


@dataclass
class Post:
    user: str
    at: str  # "YYYY-MM-DD HH:MM"
    text: str

    def as_dict(self, index: int) -> dict[str, Any]:
        profile = USERS.get(self.user, {"name": self.user, "org": ""})
        return {
            "id": f"{index:04d}",
            "user": self.user,
            "user_name": profile["name"],
            "user_org": profile["org"],
            "created_at": self.at,
            "text": self.text,
            "mine": self.user == ME,
        }


@dataclass
class Conversation:
    id: str
    type: str  # channel(공개 채널) | private(비공개 채널) | group(그룹 메시지) | dm(개인 메시지)
    name: str  # 채널 이름(URL 용). DM 은 상대 username
    display_name: str
    team: str
    purpose: str = ""
    posts: list[Post] = field(default_factory=list)

    @property
    def members(self) -> list[str]:
        seen: list[str] = []
        for post in self.posts:
            if post.user not in seen:
                seen.append(post.user)
        return seen

    @property
    def last_post(self) -> Post:
        return self.posts[-1]

    def summary(self) -> dict[str, Any]:
        last = self.last_post
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "display_name": self.display_name,
            "team": self.team,
            "purpose": self.purpose,
            "members": [USERS.get(u, {"name": u})["name"] for u in self.members],
            "member_count": len(self.members),
            "message_count": len(self.posts),
            "first_post_at": self.posts[0].at,
            "last_post_at": last.at,
            "last_message": {"user_name": USERS.get(last.user, {"name": last.user})["name"], "text": last.text},
            "url": f"mock://mattermost/{self.team}/{self.type}/{self.name}",
        }

    def detail(self) -> dict[str, Any]:
        return {**self.summary(), "posts": [p.as_dict(i + 1) for i, p in enumerate(self.posts)]}

    def to_markdown(self) -> str:
        """인수인계 자료(지식베이스)로 넣을 수 있게 대화를 날짜별 Markdown 으로 바꾼다."""
        kind = {"channel": "공개 채널", "private": "비공개 채널", "group": "그룹 메시지", "dm": "개인 메시지"}[self.type]
        members = ", ".join(f"{USERS[u]['name']}({USERS[u]['org']})" if u in USERS else u for u in self.members)
        lines = [
            f"# Mattermost 대화: {self.display_name} ({kind})",
            "",
            f"- 팀: {self.team}",
            f"- 참여자: {members}",
            f"- 기간: {self.posts[0].at[:10]} ~ {self.last_post.at[:10]} · 메시지 {len(self.posts)}건",
        ]
        if self.purpose:
            lines.append(f"- 채널 설명: {self.purpose}")
        day = ""
        for post in self.posts:
            if post.at[:10] != day:
                day = post.at[:10]
                lines += ["", f"## {day}", ""]
            name = USERS.get(post.user, {"name": post.user})["name"]
            lines.append(f"- [{post.at[11:]}] **{name}**: {post.text}")
        return "\n".join(lines) + "\n"


CONVERSATIONS: list[Conversation] = [
    Conversation(
        id="ch-homepage-ops",
        type="channel",
        name="homepage-ops",
        display_name="#홈페이지-운영",
        team="디지털전략부",
        purpose="기관 홈페이지 게시·승인·장애 공유",
        posts=[
            Post("han.jiwoo", "2026-09-29 09:12", "10월 1일 보도자료 3건 홈페이지 게시 요청드립니다. 첨부는 메일로 보냈어요."),
            Post("kim.minsu", "2026-09-29 09:30", "확인했습니다. 보도자료는 홍보팀 게시 요청 → 웹서비스팀 검수 → 당일 10시 게시가 원칙이라 1일 오전에 올라갑니다."),
            Post("lee.seoyeon", "2026-09-30 16:40", "메인 배너 교체 완료했습니다. 모바일 미리보기도 확인했어요."),
            Post("jung.daeun", "2026-10-02 11:05", "[장애] 10:20~10:48 사이 게시판 첨부파일 다운로드 오류가 있었습니다. 스토리지 마운트 재연결로 복구 완료, 상세 보고서는 오늘 중 드릴게요."),
            Post("kim.minsu", "2026-10-02 11:10", "장애 시간은 월간 검수 항목(장애 대응 1시간 기준)에 반영하겠습니다. 재발 방지 대책도 보고서에 넣어 주세요."),
            Post("kim.minsu", "2026-10-07 14:00", "공지사항 게시 절차 정리: 1) 담당 부서가 게시 요청(게시판 '게시요청') 2) 웹서비스팀 검수(오탈자·개인정보·접근성) 3) 팀장 승인 4) 게시. 긴급 공지는 팀장 구두 승인 후 선게시 가능."),
            Post("han.jiwoo", "2026-10-14 10:22", "개인정보처리방침 개정본 게시는 15일까지 맞나요?"),
            Post("kim.minsu", "2026-10-14 10:25", "네, 법무팀 검토 회신 오면 바로 올리겠습니다. 게시 전 정보보호팀 보안 점검도 같이 받습니다."),
            Post("lee.seoyeon", "2026-10-16 17:50", "이번 주 게시 현황: 공지 4건, 보도자료 2건, 채용공고 1건 처리 완료. 다음 주 월요일 정기 점검 예정입니다."),
        ],
    ),
    Conversation(
        id="dm-jung-daeun",
        type="dm",
        name="jung.daeun",
        display_name="정다은 (한빛웹솔루션 PM)",
        team="디지털전략부",
        posts=[
            Post("jung.daeun", "2026-09-25 15:02", "9월 유지보수 실적보고서 초안 공유드립니다. 장애 2건, 요청 처리 11건입니다."),
            Post("kim.minsu", "2026-09-25 15:20", "장애 2건 중 9/12 건은 대응 시간이 2시간 10분이라 SLA(1시간) 초과네요. 사유를 보고서에 명시해 주세요."),
            Post("jung.daeun", "2026-09-25 15:35", "네, 야간 당직 인수인계 지연이 원인이었고 10월부터 당직 교대 시간을 조정했습니다. 수정본 다시 드리겠습니다."),
            Post("kim.minsu", "2026-10-06 09:40", "9월 검수 확인서 결재 완료했습니다. 재무팀에 대금 지급 요청 넘겼어요(매월 5영업일 이내)."),
            Post("jung.daeun", "2026-10-13 13:10", "홈페이지 리뉴얼 입찰 공고 나오면 저희도 참여 예정입니다. 현행 CMS 구조도와 메뉴 트리는 컨플루언스에 올려 두었습니다."),
            Post("kim.minsu", "2026-10-13 13:15", "감사합니다. SSL 인증서 만료가 12월 15일이라 12월 초에 서버 적용 요청드릴게요. 담당자 바뀌어도 같은 절차로 진행됩니다."),
            Post("jung.daeun", "2026-10-13 13:16", "네, 인증서 적용은 평일 야간(22시 이후) 작업으로 잡겠습니다."),
        ],
    ),
    Conversation(
        id="dm-park-jihoon",
        type="dm",
        name="park.jihoon",
        display_name="박지훈 차장 (재무팀)",
        team="디지털전략부",
        posts=[
            Post("kim.minsu", "2026-09-18 10:05", "차장님, 홈페이지 리뉴얼 예산 3억 원 편성 관련해서 11월 이사회 안건으로 올리려고 합니다. 산출 근거 자료 먼저 보내드릴게요."),
            Post("park.jihoon", "2026-09-18 10:30", "네, 받았습니다. 이사회 2주 전(10월 말)까지 재무팀 검토 의견 드리겠습니다. 유지보수비와 중복되는 항목이 없는지 확인해 주세요."),
            Post("park.jihoon", "2026-10-08 11:20", "9월 유지보수 대금 지급 완료했습니다. 지급 요청은 지금처럼 매월 5영업일 안에 주시면 됩니다."),
            Post("kim.minsu", "2026-10-08 11:22", "감사합니다. 리뉴얼 예산 검토 의견은 10월 말에 받는 걸로 알고 있겠습니다."),
            Post("park.jihoon", "2026-10-15 16:45", "리뉴얼 예산 1차 검토 결과, 콘텐츠 이관 비용 항목의 산출 근거 보완이 필요합니다. 내주 중 미팅 가능할까요?"),
        ],
    ),
    Conversation(
        id="ch-web-accessibility",
        type="private",
        name="web-accessibility",
        display_name="#웹접근성-품질관리",
        team="디지털전략부",
        purpose="분기 자체 점검·연 1회 품질인증 갱신",
        posts=[
            Post("kim.minsu", "2026-10-01 09:00", "4분기 웹 접근성 자체 점검 시작합니다. 자동 점검 도구로 전 페이지 돌린 뒤 오류 목록을 업체에 전달할 예정입니다."),
            Post("lee.seoyeon", "2026-10-05 14:30", "자동 점검 결과: 대체텍스트 누락 23건, 키보드 접근 오류 7건, 명도 대비 4건. 3분기(31건)보다 소폭 줄었습니다."),
            Post("kim.minsu", "2026-10-05 14:35", "대체텍스트와 키보드 접근 오류는 인증 심사에서 바로 감점되니 우선 수정 요청해 주세요. 11월 품질인증 갱신 심사 전까지 끝나야 합니다."),
            Post("jung.daeun", "2026-10-09 10:15", "오류 34건 중 28건 수정 완료, 나머지 6건(외부 위젯)은 제공사 확인 중입니다."),
            Post("kang.minji", "2026-10-12 11:00", "인증 갱신 심사 신청서는 10월 말까지 제출해야 합니다. 작년 인증서 번호는 컨플루언스 '웹 접근성 품질인증' 페이지에 있어요."),
        ],
    ),
    Conversation(
        id="gm-renewal-tf",
        type="group",
        name="renewal-tf",
        display_name="홈페이지 리뉴얼 TF",
        team="디지털전략부",
        posts=[
            Post("kang.minji", "2026-09-22 10:00", "리뉴얼 TF 주간회의 요약: 일정 2026.10~2027.01, 11월 이사회 예산 승인 뒤 입찰 공고. 회의록은 컨플루언스에 올렸습니다."),
            Post("choi.youngho", "2026-09-22 10:12", "신규 서버는 클라우드로 가는 방향이면 계정·권한 체계를 새로 잡아야 합니다. 리뉴얼 설계 때 IT지원팀 참여 부탁드려요."),
            Post("kim.minsu", "2026-09-22 10:20", "네, 설계 검토 회의에 IT지원팀·정보보호팀 모두 참석 요청하겠습니다."),
            Post("lee.sujin", "2026-10-10 15:40", "리뉴얼 시 개인정보 수집 항목이 바뀌면 영향평가가 필요할 수 있습니다. 설계 단계에서 공유 부탁드립니다."),
            Post("kim.minsu", "2026-10-17 09:30", "다음 TF 회의는 10월 24일(금) 14시, 안건은 제안요청서(RFP) 초안 검토입니다."),
        ],
    ),
    Conversation(
        id="dm-choi-youngho",
        type="dm",
        name="choi.youngho",
        display_name="최영호 과장 (IT지원팀)",
        team="디지털전략부",
        posts=[
            Post("kim.minsu", "2026-09-26 11:00", "과장님, 후임자(이서연 대리) CMS 관리자 계정과 웹서버 접속 권한 발급 요청드립니다. 신청서는 전자결재로 올렸어요."),
            Post("choi.youngho", "2026-09-26 11:40", "확인했습니다. CMS 관리자 계정은 당일, 서버 SSH 접근 권한은 정보보호팀 승인 후 발급(통상 2~3일)됩니다."),
            Post("choi.youngho", "2026-09-29 09:50", "이서연 대리 CMS 계정 발급 완료, 서버 접근 권한은 10/1 부로 열렸습니다. 기존 담당자 계정은 인사발령일에 회수됩니다."),
            Post("kim.minsu", "2026-10-15 13:05", "홈페이지 모니터링 알림(장애 문자) 수신자도 후임자로 바꿔 주세요. 현재는 제 번호로만 옵니다."),
            Post("choi.youngho", "2026-10-15 13:30", "모니터링 수신자 변경은 운영관리 포털에서 팀장 승인으로 처리됩니다. 신청 올려 주시면 바로 반영할게요."),
        ],
    ),
    Conversation(
        id="dm-lee-sujin",
        type="dm",
        name="lee.sujin",
        display_name="이수진 대리 (정보보호팀)",
        team="디지털전략부",
        posts=[
            Post("lee.sujin", "2026-10-07 10:10", "개인정보처리방침 개정본 게시 전 보안 점검 일정 잡으려고 합니다. 법무팀 검토가 끝나면 알려 주세요."),
            Post("kim.minsu", "2026-10-07 10:15", "네, 법무팀 회신 오는 대로 공유드리겠습니다. 점검은 게시 하루 전에 가능할까요?"),
            Post("lee.sujin", "2026-10-07 10:18", "가능합니다. 점검 항목은 수집 항목·보유 기간·제3자 제공 문구 일치 여부이고 체크리스트는 컨플루언스에 있어요."),
            Post("lee.sujin", "2026-10-16 09:00", "연 1회 홈페이지 취약점 점검(모의해킹)은 11월 둘째 주 예정입니다. 업체 담당자 연락처 공유 부탁드립니다."),
        ],
    ),
    Conversation(
        id="dm-oh-seunghyun",
        type="dm",
        name="oh.seunghyun",
        display_name="오승현 (법무팀)",
        team="디지털전략부",
        posts=[
            Post("kim.minsu", "2026-09-30 14:00", "개인정보처리방침 개정 문구 검토 요청드립니다(제3자 제공 항목 추가). 10월 15일 게시 목표입니다."),
            Post("oh.seunghyun", "2026-09-30 14:30", "접수했습니다. 통상 2주 걸리는데 일정에 맞춰 보겠습니다."),
            Post("oh.seunghyun", "2026-10-14 17:20", "검토 결과 회신드립니다. 제3자 제공 항목에 '제공받는 자의 보유 기간'을 추가해야 하고, 나머지는 이상 없습니다. 수정본 주시면 최종 확인하겠습니다."),
            Post("kim.minsu", "2026-10-14 17:40", "감사합니다. 수정해서 내일 오전에 다시 보내드릴게요. 게시는 정보보호팀 점검 후 진행합니다."),
        ],
    ),
    Conversation(
        id="dm-yoon-taeho",
        type="dm",
        name="yoon.taeho",
        display_name="윤태호 (총무팀)",
        team="디지털전략부",
        posts=[
            Post("kim.minsu", "2026-10-10 09:20", "SSL 인증서 갱신(12월 15일 만료) 법인카드 결제 미리 요청드립니다. 금액은 작년과 같은 약 60만 원입니다."),
            Post("yoon.taeho", "2026-10-10 09:45", "12월 초 결제 일정으로 잡아 두겠습니다. 결제 1주 전에 견적서만 보내 주세요."),
        ],
    ),
    Conversation(
        id="ch-digital-notice",
        type="channel",
        name="digital-notice",
        display_name="#디지털전략부-공지",
        team="디지털전략부",
        purpose="부서 공지",
        posts=[
            Post("kang.minji", "2026-10-02 08:50", "10월 부서 회의는 매주 월요일 10시, 회의실 B 입니다."),
            Post("kang.minji", "2026-10-13 08:55", "인사발령(10/30) 대상자는 인수인계서를 10월 28일까지 제출해 주세요. 양식은 컨플루언스 '인수인계서 양식' 페이지 참고."),
            Post("kang.minji", "2026-10-17 17:10", "다음 주 수요일(10/22) 전사 보안 교육이 있습니다. 미이수자는 사이버 교육으로 대체 가능합니다."),
        ],
    ),
]

_BY_ID = {c.id: c for c in CONVERSATIONS}


def list_conversations(query: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """최근 대화 순(마지막 메시지 시각 내림차순). query 가 있으면 대화 이름·참여자·메시지에서 찾는다."""
    rows = sorted(CONVERSATIONS, key=lambda c: c.last_post.at, reverse=True)
    needle = query.strip().lower()
    if needle:
        rows = [c for c in rows if needle in c.display_name.lower() or any(needle in USERS.get(u, {"name": u})["name"].lower() for u in c.members) or any(needle in p.text.lower() for p in c.posts)]
    return [c.summary() for c in rows[: max(1, limit)]]


def get_conversation(conversation_id: str) -> Conversation | None:
    return _BY_ID.get(conversation_id)

