"""라우터 공용 의존성과 변환 함수."""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import HandoverSession, Message
from ..schemas import MessageOut, Progress, SessionDetail, SessionSummary, SourceOut, Usage, UsageTotal
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
        mode=row.mode or "transfer",
        task_name=row.task_name,
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
        usage=session_usage(row),
    )


def session_usage(row: HandoverSession) -> Usage:
    return Usage(
        requests=row.llm_requests or 0,
        input_tokens=row.input_tokens or 0,
        output_tokens=row.output_tokens or 0,
        cache_read_tokens=row.cache_read_tokens or 0,
        cache_write_tokens=row.cache_write_tokens or 0,
        cost_usd=round(row.cost_usd or 0.0, 4),
    )


def usage_total(db: Session, budget_usd: float) -> UsageTotal:
    s = HandoverSession
    totals = db.execute(
        select(
            func.count(s.id), func.coalesce(func.sum(s.llm_requests), 0), func.coalesce(func.sum(s.input_tokens), 0),
            func.coalesce(func.sum(s.output_tokens), 0), func.coalesce(func.sum(s.cache_read_tokens), 0),
            func.coalesce(func.sum(s.cache_write_tokens), 0), func.coalesce(func.sum(s.cost_usd), 0.0),
        )
    ).one()
    cost = round(float(totals[6]), 4)
    return UsageTotal(
        sessions=totals[0], requests=totals[1], input_tokens=totals[2], output_tokens=totals[3],
        cache_read_tokens=totals[4], cache_write_tokens=totals[5], cost_usd=cost,
        budget_usd=budget_usd, remaining_usd=round(max(budget_usd - cost, 0.0), 4) if budget_usd > 0 else None,
    )


def message_out(message: Message) -> MessageOut:
    return MessageOut.model_validate(message)
