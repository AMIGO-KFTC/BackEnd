"""직제규정 시행세칙(업무분장) 조회: 인계자 정보 입력 화면의 본부·팀 선택과 단위업무 목록에 쓴다."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..models import User
from ..schemas import OrgDutiesResult
from ..services.org_duties import org_duties
from .deps import current_user

router = APIRouter(prefix="/api", tags=["org-duties"])


@router.get("/org-duties", response_model=OrgDutiesResult, summary="직제규정 시행세칙: 본부·부 → 팀 → 단위업무 목록(인계자 정보 입력용)")
def get_org_duties(user: User = Depends(current_user)):
    return OrgDutiesResult(**org_duties())
