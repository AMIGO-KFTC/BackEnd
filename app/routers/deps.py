"""라우터 공용 의존성과 변환 함수."""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import HandoverSession, Message
from ..schemas import MessageOut, Progress, SessionDetail, SessionSummary, SourceOut
from ..services.container import Services


def get_services(request: Request) -> Services:
    return request.app.state.services


def get_db(services: Services = Depends(get_services)):
    with services.db() as db:
        yield db


def get_user_id(x_user_id: str | None = Header(default=None)) -> str:
    """프로토타입용 사용자 구분(브라우저가 만든 임의 ID). 실제 서비스에서는 사내 SSO 로 교체한다."""
    return (x_user_id or "").strip()[:64]


def load_session(db: Session, session_id: str) -> HandoverSession:
    row = db.get(HandoverSession, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    return row


def summary(row: HandoverSession) -> SessionSummary:
    return SessionSummary(
        id=row.id,
        title=row.title,
        owner_name=row.owner_name,
        organization=row.organization,
        position=row.position,
        stage=row.stage,
        status=row.status,
        document_version=row.document_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def detail(db: Session, row: HandoverSession) -> SessionDetail:
    gaps = row.gaps
    last_id = db.scalar(select(func.max(Message.id)).where(Message.session_id == row.id)) or 0
    return SessionDetail(
        **summary(row).model_dump(),
        duties=row.duties,
        successor=row.successor,
        handover_date=row.handover_date,
        progress=Progress(message=row.progress_message, current=row.progress_current, total=row.progress_total),
        error=row.error,
        engine=row.engine,
        slots=row.slots,
        gaps=gaps,
        open_gap_count=sum(1 for g in gaps if g.get("status") == "open"),
        question_count=row.question_count,
        sources=[SourceOut.model_validate(s) for s in row.sources],
        last_message_id=last_id,
    )


def message_out(message: Message) -> MessageOut:
    return MessageOut.model_validate(message)
