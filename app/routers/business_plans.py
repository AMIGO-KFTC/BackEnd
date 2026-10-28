"""주요 사업계획(경영정보시스템 주요 업무보고 단위) 조회: 팀장(원내 양식) 인수인계서의 인계자 정보 입력 화면이 사업계획을 고르는 데 쓴다."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..models import User
from ..schemas import BusinessPlansOut
from ..services.business_plans import team_plans
from .deps import current_user

router = APIRouter(prefix="/api/business-plans", tags=["business-plans"])


@router.get("/me", response_model=BusinessPlansOut, summary="내 소속(본부·부 + 팀)의 주요 사업계획 목록(프로필이 없거나 목록에 없는 팀이면 빈 목록)")
def my_business_plans(user: User = Depends(current_user)):
    profile = user.profile
    division, team = (profile.department, profile.team) if profile is not None else ("", "")
    return BusinessPlansOut(division=division, team=team, plans=team_plans(division, team))
