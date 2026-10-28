"""원텔 캘린더 연동(예시 데이터).

캘린더 화면의 '원텔 캘린더와 연동하기'를 누르면 원텔 캘린더에서 가져올 수 있는 일정 목록(아래 예시)을 모달로 보여 주고,
고른 일정을 내 캘린더(planner_events)에 넣는다. 실제 원텔 API 는 연결하지 않았고, 연동할 때는 onetel_events() 가
돌려주는 목록 자리에 원텔 응답을 넣으면 된다.

- 날짜는 오늘부터 며칠 뒤로 잡아, 가져오자마자 이번 주·다음 주 일정으로 보이고 마감이 일주일 안인 것은 마감 임박 알림도 뜬다.
- id 는 일정 이름으로만 만들기 때문에(날짜 제외) 몇 번을 눌러도, 날이 바뀌어도 두 번 쌓이지 않는다.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import PlannerEvent, User

PREFIX = "[원텔]"
PLANNER_LIMIT = 5000  # routers/planner.py 의 MAX_EVENTS 와 같은 값

# (오늘부터 며칠 뒤, 일정 이름). 원텔 캘린더에서 온 팀 회의·마감·교육 예시.
SAMPLE_EVENTS: tuple[tuple[int, str], ...] = (
    (1, "팀 주간회의"),
    (3, "IT아미고팀 월간 업무보고"),
    (6, "오픈지로 회비 정산 마감"),
    (9, "삼성월렛 연동 개발 점검 회의"),
    (13, "인수인계서 제출 기한"),
    (20, "정보보호 교육 이수 마감"),
    (30, "분기 업무 실적 보고"),
)


def _key(name: str) -> str:
    return "ot" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:30]


def onetel_events(today: date) -> list[tuple[str, str, str]]:
    """원텔에서 가져올 수 있는 (key, 날짜, 내용) 일정."""
    return [(_key(name), (today + timedelta(days=days)).isoformat(), f"{PREFIX} {name}") for days, name in SAMPLE_EVENTS]


def _existing_keys(db: Session, user: User, keys: list[str]) -> set[str]:
    return set(db.scalars(select(PlannerEvent.key).where(PlannerEvent.user_id == user.id, PlannerEvent.key.in_(keys or [""]))))


def list_onetel_events(db: Session, user: User, today: date | None = None) -> list[tuple[str, str, str, bool]]:
    """가져올 수 있는 (key, 날짜, 내용, 이미 캘린더에 있는지) 목록."""
    events = onetel_events(today or date.today())
    have = _existing_keys(db, user, [e[0] for e in events])
    return [(key, day, text, key in have) for key, day, text in events]


def import_onetel_events(db: Session, user: User, ids: list[str] | None = None, today: date | None = None) -> tuple[int, int]:
    """내 캘린더에 원텔 일정을 넣는다(commit 은 부르는 쪽에서). ids 가 None 이면 모두, 아니면 고른 것만.
    돌려주는 값은 (새로 넣은 수, 이미 있어 건너뛴 수). 목록에 없는 id 는 부르는 쪽에서 미리 걸러야 한다."""
    events = [e for e in onetel_events(today or date.today()) if ids is None or e[0] in set(ids)]
    have = _existing_keys(db, user, [e[0] for e in events])
    room = PLANNER_LIMIT - (db.scalar(select(func.count(PlannerEvent.id)).where(PlannerEvent.user_id == user.id)) or 0)
    added = skipped = 0
    for key, day, text in events:
        if key in have:
            skipped += 1
        elif added < room:
            db.add(PlannerEvent(user_id=user.id, key=key, date=day, text=text))
            added += 1
    return added, skipped
