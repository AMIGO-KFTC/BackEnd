"""마이페이지: 내 정보 조회 · 내 정보 수정 · 비밀번호 바꾸기.

내 정보(이름 · 부서 · 팀명 · 직위 · 이메일)를 바꾸면 이미 만든 단위업무 인수인계 세션의 인계자 정보에도 반영한다
(다음 분석·문서 생성부터 적용). 관리자 권한(is_admin)은 여기서 바꿀 수 없다(/api/admin).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import DocumentVersion, HandoverSession, UnitTask, User, UserProfile
from ..schemas import MyInfoUpdate, MyPageOut, MyStats, PasswordChangeIn, Result
from ..security import token_hash, verify_password
from ..services.accounts import set_password
from .deps import as_utc, current_user, get_db, request_token, user_out
from .transfers import transfer_counts

router = APIRouter(prefix="/api/me", tags=["mypage"])


def employee_no_taken(db: Session, employee_no: str, exclude_user_id: int | None = None) -> bool:
    if not employee_no:
        return False
    query = select(User.id).where(func.lower(User.employee_no) == employee_no.lower())
    if exclude_user_id is not None:
        query = query.where(User.id != exclude_user_id)
    return db.scalar(query) is not None


def email_taken(db: Session, email: str, exclude_user_id: int | None = None) -> bool:
    if not email:
        return False
    query = select(User.id).where(func.lower(User.email) == email.lower())
    if exclude_user_id is not None:
        query = query.where(User.id != exclude_user_id)
    return db.scalar(query) is not None


def mypage(db: Session, user: User, message: str = "") -> MyPageOut:
    owned = select(HandoverSession.id).where(HandoverSession.user_id == user.owner_key)
    stats = MyStats(
        unit_tasks=db.scalar(select(func.count(UnitTask.id)).where(UnitTask.user_id == user.id)) or 0,
        documents=db.scalar(select(func.count(HandoverSession.id)).where(HandoverSession.user_id == user.owner_key, HandoverSession.document_md != "")) or 0,
        document_versions=db.scalar(select(func.count(DocumentVersion.id)).where(DocumentVersion.session_id.in_(owned))) or 0,
        **transfer_counts(db, user),
    )
    return MyPageOut(
        message=message,
        user=user_out(user),
        created_at=as_utc(user.created_at),
        last_login_at=as_utc(user.last_login_at) if user.last_login_at else None,
        password_changed_at=as_utc(user.password_changed_at) if user.password_changed_at else None,
        stats=stats,
    )


@router.get("", response_model=MyPageOut, summary="마이페이지: 내 정보(이름·부서·팀명·직위·이메일·관리자 여부)와 작성 현황")
def get_me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return mypage(db, user)


@router.patch("", response_model=MyPageOut, summary="내 정보 수정(보낸 항목만 바꿈)")
def update_me(body: MyInfoUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="바꿀 항목을 하나 이상 보내 주세요.")
    if any(k in changes and changes[k] is None for k in ("name", "department", "position")):
        raise HTTPException(status_code=400, detail="이름·부서·직위는 비울 수 없습니다.")
    profile_fields = {"department", "team", "position"} & changes.keys()
    if profile_fields and user.profile is None and not {"department", "position"} <= changes.keys():
        raise HTTPException(status_code=400, detail="처음 입력할 때는 부서와 직위를 함께 보내 주세요.")
    if "email" in changes:
        email = (changes["email"] or "").lower()
        if email_taken(db, email, exclude_user_id=user.id):
            raise HTTPException(status_code=409, detail="다른 계정이 쓰고 있는 이메일입니다.")
        user.email = email
    if "employee_no" in changes:
        employee_no = changes["employee_no"] or ""
        if employee_no_taken(db, employee_no, exclude_user_id=user.id):
            raise HTTPException(status_code=409, detail="이미 다른 계정이 쓰고 있는 사번입니다.")
        user.employee_no = employee_no
    if "name" in changes:
        user.name = changes["name"]
    if profile_fields:
        profile = user.profile or UserProfile(user_id=user.id, team="")
        for key in profile_fields:
            setattr(profile, key, changes[key] or "")
        user.profile = profile
    if user.profile is not None and ({"name"} | profile_fields) & changes.keys():
        # 이미 만든 단위업무 인수인계서에도 바뀐 인계자 정보를 반영한다(다음 분석·문서 생성부터 적용)
        for row in db.scalars(select(HandoverSession).where(HandoverSession.user_id == user.owner_key)):
            row.owner_name, row.organization, row.position = user.name, user.profile.organization, user.profile.position
    db.commit()
    return mypage(db, user, "내 정보를 수정했습니다.")


@router.put("/password", response_model=Result, summary="비밀번호 바꾸기(지금 비밀번호 확인)")
def change_password(
    body: PasswordChangeIn,
    user: User = Depends(current_user),
    token: str = Depends(request_token),
    db: Session = Depends(get_db),
):
    """바꾸면 지금 쓰는 기기를 뺀 다른 기기의 로그인은 모두 끊긴다."""
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="지금 비밀번호가 올바르지 않습니다.")
    if body.new_password == body.current_password:
        raise HTTPException(status_code=400, detail="지금과 다른 비밀번호를 입력해 주세요.")
    set_password(db, user, body.new_password, keep_token_hash=token_hash(token) if token else None)
    db.commit()
    return Result(message="비밀번호를 바꿨습니다. 다른 기기에서는 다시 로그인해야 합니다.")
