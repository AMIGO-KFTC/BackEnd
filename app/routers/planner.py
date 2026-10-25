"""캘린더 일정과 할 일(투두): 로그인 계정마다 저장해 다른 브라우저·기기에서도 같은 값을 본다.

홈 대시보드의 작은 캘린더와 왼쪽 탭의 '캘린더' 화면이 같이 쓴다(FrontEnd src/shared/planner.ts).
id 는 화면이 만들어 보낼 수 있고(계정 안에서만 유일), 같은 id 로 다시 보내면 새로 만들지 않고 있던 값을 돌려준다.
그래서 화면은 서버 응답을 기다리지 않고 바로 그리고, 실패하면 다시 보내도 두 번 쌓이지 않는다.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..models import PlannerEvent, PlannerTodo, User
from ..schemas import (
    PlannerCleared,
    PlannerEventIn,
    PlannerEventOut,
    PlannerEventResult,
    PlannerImportIn,
    PlannerOut,
    PlannerTodoIn,
    PlannerTodoOut,
    PlannerTodoResult,
    PlannerTodoUpdate,
    Result,
)
from .deps import current_user, get_db

router = APIRouter(prefix="/api/planner", tags=["planner"])

MAX_EVENTS = 5000  # 계정당 저장할 수 있는 수(실수로 끝없이 쌓이지 않게)
MAX_TODOS = 5000
TOO_MANY = "더 저장할 수 없습니다. 지난 일정이나 완료한 할 일을 지운 뒤 다시 시도해 주세요."


def _event_out(row: PlannerEvent) -> PlannerEventOut:
    return PlannerEventOut(id=row.key, date=row.date, text=row.text)


def _todo_out(row: PlannerTodo) -> PlannerTodoOut:
    return PlannerTodoOut(id=row.key, text=row.text, done=bool(row.done), date=row.date)


def _planner(db: Session, user: User, message: str = "") -> PlannerOut:
    events = db.scalars(select(PlannerEvent).where(PlannerEvent.user_id == user.id).order_by(PlannerEvent.id)).all()
    todos = db.scalars(select(PlannerTodo).where(PlannerTodo.user_id == user.id).order_by(PlannerTodo.id)).all()
    return PlannerOut(message=message, events=[_event_out(e) for e in events], todos=[_todo_out(t) for t in todos])


def _find(db: Session, model, user: User, key: str):
    return db.scalar(select(model).where(model.user_id == user.id, model.key == key))


def _count(db: Session, model, user: User) -> int:
    return db.scalar(select(func.count(model.id)).where(model.user_id == user.id)) or 0


def _add_event(db: Session, user: User, body: PlannerEventIn) -> tuple[PlannerEvent, bool]:
    existing = _find(db, PlannerEvent, user, body.id) if body.id else None
    if existing is not None:
        return existing, False
    row = PlannerEvent(user_id=user.id, key=body.id or uuid.uuid4().hex, date=body.date, text=body.text)
    db.add(row)
    return row, True


def _add_todo(db: Session, user: User, body: PlannerTodoIn) -> tuple[PlannerTodo, bool]:
    existing = _find(db, PlannerTodo, user, body.id) if body.id else None
    if existing is not None:
        return existing, False
    row = PlannerTodo(user_id=user.id, key=body.id or uuid.uuid4().hex, text=body.text, done=body.done, date=body.date)
    db.add(row)
    return row, True


@router.get("", response_model=PlannerOut, summary="내 캘린더 일정과 할 일 전체")
def get_planner(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _planner(db, user)


@router.post("/events", response_model=PlannerEventResult, status_code=201, summary="일정 추가(같은 id 면 있던 일정을 돌려준다)")
def add_event(body: PlannerEventIn, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if _count(db, PlannerEvent, user) >= MAX_EVENTS and not (body.id and _find(db, PlannerEvent, user, body.id)):
        raise HTTPException(status_code=409, detail=TOO_MANY)
    row, created = _add_event(db, user, body)
    db.commit()
    if not created:
        response.status_code = 200
    return PlannerEventResult(message="일정을 추가했습니다." if created else "이미 있는 일정입니다.", event=_event_out(row))


@router.delete("/events/{event_id}", response_model=Result, summary="일정 삭제")
def delete_event(event_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = _find(db, PlannerEvent, user, event_id)
    if row is None:
        raise HTTPException(status_code=404, detail="일정을 찾을 수 없습니다.")
    db.delete(row)
    db.commit()
    return Result(message="일정을 지웠습니다.")


@router.post("/todos", response_model=PlannerTodoResult, status_code=201, summary="할 일 추가(같은 id 면 있던 할 일을 돌려준다)")
def add_todo(body: PlannerTodoIn, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if _count(db, PlannerTodo, user) >= MAX_TODOS and not (body.id and _find(db, PlannerTodo, user, body.id)):
        raise HTTPException(status_code=409, detail=TOO_MANY)
    row, created = _add_todo(db, user, body)
    db.commit()
    if not created:
        response.status_code = 200
    return PlannerTodoResult(message="할 일을 추가했습니다." if created else "이미 있는 할 일입니다.", todo=_todo_out(row))


@router.post("/todos/clear-done", response_model=PlannerCleared, summary="완료한 할 일 모두 지우기")
def clear_done(user: User = Depends(current_user), db: Session = Depends(get_db)):
    result = db.execute(delete(PlannerTodo).where(PlannerTodo.user_id == user.id, PlannerTodo.done.is_(True)))
    db.commit()
    return PlannerCleared(message="완료한 할 일을 지웠습니다.", removed=result.rowcount or 0)


@router.patch("/todos/{todo_id}", response_model=PlannerTodoResult, summary="할 일 수정(보낸 필드만: 내용 · 완료 여부 · 날짜)")
def update_todo(todo_id: str, body: PlannerTodoUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = _find(db, PlannerTodo, user, todo_id)
    if row is None:
        raise HTTPException(status_code=404, detail="할 일을 찾을 수 없습니다.")
    fields = body.model_fields_set
    if "text" in fields and body.text is not None:
        row.text = body.text
    if "done" in fields and body.done is not None:
        row.done = body.done
    if "date" in fields:
        row.date = body.date
    db.commit()
    return PlannerTodoResult(message="할 일을 고쳤습니다.", todo=_todo_out(row))


@router.delete("/todos/{todo_id}", response_model=Result, summary="할 일 삭제")
def delete_todo(todo_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = _find(db, PlannerTodo, user, todo_id)
    if row is None:
        raise HTTPException(status_code=404, detail="할 일을 찾을 수 없습니다.")
    db.delete(row)
    db.commit()
    return Result(message="할 일을 지웠습니다.")


@router.post("/import", response_model=PlannerOut, summary="브라우저에 저장해 둔 일정·할 일 옮기기(한 번만, 같은 id 는 건너뜀)")
def import_planner(body: PlannerImportIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    added = 0
    for event in body.events:
        added += _add_event(db, user, event)[1]
        db.flush()  # 같은 요청 안에 id 가 겹쳐도 _find 가 앞의 행을 보도록
    for todo in body.todos:
        added += _add_todo(db, user, todo)[1]
        db.flush()
    if _count(db, PlannerEvent, user) > MAX_EVENTS or _count(db, PlannerTodo, user) > MAX_TODOS:
        db.rollback()
        raise HTTPException(status_code=409, detail=TOO_MANY)
    db.commit()
    return _planner(db, user, f"{added}개를 옮겼습니다." if added else "옮길 새 항목이 없습니다.")
