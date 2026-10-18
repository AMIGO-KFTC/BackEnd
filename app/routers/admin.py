"""관리자 API(관리자 페이지용). users.is_admin 이 참인 계정만 쓸 수 있다(require_admin).

관리자 권한은 처음에 AMIGO_ADMIN_LOGIN_IDS 로 주고(서버를 켤 때 반영), 그 뒤에는 관리자가 이 API 로 주고 뺀다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import HandoverSession, UnitTask, User
from ..schemas import AdminResetOut, AdminRoleIn, AdminUserOut, AdminUserResult, AdminUsersOut
from ..services.accounts import issue_reset_token
from ..services.container import Services
from .deps import as_utc, get_db, get_services, require_admin, user_out

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
