"""팀 공유 일정: 팀장(관리자)이 팀 관리 > '일정 관리'에서 워크숍·회의·마감 같은 일정을 등록하면 구성원의 홈·캘린더에 함께 나온다.

- 하루짜리는 시작일만, 여러 날이면 끝나는 날까지 적는다. 메모(장소·준비물 등)는 비워도 된다.
- 범위와 권한은 정기인사 일정(personnel.py)과 같다: 언제나 등록한 팀장의 소속 '부서 팀' 단위.
  조회 GET /api/team-schedules 는 로그인한 누구나(자기 소속 일정만), 추가·수정·삭제 /api/admin/team-schedules 는 관리자만(자기 팀 일정만).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import TeamSchedule, User
from ..schemas import Result, TeamScheduleIn, TeamScheduleOut, TeamScheduleResult, TeamSchedulesOut
from ..services.guidelines import organization_key
from .deps import current_user, get_db, require_admin

router = APIRouter(prefix="/api", tags=["team-schedules"])

MAX_SCHEDULES = 500  # 소속별 상한(실수로 끝없이 쌓이지 않게)


def _organization(user: User) -> str:
    return organization_key(user.profile.organization) if user.profile else ""


def _out(row: TeamSchedule) -> TeamScheduleOut:
    return TeamScheduleOut(
        id=row.id,
        title=row.title,
        start_date=row.start_date,
        end_date=row.end_date or None,
        memo=row.memo,
        organization=row.organization,
        created_by=row.created_by_name,
    )


def _visible(db: Session, user: User) -> list[TeamSchedule]:
    org = _organization(user)
    if not org:  # 소속이 없으면 볼 팀 일정도 없다
        return []
    rows = db.scalars(
        select(TeamSchedule)
        .where(TeamSchedule.organization == org)
        .order_by(TeamSchedule.start_date, TeamSchedule.id)
    )
    return list(rows)


def _team_org(user: User) -> str:
    org = _organization(user)
    if not org:
        raise HTTPException(status_code=400, detail="프로필에 부서·팀이 없어 팀 일정을 만들 수 없습니다. 프로필을 먼저 채워 주세요.")
    return org


def _editable_row(db: Session, user: User, schedule_id: int) -> TeamSchedule:
    row = db.get(TeamSchedule, schedule_id)
    if row is None or not row.organization or row.organization != _organization(user):
        raise HTTPException(status_code=404, detail="팀 일정을 찾을 수 없습니다.")
    return row


def _apply(row: TeamSchedule, body: TeamScheduleIn) -> None:
    row.title = body.title
    row.start_date = body.start_date
    row.end_date = body.end_date or ""
    row.memo = body.memo


@router.get("/team-schedules", response_model=TeamSchedulesOut, summary="팀 공유 일정(내 소속, 시작일 순)")
def list_schedules(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = _visible(db, user)
    return TeamSchedulesOut(schedules=[_out(r) for r in rows], organization=_organization(user), editable=bool(user.is_admin))


@router.post("/admin/team-schedules", response_model=TeamScheduleResult, status_code=201, summary="(관리자) 팀 공유 일정 추가")
def add_schedule(body: TeamScheduleIn, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    org = _team_org(user)
    if len(db.scalars(select(TeamSchedule.id).where(TeamSchedule.organization == org)).all()) >= MAX_SCHEDULES:
        raise HTTPException(status_code=409, detail="더 등록할 수 없습니다. 지난 일정을 지운 뒤 다시 시도해 주세요.")
    row = TeamSchedule(organization=org, created_by=user.id, created_by_name=user.name)
    _apply(row, body)
    db.add(row)
    db.commit()
    db.refresh(row)
    return TeamScheduleResult(message=f"'{row.title}' 일정을 등록했습니다.", schedule=_out(row))


@router.put("/admin/team-schedules/{schedule_id}", response_model=TeamScheduleResult, summary="(관리자) 팀 공유 일정 수정")
def update_schedule(schedule_id: int, body: TeamScheduleIn, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _editable_row(db, user, schedule_id)
    _apply(row, body)
    db.commit()
    return TeamScheduleResult(message=f"'{row.title}' 일정을 고쳤습니다.", schedule=_out(row))


@router.delete("/admin/team-schedules/{schedule_id}", response_model=Result, summary="(관리자) 팀 공유 일정 삭제")
def delete_schedule(schedule_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = _editable_row(db, user, schedule_id)
    title = row.title
    db.delete(row)
    db.commit()
    return Result(message=f"'{title}' 일정을 지웠습니다.")
