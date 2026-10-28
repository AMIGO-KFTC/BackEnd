"""받은 인수인계서의 일정을 인수자 캘린더에 넣는다('받은 인수인계서 적용하기').

- 캘린더 화면의 '받은 인수인계서 적용하기'(POST /api/handovers/received/{id}/calendar)로 넣고,
  인계자가 인수인계서를 전송할 때(POST /api/sessions/{id}/transfers)도 인수자 캘린더에 자동으로 넣는다.
- 날짜가 정해진 것(마감일, 2026-12-15, 계획기간의 시작·종료일)은 캘린더의 그날 일정(planner_events)으로,
  반복해서 하는 일(매일·매주·매월·분기·반기·매년·수시)은 날짜로 풀지 않고 정기 업무(planner_routines)로 넣는다.
  캘린더 화면은 매주 업무를 달력의 주(N째주) 옆에, 매월 업무와 그 달에 해당하는 분기·반기·매년 업무(months)를 달력 아래 'N월에 해야 할 일'에 보여 준다.
- id 를 인수인계·항목·날짜로 만들기 때문에 여러 번 적용해도 두 번 쌓이지 않는다(planner 의 같은 id 규칙).

어디서 읽나(인수인계서 장 = AI 템플릿의 슬롯)
- regular(정기업무)의 timing(수행 시기)       예: "매월 5영업일 이내", "분기별(1·4·7·10월)", "매년 12월 초(인증서 만료 2026-12-15)"
- leave_tasks(장기휴가 세부 업무)의 schedule   예: "11/14까지 검수 완료", "매주 금요일 실적 확인"
- 일정 명세표(월별 막대)의 계획기간: overview(업무 단위)·office_plans(팀장 사업계획)의 period
  예: "2026.03~2026.12", "11월~2월" → 막대가 이어지는 구간마다 시작일(첫날)·종료일(말일). 일정표에 그려진 기간이므로
  이미 지난 시작일도 넣는다(일정표와 같은 해석: amigo_agent.periods.plan_span). '상시'는 정기업무 장이 맡는다.
"""

from __future__ import annotations

import calendar
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from amigo_agent.periods import plan_span

from ..models import HandoverSession, PlannerEvent, PlannerRoutine, User

HORIZON_DAYS = 365  # 한 번뿐인 날짜는 오늘부터 1년 안의 것만(지난 날짜는 넣지 않는다)
PLANNER_LIMIT = 5000  # routers/planner.py 의 MAX_EVENTS 와 같은 값
PREFIX = "[인수]"

# 정기 업무 주기(화면의 '정기 업무' 칸이 이 순서로 묶는다)
CADENCES = ("daily", "weekly", "monthly", "quarterly", "yearly", "other")
CADENCE_LABELS = {"daily": "매일", "weekly": "매주", "monthly": "매월", "quarterly": "분기·반기", "yearly": "매년", "other": "수시·기타"}

DEADLINE = re.compile(r"까지|마감|기한|이내|만료|제출")


@dataclass
class Parsed:
    """일정 문구 하나를 푼 결과: 캘린더에 올릴 (날짜, 붙일 말)과 정기 업무 주기(없으면 None)."""

    dates: list[tuple[date, str]] = field(default_factory=list)
    cadence: str | None = None


@dataclass
class ScheduleResult:
    events: int = 0  # 새로 넣은 일정 수
    routines: int = 0  # 새로 넣은 정기 업무 수
    skipped: int = 0  # 이미 있어서 건너뛴 수


# ---------------------------------------------------------------- 문구 해석

