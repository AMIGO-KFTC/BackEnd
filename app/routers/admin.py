"""관리자 API(관리자 페이지용). users.is_admin 이 참인 계정만 쓸 수 있다(require_admin).

- 회원: 목록 · 관리자 권한 주기/빼기 · 비밀번호 재설정 토큰 발급
- 팀 인수인계 현황: 팀 목록 → 팀원별 작성 수행 기록과 성과(완료율·응답률·충족률·소요 시간) → 팀원 상세(단위업무별 진행·최근 수행 기록)
- 우리 팀(관리자와 같은 소속 = 프로필의 부서 + 팀): 구성원 목록(인수인계 건수) · 팀 인수인계 목록(인수자·질문 수) · 구성원 삭제
  (팀장이 쓰는 '팀 관리' 화면용. 소속 비교는 공백·가운뎃점 차이를 무시한다)

관리자 권한은 users.is_admin 컬럼이다. 처음 관리자는 DB 에서 직접 지정하고(README 참고), 그 뒤에는 관리자가 이 API 로 주고 뺀다.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import HandoverSession, UnitTask, User, UserProfile
from ..schemas import (
    AdminHandoverOut,
    AdminHandoversResult,
    AdminMemberOut,
    AdminMembersResult,
    AdminResetOut,
    AdminRoleIn,
    AdminUserOut,
    AdminUserResult,
    AdminUsersOut,
    MemberReportOut,
    Result,
    TeamReportOut,
    TeamsOut,
)
from ..services.accounts import issue_reset_token
from ..services.container import Services
from ..services.guidelines import organization_key
from ..services.reports import collect_tasks, list_teams, member_activity, member_stats, team_members, team_totals
from .deps import as_utc, get_db, get_services, require_admin, user_out
from .handovers import member_out, received_out
from .sessions import remove_handover_session

router = APIRouter(prefix="/api/admin", tags=["admin"])


def admin_user_out(db: Session, user: User) -> AdminUserOut:
    tasks = db.scalar(select(func.count(UnitTask.id)).where(UnitTask.user_id == user.id)) or 0
    docs = db.scalar(select(func.count(HandoverSession.id)).where(HandoverSession.user_id == user.owner_key, HandoverSession.document_md != "")) or 0
    return AdminUserOut(
        **user_out(user).model_dump(),
        created_at=as_utc(user.created_at),
        last_login_at=as_utc(user.last_login_at) if user.last_login_at else None,
        unit_task_count=tasks,
        document_count=docs,
    )


def _load_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="회원을 찾을 수 없습니다.")
    return user


@router.get("/users", response_model=AdminUsersOut, summary="회원 목록(관리자)")
def list_users(
    q: str = Query(default="", max_length=50, description="아이디·이름·이메일 검색"),
    admin_only: bool = Query(default=False, description="관리자만"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = select(User)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        query = query.where(or_(User.login_id.like(like), func.lower(User.name).like(like), func.lower(User.email).like(like)))
    if admin_only:
        query = query.where(User.is_admin.is_(True))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    users = db.scalars(query.order_by(User.id).offset((page - 1) * size).limit(size))
    return AdminUsersOut(users=[admin_user_out(db, u) for u in users], total=total)


@router.patch("/users/{user_id}/role", response_model=AdminUserResult, summary="관리자 권한 주기·빼기(관리자)")
def set_role(user_id: int, body: AdminRoleIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = _load_user(db, user_id)
    if user.id == admin.id and not body.is_admin:
        raise HTTPException(status_code=400, detail="자기 자신의 관리자 권한은 뺄 수 없습니다. 다른 관리자에게 요청해 주세요.")
    user.is_admin = body.is_admin
    db.commit()
    message = f"{user.name}({user.login_id})님에게 관리자 권한을 주었습니다." if body.is_admin else f"{user.name}({user.login_id})님의 관리자 권한을 뺐습니다."
    return AdminUserResult(message=message, user=admin_user_out(db, user))


@router.post("/users/{user_id}/password-reset", response_model=AdminResetOut, summary="비밀번호 재설정 토큰 발급(관리자)")
def admin_reset(user_id: int, _: User = Depends(require_admin), db: Session = Depends(get_db), services: Services = Depends(get_services)):
    """이메일이 없거나 메일을 받을 수 없는 회원을 위해 관리자가 재설정 토큰을 발급해 직접 전달한다(한 번만 보여 줌)."""
    user = _load_user(db, user_id)
    token, reset = issue_reset_token(db, user, services.settings.password_reset_ttl_minutes, requested_by="admin")
    db.commit()
    return AdminResetOut(
        message=f"{user.name}님의 비밀번호 재설정 토큰을 발급했습니다. {services.settings.password_reset_ttl_minutes}분 안에 전달해 주세요.",
        reset_token=token,
        reset_url=services.reset_notifier.url(token),
        expires_at=as_utc(reset.expires_at),
    )


KST = timezone(timedelta(hours=9))


def _period(date_from: date | None, date_to: date | None) -> tuple[datetime | None, datetime | None]:
    """화면의 날짜(한국 시간)를 DB 비교용 UTC 로 바꾼다(SQLite 는 시간대 없이 저장하므로 naive UTC)."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="시작일이 종료일보다 늦습니다.")
    start = datetime.combine(date_from, time.min, KST).astimezone(timezone.utc).replace(tzinfo=None) if date_from else None
    end = datetime.combine(date_to, time.max, KST).astimezone(timezone.utc).replace(tzinfo=None) if date_to else None
    return start, end


