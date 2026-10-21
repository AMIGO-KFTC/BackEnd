"""구성원 목록(인수자 선택용)과 받은 인수인계(내가 인수자로 지정된 인수인계).

인계자가 단위업무·세션을 만들 때 인수자의 로그인 아이디(successor_login_id)를 적어 두면, 그 계정의 '받은 인수인계' 목록에 나타난다.
인수자는 그 세션을 읽기만(상태·문서·PDF 내려받기·근거) 할 수 있다(routers/deps.py 의 guard_session_owner).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import HandoverSession, User, UserProfile
from ..schemas import MemberOut, MembersResult, ReceivedHandoverOut, ReceivedHandoversResult
from .deps import current_user, get_db

router = APIRouter(prefix="/api", tags=["handovers"])


def member_out(user: User) -> MemberOut:
    p = user.profile
    return MemberOut(
        id=user.id,
        login_id=user.login_id,
        name=user.name,
        role=user.role or "member",
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


def received_out(row: HandoverSession, owner_login_id: str = "") -> ReceivedHandoverOut:
    return ReceivedHandoverOut(
        id=row.id,
        title=row.title,
        task_name=row.task_name,
        mode=row.mode or "reassignment",
        owner_name=row.owner_name,
        owner_login_id=owner_login_id,
        organization=row.organization,
        position=row.position,
        handover_date=row.handover_date,
        stage=row.stage,
        status=row.status,
        document_version=row.document_version,
        file_count=sum(1 for s in row.sources if s.kind == "file"),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/members", response_model=MembersResult, summary="구성원 목록(프로필을 입력한 계정, 인수자 선택용)")
def list_members(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(User).join(UserProfile, UserProfile.user_id == User.id).order_by(UserProfile.department, UserProfile.team, User.name)
    ).all()
    return MembersResult(members=[member_out(u) for u in rows])


@router.get("/handovers/received", response_model=ReceivedHandoversResult, summary="받은 인수인계: 내가 인수자로 지정된 인수인계(최근 순)")
def list_received(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(HandoverSession)
        .where(func.lower(HandoverSession.successor_login_id) == user.login_id.lower())
        .order_by(HandoverSession.updated_at.desc())
    ).all()
    owners = owner_login_ids(db, rows)
    return ReceivedHandoversResult(handovers=[received_out(r, owners.get(r.user_id, "")) for r in rows])
