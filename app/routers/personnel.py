"""정기인사 일정: 팀장(관리자)이 등록하고, 홈·캘린더 화면이 다가오는 일정을 D-day 로 보여 준다.

- 1년에 여러 번(상·하반기, 수시 인사 등) 등록할 수 있다. 발표일은 비워도 되고 발령일은 꼭 있어야 한다.
- 범위는 언제나 팀(등록한 팀장의 소속 '부서 팀')이다. 구성원은 자기 소속 일정만 본다(예전에 전사로 만든 일정은 더 보이지 않는다).
- 조회 GET /api/personnel-schedules 는 로그인한 누구나, 추가·수정·삭제 /api/admin/personnel-schedules 는 관리자만.
  관리자는 자기 팀 일정만 고치고 지울 수 있다(다른 팀 일정은 보이지 않는다).
- 등록된 일정이 없으면 화면은 예전처럼 매년 10/26 발표 · 10/28 발령을 기본값으로 쓴다(FrontEnd src/shared/personnel.ts).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PersonnelSchedule, User
from ..schemas import PersonnelScheduleIn, PersonnelScheduleOut, PersonnelScheduleResult, PersonnelSchedulesOut, Result
from ..services.guidelines import organization_key
from .deps import current_user, get_db, require_admin

router = APIRouter(prefix="/api", tags=["personnel"])

MAX_SCHEDULES = 200  # 소속별 상한(실수로 끝없이 쌓이지 않게)


def _organization(user: User) -> str:
    return organization_key(user.profile.organization) if user.profile else ""


def _out(row: PersonnelSchedule) -> PersonnelScheduleOut:
    return PersonnelScheduleOut(
        id=row.id,
        name=row.name,
        announce_date=row.announce_date or None,
        appoint_date=row.appoint_date,
        organization=row.organization,
        created_by=row.created_by_name,
    )


def _visible(db: Session, user: User) -> list[PersonnelSchedule]:
    org = _organization(user)
    if not org:  # 소속이 없으면 볼 팀 일정도 없다
        return []
    rows = db.scalars(
        select(PersonnelSchedule)
        .where(PersonnelSchedule.organization == org)
        .order_by(PersonnelSchedule.appoint_date, PersonnelSchedule.id)
    )
    return list(rows)


def _team_org(user: User) -> str:
    org = _organization(user)
    if not org:
        raise HTTPException(status_code=400, detail="프로필에 부서·팀이 없어 팀 일정을 만들 수 없습니다. 프로필을 먼저 채워 주세요.")
    return org


def _editable_row(db: Session, user: User, schedule_id: int) -> PersonnelSchedule:
    row = db.get(PersonnelSchedule, schedule_id)
    if row is None or not row.organization or row.organization != _organization(user):
        raise HTTPException(status_code=404, detail="정기인사 일정을 찾을 수 없습니다.")
    return row


@router.get("/personnel-schedules", response_model=PersonnelSchedulesOut, summary="정기인사 일정(내 소속, 발령일 순)")
def list_schedules(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = _visible(db, user)
    return PersonnelSchedulesOut(schedules=[_out(r) for r in rows], organization=_organization(user), editable=bool(user.is_admin))


@router.post("/admin/personnel-schedules", response_model=PersonnelScheduleResult, status_code=201, summary="(관리자) 정기인사 일정 추가")
def add_schedule(body: PersonnelScheduleIn, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    org = _team_org(user)
    if len(db.scalars(select(PersonnelSchedule.id).where(PersonnelSchedule.organization == org)).all()) >= MAX_SCHEDULES:
        raise HTTPException(status_code=409, detail="더 등록할 수 없습니다. 지난 일정을 지운 뒤 다시 시도해 주세요.")
    row = PersonnelSchedule(
        name=body.name,
        announce_date=body.announce_date or "",
        appoint_date=body.appoint_date,
        organization=org,
        created_by=user.id,
        created_by_name=user.name,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return PersonnelScheduleResult(message=f"'{row.name}' 일정을 등록했습니다.", schedule=_out(row))


@router.put("/admin/personnel-schedules/{schedule_id}", response_model=PersonnelScheduleResult, summary="(관리자) 정기인사 일정 수정")
def update_schedule(schedule_id: int, body: PersonnelScheduleIn, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _editable_row(db, user, schedule_id)
    row.name = body.name
    row.announce_date = body.announce_date or ""
    row.appoint_date = body.appoint_date
    db.commit()
    return PersonnelScheduleResult(message=f"'{row.name}' 일정을 고쳤습니다.", schedule=_out(row))


@router.delete("/admin/personnel-schedules/{schedule_id}", response_model=Result, summary="(관리자) 정기인사 일정 삭제")
def delete_schedule(schedule_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _editable_row(db, user, schedule_id)
    name = row.name
    db.delete(row)
    db.commit()
    return Result(message=f"'{name}' 일정을 지웠습니다.")
