"""관리자(팀장) API: 우리 팀 구성원과 팀의 인수인계 현황.

- 역할은 users.role 로 구분한다(admin = 팀 관리자, member = 일반 구성원). 회원가입은 member 로 만들어지고,
  관리자는 DB 에서 지정한다(scripts/seed_accounts.py 참고).
- '우리 팀'은 관리자 프로필의 소속(부서 + 팀)과 같은 소속의 구성원이다(공백·가운뎃점 차이는 무시).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import HandoverSession, User, UserProfile
from ..schemas import AdminHandoverOut, AdminHandoversResult, AdminMemberOut, AdminMembersResult, Result
from ..services.container import Services
from ..services.guidelines import organization_key
from .deps import admin_user, get_db, get_services
from .handovers import member_out, received_out
from .sessions import remove_handover_session

router = APIRouter(prefix="/api/admin", tags=["admin"])

NO_ORGANIZATION = "프로필에 부서·팀이 없어 팀을 알 수 없습니다. 먼저 프로필을 입력해 주세요."


def team_members(db: Session, admin: User) -> tuple[str, list[User]]:
    """관리자와 같은 소속(부서 + 팀)의 구성원(관리자 본인 포함)."""
    organization = organization_key(admin.profile.organization) if admin.profile else ""
    if not organization:
        raise HTTPException(status_code=400, detail=NO_ORGANIZATION)
    rows = db.scalars(select(User).join(UserProfile, UserProfile.user_id == User.id).order_by(User.name)).all()
    members = [u for u in rows if organization_key(u.profile.organization) == organization]
    members.sort(key=lambda u: (0 if u.is_admin else 1, u.name))
    return organization, members


def team_sessions(db: Session, members: list[User]) -> list[HandoverSession]:
    keys = [u.owner_key for u in members]
    if not keys:
        return []
    return db.scalars(select(HandoverSession).where(HandoverSession.user_id.in_(keys)).order_by(HandoverSession.updated_at.desc())).all()


@router.get("/members", response_model=AdminMembersResult, summary="우리 팀 구성원과 인수인계 건수")
def list_team_members(admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    organization, members = team_members(db, admin)
    sessions = team_sessions(db, members)
    out = []
    for u in members:
        mine = [s for s in sessions if s.user_id == u.owner_key]
        out.append(
            AdminMemberOut(
                **member_out(u).model_dump(),
                handover_count=len(mine),
                completed_count=sum(1 for s in mine if s.document_version > 0),
                last_login_at=u.last_login_at,
            )
        )
    return AdminMembersResult(organization=organization, members=out)


@router.get("/handovers", response_model=AdminHandoversResult, summary="우리 팀 구성원이 인계자인 인수인계 목록(최근 순)")
def list_team_handovers(admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    organization, members = team_members(db, admin)
    logins = {u.owner_key: u.login_id for u in members}
    out = []
    for row in team_sessions(db, members):
        gaps = row.gaps
        out.append(
            AdminHandoverOut(
                **received_out(row, logins.get(row.user_id, "")).model_dump(),
                successor=row.successor,
                successor_login_id=row.successor_login_id or "",
                question_count=row.question_count,
                open_gap_count=sum(1 for g in gaps if g.get("status") == "open"),
            )
        )
    return AdminHandoversResult(organization=organization, handovers=out)


@router.delete("/members/{user_id}", response_model=Result, summary="우리 팀 구성원 삭제(그 구성원의 인수인계·자료·문서도 함께)")
def delete_team_member(user_id: int, admin: User = Depends(admin_user), db: Session = Depends(get_db), services: Services = Depends(get_services)):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="자기 자신은 삭제할 수 없습니다.")
    _, members = team_members(db, admin)
    target = next((u for u in members if u.id == user_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="우리 팀에서 그 구성원을 찾을 수 없습니다.")
    sessions = team_sessions(db, [target])
    if any(services.runner.is_busy(s.id) for s in sessions):
        raise HTTPException(status_code=409, detail="그 구성원의 인수인계를 AI 가 처리 중이라 지금은 삭제할 수 없습니다.")
    name = target.name
    for row in sessions:
        remove_handover_session(db, services, row)
    db.delete(target)  # 프로필·로그인 세션·단위업무·읽음 위치는 FK cascade
    db.commit()
    return Result(message=f"{name}님을 구성원에서 삭제했습니다.")
