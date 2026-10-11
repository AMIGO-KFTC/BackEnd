"""프로필 입력: 부서 · 직위 · 팀 · 이름."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import HandoverSession, User, UserProfile
from ..schemas import ProfileIn, ProfileResult
from .deps import current_user, get_db, profile_out

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("", response_model=ProfileResult, summary="내 프로필 조회")
def get_profile(user: User = Depends(current_user)):
    profile = profile_out(user)
    if profile is None:
        raise HTTPException(status_code=404, detail="아직 입력한 프로필이 없습니다.")
    return ProfileResult(profile=profile)


@router.put("", response_model=ProfileResult, summary="프로필 입력·수정(부서·직위·팀·이름)")
def save_profile(body: ProfileIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    profile = user.profile or UserProfile(user_id=user.id)
    profile.department, profile.team, profile.position = body.department, body.team, body.position
    user.name = body.name
    user.profile = profile
    # 이미 만든 단위업무 인수인계서에도 바뀐 인계자 정보를 반영한다(다음 분석·문서 생성부터 적용)
    for row in db.scalars(select(HandoverSession).where(HandoverSession.user_id == user.owner_key)):
        row.owner_name, row.organization, row.position = user.name, profile.organization, profile.position
    db.commit()
    return ProfileResult(message="프로필이 저장되었습니다.", profile=profile_out(user))
