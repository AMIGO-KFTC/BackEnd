"""구성원 목록(인수자 선택용)과 받은 인수인계(내가 인수자로 지정된 인수인계).

인계자가 단위업무·세션을 만들 때 적어 둔 인수자(successor_login_id)는 '예정'일 뿐이다. 인계자가 완성 문서 화면에서 받는 사람을 골라 전송해야
(POST /api/sessions/{id}/transfers) 그 계정의 '받은 인수인계' 목록에 나타난다(여러 명에게 보낼 수 있다).
받은 사람은 그 세션을 읽기만(상태·문서·PDF 내려받기·근거) 할 수 있다(routers/deps.py 의 guard_session_owner).
문서는 언제나 인계자의 최신 버전이다. 인계자가 '직접 수정하기'로 고쳐 저장하면(PUT /document) 받은 인수인계에도 고친 문서가 뜨고,
목록의 document_origin(ai | user | restore)·document_updated_at 으로 직접 고친 버전인지와 고친 시각을 알 수 있다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import DocumentVersion, HandoverSession, HandoverTransfer, User, UserProfile
from ..schemas import HandoverCalendarResult, MemberOut, MembersResult, ReceivedHandoverOut, ReceivedHandoversResult
from ..services.documents import ORIGIN_LABELS
from ..services.handover_calendar import add_handover_schedule, summary_message
from .deps import as_utc, current_user, get_db

router = APIRouter(prefix="/api", tags=["handovers"])


def member_out(user: User) -> MemberOut:
    p = user.profile
    return MemberOut(
        id=user.id,
        login_id=user.login_id,
        name=user.name,
        is_admin=bool(user.is_admin),
        department=p.department if p else "",
        team=p.team if p else "",
        position=p.position if p else "",
        organization=p.organization if p else "",
    )


def owner_login_ids(db: Session, rows: list[HandoverSession]) -> dict[str, str]:
    """세션 소유자(user:N) → 로그인 아이디."""
    ids = {int(r.user_id.split(":", 1)[1]) for r in rows if r.user_id.startswith("user:")}
    if not ids:
        return {}
    pairs = db.execute(select(User.id, User.login_id).where(User.id.in_(ids))).all()
    return {f"user:{uid}": login_id for uid, login_id in pairs}


def latest_versions(db: Session, session_ids: list[str]) -> dict[str, DocumentVersion]:
    """세션별 마지막 인수인계서 버전(현재 문서)."""
    if not session_ids:
        return {}
    last = (
        select(DocumentVersion.session_id, func.max(DocumentVersion.version).label("v"))
        .where(DocumentVersion.session_id.in_(session_ids))
        .group_by(DocumentVersion.session_id)
        .subquery()
    )
    rows = db.scalars(select(DocumentVersion).join(last, (DocumentVersion.session_id == last.c.session_id) & (DocumentVersion.version == last.c.v)))
    return {v.session_id: v for v in rows}


def duty_names(row: HandoverSession) -> list[str]:
    """세션의 담당 업무 이름 목록. duties 는 한 줄에 업무 하나('업무명' 또는 '업무명: 설명')이고, 단위 업무명(task_name)을 맨 앞에 둔다."""
    names: list[str] = [row.task_name.strip()] if row.task_name and row.task_name.strip() else []
    for line in (row.duties or "").splitlines():
        name = line.split(": ", 1)[0].strip()
        if name and name not in names:
            names.append(name)
    return names


def received_out(row: HandoverSession, owner_login_id: str = "", latest: DocumentVersion | None = None) -> ReceivedHandoverOut:
    return ReceivedHandoverOut(
        id=row.id,
        title=row.title,
        task_name=row.task_name,
        duties=duty_names(row),
        mode=row.mode or "reassignment",
        leave_start=row.leave_start or "",
        leave_end=row.leave_end or "",
        owner_name=row.owner_name,
        owner_login_id=owner_login_id,
        organization=row.organization,
        position=row.position,
        handover_date=row.handover_date,
        stage=row.stage,
        status=row.status,
        document_version=row.document_version,
        file_count=sum(1 for s in row.sources if s.kind == "file"),
        document_origin=latest.origin if latest is not None and row.document_md else "",
        document_origin_label=ORIGIN_LABELS.get(latest.origin, "") if latest is not None and row.document_md else "",
        document_updated_at=as_utc(latest.created_at) if latest is not None and row.document_md else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/members", response_model=MembersResult, summary="구성원 목록(프로필을 입력한 계정, 인수자 선택용)")
def list_members(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(User).join(UserProfile, UserProfile.user_id == User.id).order_by(UserProfile.department, UserProfile.team, User.name)
    ).all()
    return MembersResult(members=[member_out(u) for u in rows])


@router.get("/handovers/received", response_model=ReceivedHandoversResult, summary="받은 인수인계: 인계자가 나에게 전송한 인수인계(최근 순)")
def list_received(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(HandoverSession)
        .where(HandoverSession.id.in_(select(HandoverTransfer.session_id).where(HandoverTransfer.recipient_id == user.id)))
        .order_by(HandoverSession.updated_at.desc())
    ).all()
    owners = owner_login_ids(db, rows)
    latest = latest_versions(db, [r.id for r in rows])
    return ReceivedHandoversResult(handovers=[received_out(r, owners.get(r.user_id, ""), latest.get(r.id)) for r in rows])


@router.post("/handovers/received/{session_id}/calendar", response_model=HandoverCalendarResult, summary="받은 인수인계서의 정기업무·마감일을 내 캘린더에 넣기(같은 일정은 건너뜀)")
def add_received_to_calendar(session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    received = db.scalar(select(HandoverTransfer.id).where(HandoverTransfer.session_id == session_id, HandoverTransfer.recipient_id == user.id).limit(1))
    row = db.get(HandoverSession, session_id) if received else None
    if row is None:
        raise HTTPException(status_code=404, detail="받은 인수인계서를 찾을 수 없습니다.")
    result = add_handover_schedule(db, user, row)
    db.commit()
    return HandoverCalendarResult(message=summary_message(result, user.name), events=result.events, routines=result.routines, skipped=result.skipped)
