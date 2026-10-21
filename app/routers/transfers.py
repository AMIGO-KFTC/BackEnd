"""인수인계서 전송: 인계자가 인수자 이름을 입력해 자기 인수인계서를 보내고, 인수자는 마이페이지 '인수받은 내용'에서 확인한다.

- 인계자: POST /api/sessions/{session_id}/transfers  {"recipient_name": "이서연", "message": "..."}
  이름이 같은 회원이 여럿이면 409 와 후보(detail.candidates)를 돌려주고, 화면에서 고른 recipient_id 로 다시 보낸다.
  보낸 시점의 문서를 복사해 두므로 나중에 문서를 고쳐도 인수자가 받은 문서는 그대로다(고친 문서는 다시 보내면 새 전송).
- 인수자: GET /api/me/received(인수 현황) → GET /api/me/received/{id}(산출물 보기, 처음 열면 읽음 처리) → 내려받기
- 인계자: GET /api/sessions/{session_id}/transfers · GET /api/me/sent(보낸 기록과 읽음 여부)
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import HandoverTransfer, Message, User, UserProfile, utcnow
from ..schemas import RecipientCandidate, RecipientSearchOut, TransferDetail, TransferIn, TransferResult, TransfersOut, TransferSummary
from ..services.exporters import to_docx, to_pdf
from .deps import as_utc, current_user, get_db, load_session
from .documents import MEDIA_TYPES

session_router = APIRouter(prefix="/api/sessions/{session_id}", tags=["transfers"])
router = APIRouter(prefix="/api", tags=["transfers"])


def candidate(user: User) -> RecipientCandidate:
    p = user.profile
    return RecipientCandidate(
        user_id=user.id,
        name=user.name,
        login_id=user.login_id,
        department=p.department if p else "",
        team=p.team if p else "",
        position=p.position if p else "",
    )


def summary(t: HandoverTransfer) -> TransferSummary:
    return TransferSummary(
        id=t.id,
        title=t.title,
        task_name=t.task_name,
        mode=t.mode,
        document_version=t.document_version,
        sender=candidate(t.sender),
        recipient=candidate(t.recipient),
        handover_date=t.handover_date,
        message=t.message,
        created_at=as_utc(t.created_at),
        read_at=as_utc(t.read_at) if t.read_at else None,
        is_read=t.read_at is not None,
    )


def _users_named(db: Session, name: str, exclude_id: int) -> list[User]:
    return list(db.scalars(select(User).where(User.name == name, User.id != exclude_id).order_by(User.id)))


@router.get("/users/recipients", response_model=RecipientSearchOut, summary="인수자 찾기(이름 일부로 회원 검색)")
def search_recipients(
    q: str = Query(min_length=1, max_length=50, description="이름(일부도 됨)"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """인수자 이름을 입력할 때 같은 이름을 구별할 수 있도록 소속·직위를 함께 보여 준다(본인 제외, 최대 20명)."""
    like = f"%{q.strip()}%"
    users = db.scalars(
        select(User).outerjoin(UserProfile, UserProfile.user_id == User.id).where(User.name.like(like), User.id != user.id).order_by(User.name, User.id).limit(20)
    )
    return RecipientSearchOut(candidates=[candidate(u) for u in users])


@session_router.post("/transfers", response_model=TransferResult, status_code=status.HTTP_201_CREATED, summary="인수인계서를 인수자에게 전송(인수자 이름 입력)")
def send_transfer(session_id: str, body: TransferIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = load_session(db, session_id)
    if row.user_id != user.owner_key:
        raise HTTPException(status_code=403, detail="본인이 작성한 인수인계서만 보낼 수 있습니다.")
    if not row.document_md:
        raise HTTPException(status_code=400, detail="아직 인수인계서가 없습니다. 문서를 만든 뒤 보내 주세요.")
    if body.recipient_id is not None:
        recipient = db.get(User, body.recipient_id)
        if recipient is None:
            raise HTTPException(status_code=404, detail="인수자를 찾을 수 없습니다.")
    else:
        if not body.recipient_name:
            raise HTTPException(status_code=400, detail="인수자 이름을 입력해 주세요.")
        matches = _users_named(db, body.recipient_name, exclude_id=user.id)
        if not matches:
            if body.recipient_name == user.name:
                raise HTTPException(status_code=400, detail="본인에게는 보낼 수 없습니다.")
            raise HTTPException(status_code=404, detail=f"'{body.recipient_name}' 이름의 회원을 찾을 수 없습니다. 인수자가 가입했는지 확인해 주세요.")
        if len(matches) > 1:  # 동명이인: 화면이 후보 중에서 골라 recipient_id 로 다시 보낸다
            candidates = [candidate(u).model_dump() for u in matches]
            message = f"'{body.recipient_name}' 이름의 회원이 {len(matches)}명입니다. 인수자를 골라 주세요."
            return JSONResponse(status_code=409, content={"success": False, "message": message, "detail": {"candidates": candidates}})
        recipient = matches[0]
    if recipient.id == user.id:
        raise HTTPException(status_code=400, detail="본인에게는 보낼 수 없습니다.")

    transfer = HandoverTransfer(
        id=uuid.uuid4().hex,
        session_id=row.id,
        sender_id=user.id,
        recipient_id=recipient.id,
        title=row.title,
        task_name=row.task_name,
        mode=row.mode or "reassignment",
        sender_name=row.owner_name or user.name,
        sender_organization=row.organization,
        sender_position=row.position,
        handover_date=row.handover_date,
        document_version=row.document_version,
        markdown=row.document_md,
        message=body.message,
    )
    db.add(transfer)
    if not row.successor:  # 인수자 칸이 비어 있으면 보낸 사람으로 채운다
        row.successor = recipient.name
    notice = f"인수인계서 v{row.document_version}을(를) {recipient.name}님에게 보냈습니다."
    db.add(Message(session_id=row.id, role="system", kind="info", content=notice, meta_json=json.dumps({"transfer_id": transfer.id}, ensure_ascii=False)))
    db.commit()
    db.refresh(transfer)
    return TransferResult(message=notice, transfer=summary(transfer))


@session_router.get("/transfers", response_model=TransfersOut, summary="이 인수인계서를 보낸 기록(받는 사람·읽음 여부)")
def session_transfers(session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = load_session(db, session_id)
    if row.user_id != user.owner_key:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    rows = list(db.scalars(select(HandoverTransfer).where(HandoverTransfer.session_id == row.id).order_by(HandoverTransfer.created_at.desc())))
    return TransfersOut(transfers=[summary(t) for t in rows], total=len(rows))


@router.get("/me/sent", response_model=TransfersOut, summary="내가 보낸 인수인계서(받는 사람·읽음 여부)")
def sent(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = list(db.scalars(select(HandoverTransfer).where(HandoverTransfer.sender_id == user.id).order_by(HandoverTransfer.created_at.desc())))
    return TransfersOut(transfers=[summary(t) for t in rows], total=len(rows))


@router.get("/me/received", response_model=TransfersOut, summary="인수받은 내용: 인수 현황(받은 인수인계서 목록)")
def received(
    q: str = Query(default="", max_length=50, description="업무명·제목·보낸 사람 검색"),
    unread_only: bool = Query(default=False, description="아직 열어 보지 않은 것만"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    query = select(HandoverTransfer).where(HandoverTransfer.recipient_id == user.id)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.where(or_(HandoverTransfer.task_name.like(like), HandoverTransfer.title.like(like), HandoverTransfer.sender_name.like(like)))
    if unread_only:
        query = query.where(HandoverTransfer.read_at.is_(None))
    rows = list(db.scalars(query.order_by(HandoverTransfer.created_at.desc())))
    unread = db.scalar(select(func.count(HandoverTransfer.id)).where(HandoverTransfer.recipient_id == user.id, HandoverTransfer.read_at.is_(None))) or 0
    message = f"인수받은 인수인계서 {len(rows)}건" + (f"(새로 받은 것 {unread}건)" if unread else "") if rows else "아직 인수받은 인수인계서가 없습니다."
    return TransfersOut(message=message, transfers=[summary(t) for t in rows], total=len(rows), unread_count=unread)


def _load_received(db: Session, user: User, transfer_id: str) -> HandoverTransfer:
    t = db.get(HandoverTransfer, transfer_id)
    if t is None or t.recipient_id != user.id:  # 받는 사람만 볼 수 있다
        raise HTTPException(status_code=404, detail="인수받은 인수인계서를 찾을 수 없습니다.")
    return t


@router.get("/me/received/{transfer_id}", response_model=TransferDetail, summary="인수받은 산출물 보기(처음 열면 읽음 처리)")
def received_detail(transfer_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = _load_received(db, user, transfer_id)
    if t.read_at is None:
        t.read_at = utcnow()
        db.commit()
    return TransferDetail(**summary(t).model_dump(), markdown=t.markdown, session_id=t.session_id)


@router.get("/me/received/{transfer_id}/download", summary="인수받은 산출물 내려받기(pdf|docx|md)")
def received_download(
    transfer_id: str,
    format: Literal["pdf", "docx", "md"] = Query(default="pdf"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    t = _load_received(db, user, transfer_id)
    title = f"{t.sender_name} 인수인계서"
    if format == "pdf":
        content = to_pdf(t.markdown, title=title)
    elif format == "docx":
        content = to_docx(t.markdown, title=title)
    else:
        content = t.markdown.encode("utf-8")
    base = re.sub(r"[^\w가-힣.-]+", "_", f"인수인계서_{t.sender_name}_{t.task_name or t.title}_v{t.document_version}").strip("_")
    headers = {"Content-Disposition": f"attachment; filename=\"handover.{format}\"; filename*=UTF-8''{quote(base + '.' + format)}"}
    return Response(content=content, media_type=MEDIA_TYPES[format], headers=headers)


def transfer_counts(db: Session, user: User) -> dict[str, int]:
    """마이페이지 작성 현황에 넣는 보낸·받은 수."""
    sent_count = db.scalar(select(func.count(HandoverTransfer.id)).where(HandoverTransfer.sender_id == user.id)) or 0
    received_count = db.scalar(select(func.count(HandoverTransfer.id)).where(HandoverTransfer.recipient_id == user.id)) or 0
    unread = db.scalar(select(func.count(HandoverTransfer.id)).where(HandoverTransfer.recipient_id == user.id, HandoverTransfer.read_at.is_(None))) or 0
    return {"sent_transfers": sent_count, "received_transfers": received_count, "received_unread": unread}