_FULL_DATE = re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?")
_YEAR_MONTH = re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*월?(?![\d.\-/])")
_MONTH_DAY = re.compile(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_SLASH_DAY = re.compile(r"(?<![\d.])(\d{1,2})\s*/\s*(\d{1,2})(?![\d/])")

_DAILY = re.compile(r"매일|날마다|매\s*영업일|일일\s*(점검|확인|보고|마감)")
_WEEKLY = re.compile(r"매주|주간|주\s*\d\s*회|격주|[월화수목금]요일마다")
_MONTHLY = re.compile(r"매월|매달|월별|월간|월\s*\d\s*회|매\s*월(?!\s*\d)|영업일")
_QUARTERLY = re.compile(r"분기|반기")
_YEARLY = re.compile(r"매년|연간|해마다|연\s*\d\s*회|연례|매\s*년|년\s*\d\s*회")
_OCCASIONAL = re.compile(r"수시|상시|필요\s*시|발생\s*시|요청\s*시")
_MONTH_LIST = re.compile(r"(?<!\d)(\d{1,2}(?:\s*[·,/및]\s*\d{1,2})*)\s*월")


def _month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _next_occurrence(today: date, month: int, day: int) -> date | None:
    for year in (today.year, today.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if d >= today:
            return d
    return None


def parse_schedule(text: str, today: date, recurring: bool = True, horizon_days: int = HORIZON_DAYS) -> Parsed:
    """일정 문구를 캘린더 날짜와 정기 업무 주기로 나눈다.

    recurring 이 True(정기업무 장)이면 날짜가 없거나 반복 문구여도 정기 업무로 남긴다. False(장기휴가·업무 단위 요약)이면
    반복 문구가 분명할 때만 정기 업무로 본다.
    """
    raw = re.sub(r"\s+", " ", (text or "")).strip()
    result = Parsed()
    if not raw or re.fullmatch(r"(해당\s*없음|없음|-|미정)\.?", raw):
        return result
    end = today + timedelta(days=horizon_days)
    deadline = bool(DEADLINE.search(raw))

    def add(d: date | None, label: str):
        if d is not None and today <= d <= end and (d, label) not in result.dates:
            result.dates.append((d, label))

    # 1) 연도가 있는 날짜: 2026-12-15, 2026.03~2026.12(기간이면 시작·종료일)
    dated: list[tuple[date, bool]] = []  # (날짜, 월 단위인지)
    for m in _FULL_DATE.finditer(raw):
        try:
            dated.append((date(int(m.group(1)), int(m.group(2)), int(m.group(3))), False))
        except ValueError:
            pass
    for m in _YEAR_MONTH.finditer(_FULL_DATE.sub(" ", raw)):
        if 1 <= int(m.group(2)) <= 12:
            dated.append((date(int(m.group(1)), int(m.group(2)), 1), True))
    dated.sort()
    if "~" in raw and len(dated) >= 2:
        add(dated[0][0], "시작")
        add(_month_end(dated[-1][0]) if dated[-1][1] else dated[-1][0], "종료")
    else:
        for d, monthly in dated:
            add(_month_end(d) if monthly and deadline else d, "마감" if deadline else "")
    rest = _YEAR_MONTH.sub(" ", _FULL_DATE.sub(" ", raw))

    # 2) 반복 주기
    if _DAILY.search(rest):
        result.cadence = "daily"
    elif _WEEKLY.search(rest):
        result.cadence = "weekly"
    elif _QUARTERLY.search(rest):
        result.cadence = "quarterly"
    elif _MONTHLY.search(rest):
        result.cadence = "monthly"
    elif _YEARLY.search(rest):
        result.cadence = "yearly"

    # 3) 연도 없는 월·일(11월 14일, 11/14): 반복이 아니면 다음에 오는 그날 한 번, 정기업무 장이면 매년 반복으로도 본다
    if result.cadence in (None, "yearly"):
        for m in list(_MONTH_DAY.finditer(rest)) + list(_SLASH_DAY.finditer(rest)):
            month, day = int(m.group(1)), int(m.group(2))
            if 1 <= month <= 12 and 1 <= day <= 31:
                add(_next_occurrence(today, month, day), "마감" if deadline else "")
                if recurring and result.cadence is None and not deadline:
                    result.cadence = "yearly"

    # 4) 날짜로도 주기로도 못 푼 문구: 빠뜨리지 않게 정기 업무 '수시·기타'로(정기업무 장에서 'N월 중'이면 매년)
    if result.cadence is None and (not result.dates or _OCCASIONAL.search(rest)):
        result.cadence = "yearly" if recurring and not _OCCASIONAL.search(rest) and re.search(r"\d{1,2}\s*월", rest) else "other"
    result.dates.sort()
    return result


# ---------------------------------------------------------------- 인수인계서 → 캘린더


def routine_months(cadence: str, text: str) -> list[int]:
    """분기·반기·매년 업무가 몇 월에 해당하는지. 비어 있으면 매달(매일·매주·매월·수시) 또는 시기를 모르는 것."""
    if cadence not in ("quarterly", "yearly"):
        return []
    listed = sorted({int(n) for m in _MONTH_LIST.finditer(text) for n in re.findall(r"\d{1,2}", m.group(1)) if 1 <= int(n) <= 12})
    if listed:
        return listed
    if cadence == "yearly":
        return []
    early = re.search(r"초|첫|종료\s*후|이후|익월|다음\s*달", text) is not None
    if "반기" in text:
        if "상반기" in text and "하반기" not in text:
            return [1] if early else [6]
        if "하반기" in text and "상반기" not in text:
            return [7] if early else [12]
        return [1, 7] if early else [6, 12]
    return [1, 4, 7, 10] if early else [3, 6, 9, 12]


def _field(item: dict, key: str) -> str:
    value = (item.get("fields") or {}).get(key, "")
    return value.strip() if isinstance(value, str) else ""


def schedule_sources(slots: dict) -> list[tuple[str, int, str, str, bool, bool]]:
    """(슬롯, 항목 순번, 업무명, 일정 문구, 반복 업무 장인지, 정기 업무로 남길지)."""
    out = []
    for slot, key, recurring, keep_routine in (("regular", "timing", True, True), ("leave_tasks", "schedule", False, True)):
        for i, item in enumerate((slots.get(slot) or {}).get("items") or []):
            name, text = _field(item, "name") or (item.get("title") or "").strip(), _field(item, key)
            if name and text:
                out.append((slot, i, name, text, recurring, keep_routine))
    return out


GANTT_SOURCES = (("overview", "period"), ("office_plans", "period"))  # 일정 명세표(월별 막대)에 그리는 장과 계획기간 칸


def gantt_spans(slots: dict, base_year: int) -> list[tuple[str, int, str, str, date | None, date | None]]:
    """일정 명세표의 막대: (슬롯, 항목 순번, 업무명, 계획기간, 시작일, 종료일). 막대가 끊기면 구간마다 따로.
    '상시·매월'처럼 일정표 전체에 막대가 그려지는 행은 시작·종료일 없이(None) 한 번 돌려준다(정기 업무로 넣는다)."""
    out = []
    for slot, key in GANTT_SOURCES:
        for i, item in enumerate((slots.get(slot) or {}).get("items") or []):
            name, text = _field(item, "name") or (item.get("title") or "").strip(), _field(item, key)
            if not (name and text):
                continue
            span, always = plan_span(text, base_year)
            if always:
                out.append((slot, i, name, text, None, None))
                continue
            months = sorted(y * 12 + m - 1 for y, m in span)
            runs: list[list[int]] = []
            for n in months:
                if runs and n == runs[-1][1] + 1:
                    runs[-1][1] = n
                else:
                    runs.append([n, n])
            for a, b in runs:
                out.append((slot, i, name, text, date(a // 12, a % 12 + 1, 1), _month_end(date(b // 12, b % 12 + 1, 1))))
    return out


def _base_year(row: HandoverSession, today: date) -> int:
    m = re.match(r"(20\d{2})", row.handover_date or "")
    return int(m.group(1)) if m else today.year


def _key(prefix: str, *parts: object) -> str:
    return prefix + hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[: 40 - len(prefix)]


def plan_for(row: HandoverSession, today: date) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str, str]]]:
    """인수인계서 하나에서 넣을 (key, 날짜, 내용) 일정과 (key, 주기, 업무명, 수행 시기) 정기 업무."""
    dated: list[tuple[str, str, str]] = []
    routines: list[tuple[str, str, str, str]] = []
    for slot, index, name, text, recurring, keep_routine in schedule_sources(row.slots):
        parsed = parse_schedule(text, today, recurring=recurring)
        for d, label in parsed.dates:
            content = f"{PREFIX} {name}" + (f" · {label}" if label else "")
            dated.append((_key("ho", row.id, slot, index, d.isoformat(), label), d.isoformat(), content[:200]))
        if parsed.cadence and keep_routine:
            routines.append((_key("hr", row.id, slot, index), parsed.cadence, name[:200], text[:200]))
    # 상시·매월 막대(일정표 전체): 보통 정기업무 장의 수행 시기와 같은 내용이라, 그 장에서 아무것도 못 찾았을 때만 문구로 넣는다
    always: list[tuple[str, int, str, str]] = []
    for slot, index, name, text, start, end in gantt_spans(row.slots, _base_year(row, today)):
        if start is None or end is None:
            always.append((slot, index, name, text))
            continue
        for d, label in ((start, "시작"), (end, "종료")):
            if d <= today + timedelta(days=HORIZON_DAYS):
                dated.append((_key("ho", row.id, slot, index, d.isoformat(), label), d.isoformat(), f"{PREFIX} {name} · {label}"[:200]))
    if not routines and not any(key.startswith("ho") for key, _, _ in dated):
        for slot, index, name, text in always:
            parsed = parse_schedule(text, today, recurring=True)
            for d, label in parsed.dates:
                dated.append((_key("ho", row.id, slot, index, d.isoformat(), label), d.isoformat(), (f"{PREFIX} {name}" + (f" · {label}" if label else ""))[:200]))
            routines.append((_key("hr", row.id, slot, index), parsed.cadence or "other", name[:200], text[:200]))
    dated.sort(key=lambda e: e[1])
    return dated, routines


def source_title(row: HandoverSession) -> str:
    return (row.task_name or row.title or "인수인계서")[:200]


def add_handover_schedule(db: Session, recipient: User, row: HandoverSession, today: date | None = None) -> ScheduleResult:
    """인수자 캘린더에 인수인계서의 일정·정기 업무를 넣는다(commit 은 부르는 쪽에서). 이미 있는 id 는 건너뛴다."""
    dated, routines = plan_for(row, today or date.today())
    result = ScheduleResult()
    if not dated and not routines:
        return result
    have_events = set(db.scalars(select(PlannerEvent.key).where(PlannerEvent.user_id == recipient.id, PlannerEvent.key.in_([e[0] for e in dated] or [""]))))
    have_routines = set(db.scalars(select(PlannerRoutine.key).where(PlannerRoutine.user_id == recipient.id, PlannerRoutine.key.in_([r[0] for r in routines] or [""]))))
    room = PLANNER_LIMIT - (db.scalar(select(func.count(PlannerEvent.id)).where(PlannerEvent.user_id == recipient.id)) or 0)
    for key, day, text in dated:
        if key in have_events:
            result.skipped += 1
        elif result.events < room:
            db.add(PlannerEvent(user_id=recipient.id, key=key, date=day, text=text))
            result.events += 1
    source = source_title(row)
    for key, cadence, name, timing in routines:
        if key in have_routines:
            result.skipped += 1
        else:
            months = ",".join(str(m) for m in routine_months(cadence, timing))
            db.add(PlannerRoutine(user_id=recipient.id, key=key, cadence=cadence, name=name, timing=timing, months=months, source=source, session_id=row.id))
            result.routines += 1
    return result


def summary_message(result: ScheduleResult, name: str) -> str:
    if result.events or result.routines:
        parts = [f"일정 {result.events}건"] if result.events else []
        if result.routines:
            parts.append(f"정기 업무 {result.routines}건")
        return f"{name}님 캘린더에 {' · '.join(parts)}을 넣었습니다."
    if result.skipped:
        return "이 인수인계서의 일정은 이미 캘린더에 있습니다."
    return "인수인계서에서 캘린더에 넣을 일정을 찾지 못했습니다."
