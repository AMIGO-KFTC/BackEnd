"""팀 담당 업무 목록: 팀장이 '우리 팀에서 하는 업무'를 정해 두면, 구성원이 인수인계 1단계(기본 정보)에서 이 목록에서 담당 업무를 고른다.

목록은 소속(프로필의 부서 + 팀) 단위로 저장한다. 같은 소속이면 누구나 읽을 수 있고, 추가·수정·삭제는 관리자(users.is_admin = 팀장)만 한다.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import TeamDuty, User
from ..schemas import TeamDutiesOut, TeamDutyIn, TeamDutyOut, TeamDutyUpdate
from ..services.guidelines import organization_key
from .deps import as_utc, current_user, get_db

router = APIRouter(prefix="/api/team-duties", tags=["team-duties"])

MAX_DUTIES = 50
NO_ORGANIZATION = "프로필에 부서·팀이 없어 팀 업무 목록을 쓸 수 없습니다. 먼저 프로필을 입력해 주세요."
NOT_EDITOR = "팀 업무 목록은 팀 관리자만 고칠 수 있습니다."


def _organization(user: User) -> str:
    organization = organization_key(user.profile.organization) if user.profile else ""
    if not organization:
        raise HTTPException(status_code=400, detail=NO_ORGANIZATION)
    return organization


def _editor(user: User) -> str:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail=NOT_EDITOR)
    return _organization(user)


def _clean(name: str) -> str:
    return " ".join(name.split())


def _rows(db: Session, organization: str) -> list[TeamDuty]:
    query = select(TeamDuty).where(TeamDuty.organization == organization).order_by(TeamDuty.sort_order, TeamDuty.created_at)
    return list(db.scalars(query))


def _out(db: Session, user: User, organization: str, message: str = "") -> TeamDutiesOut:
    duties = [
        TeamDutyOut(id=r.id, name=r.name, description=r.description, sort_order=r.sort_order, updated_by=r.updated_by, updated_at=as_utc(r.updated_at) if r.updated_at else None)
        for r in _rows(db, organization)
    ]
    return TeamDutiesOut(message=message, organization=organization, duties=duties, editable=bool(user.is_admin))


def _name_taken(db: Session, organization: str, name: str, exclude_id: str = "") -> bool:
    query = select(TeamDuty.id).where(TeamDuty.organization == organization, func.lower(TeamDuty.name) == name.lower(), TeamDuty.id != exclude_id)
    return db.scalar(query) is not None


def _load(db: Session, organization: str, duty_id: str) -> TeamDuty:
    row = db.get(TeamDuty, duty_id)
    if row is None or row.organization != organization:
        raise HTTPException(status_code=404, detail="팀 업무를 찾을 수 없습니다.")
    return row


@router.get("/me", response_model=TeamDutiesOut, summary="내 소속(부서 + 팀)의 담당 업무 목록")
def list_my_duties(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _out(db, user, _organization(user))


@router.post("/me", response_model=TeamDutiesOut, status_code=status.HTTP_201_CREATED, summary="팀 업무 추가(팀 관리자)")
def create_duty(body: TeamDutyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    organization = _editor(user)
    name = _clean(body.name)
    rows = _rows(db, organization)
    if len(rows) >= MAX_DUTIES:
        raise HTTPException(status_code=422, detail=f"팀 업무는 {MAX_DUTIES}개까지 둘 수 있습니다.")
    if _name_taken(db, organization, name):
        raise HTTPException(status_code=409, detail=f"이미 있는 업무입니다: {name}")
    order = max((r.sort_order for r in rows), default=0) + 1
    db.add(TeamDuty(id=uuid.uuid4().hex, organization=organization, name=name, description=body.description, sort_order=order, updated_by=user.name))
    db.commit()
    return _out(db, user, organization, f"'{name}' 업무를 추가했습니다.")


@router.patch("/me/{duty_id}", response_model=TeamDutiesOut, summary="팀 업무 수정(이름·설명·순서, 팀 관리자)")
def update_duty(duty_id: str, body: TeamDutyUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    organization = _editor(user)
    row = _load(db, organization, duty_id)
    if body.name is not None:
        name = _clean(body.name)
        if _name_taken(db, organization, name, exclude_id=row.id):
            raise HTTPException(status_code=409, detail=f"이미 있는 업무입니다: {name}")
        row.name = name
    if body.description is not None:
        row.description = body.description
    if body.sort_order is not None:
        row.sort_order = body.sort_order
    row.updated_by = user.name
    db.commit()
    return _out(db, user, organization, "팀 업무를 수정했습니다.")


@router.delete("/me/{duty_id}", response_model=TeamDutiesOut, summary="팀 업무 삭제(팀 관리자). 이미 시작한 인수인계에는 영향이 없다")
def delete_duty(duty_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    organization = _editor(user)
    row = _load(db, organization, duty_id)
    name = row.name
    db.delete(row)
    db.commit()
    return _out(db, user, organization, f"'{name}' 업무를 삭제했습니다.")

