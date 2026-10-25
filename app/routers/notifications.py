"""알림: 받은 인수인계서(새로 도착 · 인계자가 고친 새 버전)를 알림으로 보여 주고, 확인(읽음)·지우기·모두 확인을 계정별로 저장한다.

알림 ID 는 'transfer:<전송 ID>:v<문서 버전>' 이다. 인계자가 보낸 뒤 문서를 고쳐 버전이 오르면 새 알림이 생긴다.
인수자가 받은 인수인계서를 이미 열어 봤으면(handover_transfers.read_at) 그 버전의 알림은 확인한 것으로 본다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import HandoverTransfer, NotificationState, User, utcnow
from ..schemas import NotificationIdsIn, NotificationOut, NotificationsOut
from .deps import as_utc, current_user, get_db

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

MAX_NOTIFICATIONS = 100


def _build(db: Session, user: User) -> list[NotificationOut]:
    states = {s.key: s for s in db.scalars(select(NotificationState).where(NotificationState.user_id == user.id))}
    transfers = db.scalars(select(HandoverTransfer).where(HandoverTransfer.recipient_id == user.id).order_by(HandoverTransfer.created_at.desc())).all()
    out: list[NotificationOut] = []
    for t in transfers:
        row = t.session
        version = row.document_version if row is not None and row.document_md else t.document_version
        key = f"transfer:{t.id}:v{version}"
        state = states.get(key)
        if state is not None and state.dismissed_at is not None:
            continue
        task = (row.task_name if row is not None else t.task_name) or t.title
        updated = version > t.document_version
        at = as_utc(row.updated_at) if updated and row is not None else as_utc(t.created_at)
        opened = t.read_at is not None and (not updated or (row is not None and as_utc(t.read_at) >= as_utc(row.updated_at)))
        out.append(
            NotificationOut(
                id=key,
                kind="updated" if updated else "received",
                session_id=row.id if row is not None else None,
                title="인수인계서가 수정됐어요" if updated else "새 인수인계서가 도착했어요",
                text=f"{t.sender_name}님이 ‘{task}’ 인수인계서를 {'고쳤어요. 바뀐 내용을 확인해 주세요.' if updated else '보냈어요. 문서를 확인해 주세요.'}",
                sender_name=t.sender_name,
                task_name=task,
                created_at=at,
                read=opened or (state is not None and state.read_at is not None),
            )
        )
    out.sort(key=lambda n: n.created_at, reverse=True)
    return out[:MAX_NOTIFICATIONS]


def _result(db: Session, user: User, message: str = "") -> NotificationsOut:
    items = _build(db, user)
    return NotificationsOut(message=message, notifications=items, unread=sum(1 for n in items if not n.read))


def _states(db: Session, user: User, keys: list[str]) -> list[NotificationState]:
    rows = []
    for key in dict.fromkeys(keys):
        state = db.get(NotificationState, (user.id, key))
        if state is None:
            state = NotificationState(user_id=user.id, key=key)
            db.add(state)
        rows.append(state)
    return rows


def _targets(db: Session, user: User, ids: list[str] | None) -> list[str]:
    known = {n.id for n in _build(db, user)}
    if ids is None:
        return list(known)
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise HTTPException(status_code=404, detail="알림을 찾을 수 없습니다.")
    return ids


@router.get("", response_model=NotificationsOut, summary="내 알림(받은 인수인계서 도착·수정, 최근 순)과 안 읽은 수")
def list_notifications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _result(db, user)


@router.post("/read", response_model=NotificationsOut, summary="알림 확인(읽음). ids 를 비우면(null) 모두 확인")
def read_notifications(body: NotificationIdsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    keys = _targets(db, user, body.ids)
    now = utcnow()
    for state in _states(db, user, keys):
        state.read_at = state.read_at or now
    db.commit()
    return _result(db, user, "모든 알림을 확인했어요." if body.ids is None else "")


@router.post("/dismiss", response_model=NotificationsOut, summary="알림 지우기. ids 를 비우면(null) 모두 지움(받은 인수인계서는 그대로)")
def dismiss_notifications(body: NotificationIdsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    keys = _targets(db, user, body.ids)
    now = utcnow()
    for state in _states(db, user, keys):
        state.read_at = state.read_at or now
        state.dismissed_at = now
    db.commit()
    return _result(db, user, f"알림 {len(keys)}건을 지웠어요.")