@router.get("/teams", response_model=TeamsOut, summary="팀 목록(관리자): 부서·팀명별 팀원 수와 작성 현황")
def teams(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return TeamsOut(teams=list_teams(db))


@router.get("/teams/report", response_model=TeamReportOut, summary="팀 인수인계 현황(관리자): 팀원별 작성 수행 기록과 성과")
def team_report(
    department: str = Query(min_length=1, max_length=100, description="부서(팀 목록의 department)"),
    team: str = Query(default="", max_length=100, description="팀명(팀 목록의 team, 팀이 없으면 비움)"),
    date_from: date | None = Query(default=None, description="이 날짜 이후 등록한 단위업무만(YYYY-MM-DD)"),
    date_to: date | None = Query(default=None, description="이 날짜까지 등록한 단위업무만(YYYY-MM-DD)"),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    members = team_members(db, department, team)
    if not members:
        raise HTTPException(status_code=404, detail="해당 팀의 팀원이 없습니다.")
    start, end = _period(date_from, date_to)
    tasks = collect_tasks(db, members, start, end)
    stats = [member_stats(user, tasks.get(user.id, [])) for user in members]
    label = " ".join(x for x in (department, team) if x)
    return TeamReportOut(
        message=f"{label} 팀원 {len(members)}명의 인수인계 현황입니다.",
        department=department,
        team=team,
        label=label,
        date_from=date_from,
        date_to=date_to,
        summary=team_totals(stats),
        members=stats,
    )


@router.get("/members/{user_id}/report", response_model=MemberReportOut, summary="팀원 상세(관리자): 단위업무별 진행과 최근 수행 기록")
def member_report(
    user_id: int,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = _load_user(db, user_id)
    start, end = _period(date_from, date_to)
    tasks = collect_tasks(db, [user], start, end).get(user.id, [])
    profile = user.profile
    return MemberReportOut(
        member=member_stats(user, tasks),
        department=profile.department if profile else "",
        team=profile.team if profile else "",
        tasks=[t.summary() for t in tasks],
        activity=member_activity(db, tasks),
    )


# ---------------------------------------------------------------- 우리 팀(관리자와 같은 소속) 구성원·인수인계

NO_ORGANIZATION = "프로필에 부서·팀이 없어 팀을 알 수 없습니다. 먼저 프로필을 입력해 주세요."


def own_team_members(db: Session, admin: User) -> tuple[str, list[User]]:
    """관리자와 같은 소속(부서 + 팀)의 구성원(관리자 본인 포함). 관리자 먼저, 그다음 이름순."""
    organization = organization_key(admin.profile.organization) if admin.profile else ""
    if not organization:
        raise HTTPException(status_code=400, detail=NO_ORGANIZATION)
    rows = db.scalars(select(User).join(UserProfile, UserProfile.user_id == User.id).order_by(User.name)).all()
    members = [u for u in rows if organization_key(u.profile.organization) == organization]
    members.sort(key=lambda u: (0 if u.is_admin else 1, u.name))
    return organization, members


def own_team_sessions(db: Session, members: list[User]) -> list[HandoverSession]:
    keys = [u.owner_key for u in members]
    if not keys:
        return []
    return db.scalars(select(HandoverSession).where(HandoverSession.user_id.in_(keys)).order_by(HandoverSession.updated_at.desc())).all()


@router.get("/members", response_model=AdminMembersResult, summary="우리 팀 구성원과 인수인계 건수(관리자)")
def list_team_members(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    organization, members = own_team_members(db, admin)
    sessions = own_team_sessions(db, members)
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


@router.get("/handovers", response_model=AdminHandoversResult, summary="우리 팀 구성원이 인계자인 인수인계 목록(관리자, 최근 순)")
def list_team_handovers(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    organization, members = own_team_members(db, admin)
    logins = {u.owner_key: u.login_id for u in members}
    out = []
    for row in own_team_sessions(db, members):
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


@router.delete("/members/{user_id}", response_model=Result, summary="우리 팀 구성원 삭제(관리자. 그 구성원의 인수인계·자료·문서도 함께)")
def delete_team_member(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db), services: Services = Depends(get_services)):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="자기 자신은 삭제할 수 없습니다.")
    _, members = own_team_members(db, admin)
    target = next((u for u in members if u.id == user_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="우리 팀에서 그 구성원을 찾을 수 없습니다.")
    sessions = own_team_sessions(db, [target])
    if any(services.runner.is_busy(s.id) for s in sessions):
        raise HTTPException(status_code=409, detail="그 구성원의 인수인계를 AI 가 처리 중이라 지금은 삭제할 수 없습니다.")
    name = target.name
    for row in sessions:
        remove_handover_session(db, services, row)
    db.delete(target)  # 프로필·로그인 세션·단위업무·읽음 위치는 FK cascade
    db.commit()
    return Result(message=f"{name}님을 구성원에서 삭제했습니다.")
