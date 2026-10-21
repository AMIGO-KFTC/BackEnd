"""관리자용 팀 인수인계 현황: 팀원별 인수인계서 작성 수행 기록과 성과를 DB 에서 모아 계산한다.

팀은 프로필의 (부서, 팀명) 으로 묶는다. 한 팀원의 단위업무마다 인수인계 세션이 하나씩 있으므로
세션(단계·빈 항목·장별 충족도·사용량), 자료, 대화 메시지, 인수인계서 버전을 세션 단위로 읽어 팀원·팀 합계를 낸다.

성과 지표
- 작성 완료율: 인수인계서가 만들어진 단위업무 / 전체 단위업무
- 질문 응답률: 답한 질문 / (답한 + 건너뛴 질문)
- 항목 충족률: 분석이 끝난 단위업무의 장별 충족도 평균(충분 1, 부분 0.5, 부족 0)
- 첫 문서까지 걸린 시간: 단위업무 등록부터 첫 인수인계서(v1)까지(시간)
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import DocumentVersion, HandoverSession, Message, Source, UnitTask, User, UserProfile

COVERAGE_SCORE = {"sufficient": 1.0, "partial": 0.5, "missing": 0.0}
ANSWERED = ("answered", "resolved")
STAGE_GROUP = {  # 화면에 보여 줄 진행 단계 묶음
    "setup": "preparing",
    "analyzing": "analyzing",
    "summary": "answering",
    "qna": "answering",
    "composing": "answering",
    "review": "completed",
}


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _ratio(num: float, den: float) -> float | None:
    return round(num / den, 3) if den else None


@dataclass
class TaskFacts:
    """단위업무(세션) 하나의 수행 기록."""

    task: UnitTask
    row: HandoverSession
    source_count: int = 0
    answer_count: int = 0
    ai_versions: int = 0
    user_versions: int = 0
    first_document_at: datetime | None = None
    last_activity_at: datetime | None = None
    gaps: Counter = field(default_factory=Counter)
    coverage: float | None = None

    @property
    def has_document(self) -> bool:
        return bool(self.row.document_md)

    @property
    def hours_to_first_document(self) -> float | None:
        if self.first_document_at is None:
            return None
        return round(max((self.first_document_at - _utc(self.task.created_at)).total_seconds(), 0) / 3600, 1)

    def summary(self) -> dict[str, Any]:
        row = self.row
        answered = sum(self.gaps[s] for s in ANSWERED)
        return {
            "task_id": self.task.id,
            "session_id": row.id,
            "name": self.task.name,
            "mode": row.mode or "reassignment",
            "stage": row.stage,
            "stage_group": STAGE_GROUP.get(row.stage, "preparing"),
            "status": row.status,
            "document_version": row.document_version,
            "has_document": self.has_document,
            "source_count": self.source_count,
            "answer_count": self.answer_count,
            "questions_answered": answered,
            "questions_skipped": self.gaps["skipped"],
            "questions_open": self.gaps["open"] + self.gaps["asked"],
            "coverage_rate": self.coverage,
            "ai_versions": self.ai_versions,
            "user_versions": self.user_versions,
            "hours_to_first_document": self.hours_to_first_document,
            "cost_usd": round(row.cost_usd or 0.0, 4),
            "created_at": _utc(self.task.created_at),
            "last_activity_at": self.last_activity_at,
        }


def _coverage(row: HandoverSession) -> float | None:
    if row.stage == "setup":
        return None
    scores = [COVERAGE_SCORE.get(str(slot.get("coverage")), 0.0) for slot in row.slots.values() if isinstance(slot, dict)]
    return round(sum(scores) / len(scores), 3) if scores else None


def collect_tasks(db: Session, users: list[User], date_from: datetime | None = None, date_to: datetime | None = None) -> dict[int, list[TaskFacts]]:
    """팀원들의 단위업무별 수행 기록. date_from·date_to 는 단위업무 등록일 범위."""
    if not users:
        return {}
    query = select(UnitTask).where(UnitTask.user_id.in_([u.id for u in users])).order_by(UnitTask.sort_order, UnitTask.created_at)
    if date_from is not None:
        query = query.where(UnitTask.created_at >= date_from)
    if date_to is not None:
        query = query.where(UnitTask.created_at <= date_to)
    tasks = list(db.scalars(query))
    facts = {t.session_id: TaskFacts(task=t, row=t.session) for t in tasks}
    ids = list(facts)
    if ids:
        for sid, n, last in db.execute(select(Source.session_id, func.count(Source.id), func.max(Source.created_at)).where(Source.session_id.in_(ids)).group_by(Source.session_id)):
            facts[sid].source_count = n
            facts[sid].last_activity_at = _utc(last)
        answers = select(Message.session_id, func.count(Message.id), func.max(Message.created_at)).where(
            Message.session_id.in_(ids), Message.role == "user"
        )
        for sid, n, last in db.execute(answers.group_by(Message.session_id)):
            facts[sid].answer_count = n
            facts[sid].last_activity_at = max(filter(None, [facts[sid].last_activity_at, _utc(last)]))
        versions = select(DocumentVersion.session_id, DocumentVersion.origin, func.count(DocumentVersion.id), func.min(DocumentVersion.created_at), func.max(DocumentVersion.created_at))
        for sid, origin, n, first, last in db.execute(versions.where(DocumentVersion.session_id.in_(ids)).group_by(DocumentVersion.session_id, DocumentVersion.origin)):
            f = facts[sid]
            if origin == "ai":
                f.ai_versions = n
            else:
                f.user_versions += n
            f.first_document_at = min(filter(None, [f.first_document_at, _utc(first)]))
            f.last_activity_at = max(filter(None, [f.last_activity_at, _utc(last)]))
    by_user: dict[int, list[TaskFacts]] = defaultdict(list)
    for f in facts.values():
        f.gaps = Counter(str(g.get("status")) for g in f.row.gaps)
        f.coverage = _coverage(f.row)
        f.last_activity_at = max(filter(None, [f.last_activity_at, _utc(f.task.created_at)]))
        by_user[f.task.user_id].append(f)
    return by_user


def member_stats(user: User, tasks: list[TaskFacts]) -> dict[str, Any]:
    profile = user.profile
    total = len(tasks)
    documents = sum(1 for t in tasks if t.has_document)
    answered = sum(sum(t.gaps[s] for s in ANSWERED) for t in tasks)
    skipped = sum(t.gaps["skipped"] for t in tasks)
    waiting = sum(t.gaps["open"] + t.gaps["asked"] for t in tasks)
    coverages = [t.coverage for t in tasks if t.coverage is not None]
    hours = [t.hours_to_first_document for t in tasks if t.hours_to_first_document is not None]
    stages = Counter(STAGE_GROUP.get(t.row.stage, "preparing") for t in tasks)
    activity = [t.last_activity_at for t in tasks if t.last_activity_at]
    return {
        "user_id": user.id,
        "login_id": user.login_id,
        "name": user.name,
        "position": profile.position if profile else "",
        "is_admin": bool(user.is_admin),
        "last_login_at": _utc(user.last_login_at),
        "last_activity_at": max(activity) if activity else None,
        "task_count": total,
        "document_count": documents,
        "completion_rate": _ratio(documents, total),
        "stages": {k: stages.get(k, 0) for k in ("preparing", "analyzing", "answering", "completed")},
        "source_count": sum(t.source_count for t in tasks),
        "answer_count": sum(t.answer_count for t in tasks),
        "questions_answered": answered,
        "questions_skipped": skipped,
        "questions_open": waiting,
        "answer_rate": _ratio(answered, answered + skipped),
        "coverage_rate": round(sum(coverages) / len(coverages), 3) if coverages else None,
        "ai_versions": sum(t.ai_versions for t in tasks),
        "user_versions": sum(t.user_versions for t in tasks),
        "avg_hours_to_first_document": round(sum(hours) / len(hours), 1) if hours else None,
        "cost_usd": round(sum(t.row.cost_usd or 0.0 for t in tasks), 4),
    }


def team_totals(members: list[dict[str, Any]]) -> dict[str, Any]:
    tasks = sum(m["task_count"] for m in members)
    docs = sum(m["document_count"] for m in members)
    answered = sum(m["questions_answered"] for m in members)
    asked = sum(m["questions_answered"] + m["questions_skipped"] for m in members)
    coverages = [m["coverage_rate"] for m in members if m["coverage_rate"] is not None]
    hours = [m["avg_hours_to_first_document"] for m in members if m["avg_hours_to_first_document"] is not None]
    return {
        "member_count": len(members),
        "active_member_count": sum(1 for m in members if m["task_count"]),
        "task_count": tasks,
        "document_count": docs,
        "completion_rate": _ratio(docs, tasks),
        "answer_rate": _ratio(answered, asked),
        "coverage_rate": round(sum(coverages) / len(coverages), 3) if coverages else None,
        "avg_hours_to_first_document": round(sum(hours) / len(hours), 1) if hours else None,
        "user_versions": sum(m["user_versions"] for m in members),
        "cost_usd": round(sum(m["cost_usd"] for m in members), 4),
    }


def team_members(db: Session, department: str, team: str) -> list[User]:
    query = (
        select(User)
        .join(UserProfile, UserProfile.user_id == User.id)
        .where(UserProfile.department == department, UserProfile.team == team)
        .order_by(User.name, User.id)
    )
    return list(db.scalars(query))


def list_teams(db: Session) -> list[dict[str, Any]]:
    """프로필이 있는 회원을 (부서, 팀명) 으로 묶은 팀 목록과 간단한 현황."""
    rows = db.execute(
        select(UserProfile.department, UserProfile.team, func.count(UserProfile.user_id))
        .group_by(UserProfile.department, UserProfile.team)
        .order_by(UserProfile.department, UserProfile.team)
    ).all()
    tasks = dict(
        ((d, t), n)
        for d, t, n in db.execute(
            select(UserProfile.department, UserProfile.team, func.count(UnitTask.id))
            .join(UnitTask, UnitTask.user_id == UserProfile.user_id)
            .group_by(UserProfile.department, UserProfile.team)
        )
    )
    docs = dict(
        ((d, t), n)
        for d, t, n in db.execute(
            select(UserProfile.department, UserProfile.team, func.count(UnitTask.id))
            .join(UnitTask, UnitTask.user_id == UserProfile.user_id)
            .join(HandoverSession, HandoverSession.id == UnitTask.session_id)
            .where(HandoverSession.document_md != "")
            .group_by(UserProfile.department, UserProfile.team)
        )
    )
    return [
        {
            "department": d,
            "team": t,
            "label": " ".join(x for x in (d, t) if x),
            "member_count": n,
            "task_count": tasks.get((d, t), 0),
            "document_count": docs.get((d, t), 0),
            "completion_rate": _ratio(docs.get((d, t), 0), tasks.get((d, t), 0)),
        }
        for d, t, n in rows
    ]


ACTIVITY_LABELS = {
    "task": "단위업무 등록",
    "source": "자료 등록",
    "answer": "질문에 답변",
    "skip": "질문 건너뜀",
    "command": "문서 생성 요청",
    "document_ai": "AI 인수인계서 작성",
    "document_user": "인수인계서 직접 수정",
    "document_restore": "이전 버전 복원",
}


def member_activity(db: Session, tasks: list[TaskFacts], limit: int = 50) -> list[dict[str, Any]]:
    """팀원의 수행 기록(최근순): 단위업무 등록 · 자료 등록 · 답변 · 인수인계서 작성·수정. 답변 내용은 담지 않는다."""
    if not tasks:
        return []
    by_session = {t.row.id: t.task for t in tasks}
    events: list[dict[str, Any]] = []

    def add(kind: str, at: datetime | None, sid: str, detail: str = "") -> None:
        task = by_session[sid]
        events.append({"kind": kind, "label": ACTIVITY_LABELS[kind], "at": _utc(at), "task_id": task.id, "task_name": task.name, "detail": detail})

    for t in tasks:
        add("task", t.task.created_at, t.row.id)
    ids = list(by_session)
    for s in db.scalars(select(Source).where(Source.session_id.in_(ids)).order_by(Source.created_at.desc()).limit(limit)):
        add("source", s.created_at, s.session_id, s.name)
    user_kinds = {"answer": "answer", "skip": "skip", "command": "command"}
    for m in db.scalars(
        select(Message).where(Message.session_id.in_(ids), Message.role == "user", Message.kind.in_(user_kinds)).order_by(Message.id.desc()).limit(limit)
    ):
        add(user_kinds[m.kind], m.created_at, m.session_id)
    for v in db.scalars(select(DocumentVersion).where(DocumentVersion.session_id.in_(ids)).order_by(DocumentVersion.created_at.desc()).limit(limit)):
        add(f"document_{v.origin}" if v.origin in ("ai", "user", "restore") else "document_ai", v.created_at, v.session_id, f"v{v.version}" + (f" · {v.note}" if v.note else ""))
    events.sort(key=lambda e: e["at"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return events[:limit]
