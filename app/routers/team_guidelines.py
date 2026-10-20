"""팀 인수인계서 지침: 관리자가 '우리 팀 인수인계서에 꼭 들어가야 하는 항목'과 '팀 특징·작성 지침'을 적는다.

지침은 소속(프로필의 부서 + 팀) 단위로 저장하고, 그 소속의 인수인계 세션이 AI 분석을 시작할 때 에이전트 프롬프트에 반영된다
(app/services/runner.py → AI 의 prompts.team_guidelines_block). 이미 분석을 마친 세션에는 다음 분석(다시 분석)부터 적용된다.

관리자 권한: 아직 사용자 역할이 없어, AMIGO_TEAM_GUIDELINE_EDITORS(쉼표로 구분한 로그인 아이디)를 정하면 그 계정만 저장할 수 있다.
비워 두면(기본) 로그인한 누구나 자기 소속의 지침을 저장할 수 있으므로, 운영할 때는 꼭 정해 두어야 한다.
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import TeamGuideline, User
from ..schemas import TeamGuidelineIn, TeamGuidelineItem, TeamGuidelineOut
from ..services.container import Services
from ..services.guidelines import organization_key, prompt_preview, stored_items
from .deps import current_user, get_db, get_services

router = APIRouter(prefix="/api/team-guidelines", tags=["team-guidelines"])

NO_ORGANIZATION = "프로필에 부서·팀이 없어 팀 지침을 쓸 수 없습니다. 먼저 프로필을 입력해 주세요."
NOT_EDITOR = "팀 지침을 수정할 권한이 없습니다. 관리자에게 요청해 주세요."


def _organization(user: User) -> str:
    organization = organization_key(user.profile.organization) if user.profile else ""
    if not organization:
        raise HTTPException(status_code=400, detail=NO_ORGANIZATION)
    return organization


def _editable(user: User, settings: Settings) -> bool:
    editors = settings.team_guideline_editor_ids
    return not editors or user.login_id.lower() in editors


def _out(row: TeamGuideline | None, organization: str, editable: bool, message: str = "") -> TeamGuidelineOut:
    items = [TeamGuidelineItem(**item) for item in stored_items(row)]
    return TeamGuidelineOut(
        message=message,
        organization=organization,
        items=items,
        notes=row.notes if row else "",
        updated_at=row.updated_at if row else None,
        updated_by=row.updated_by if row else "",
        editable=editable,
        prompt_preview=prompt_preview(row),
    )


@router.get("/me", response_model=TeamGuidelineOut, summary="내 소속(부서 + 팀)의 인수인계서 지침 조회")
def get_my_guidelines(user: User = Depends(current_user), db: Session = Depends(get_db), services: Services = Depends(get_services)):
    organization = _organization(user)
    return _out(db.get(TeamGuideline, organization), organization, _editable(user, services.settings))


@router.put("/me", response_model=TeamGuidelineOut, summary="내 소속의 인수인계서 지침 저장(항목과 팀 특징·작성 지침)")
def save_my_guidelines(
    body: TeamGuidelineIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
):
    organization = _organization(user)
    if not _editable(user, services.settings):
        raise HTTPException(status_code=403, detail=NOT_EDITOR)
    seen: set[str] = set()
    items: list[dict] = []
    for item in body.items:
        key = " ".join(item.name.split()).lower()
        if not key:
            continue
        if key in seen:
            raise HTTPException(status_code=422, detail=f"같은 이름의 항목이 둘 이상 있습니다: {item.name}")
        seen.add(key)
        items.append({"id": item.id or uuid.uuid4().hex[:8], "name": " ".join(item.name.split()), "instruction": item.instruction})
    row = db.get(TeamGuideline, organization) or TeamGuideline(organization=organization)
    row.items_json = json.dumps(items, ensure_ascii=False)
    row.notes = body.notes
    row.updated_by = user.name
    db.add(row)
    db.commit()
    db.refresh(row)
    return _out(row, organization, True, "팀 지침이 저장되었습니다. 이후 AI 분석부터 반영돼요.")
