"""에이전트 실행: 분석 시작(/analyze), 대화(/chat), 건너뛰기, 문서 생성, 재시도.

모든 실행은 백그라운드에서 돌고 202 를 즉시 돌려준다. 결과(진행률·AI 메시지)는 /state 폴링으로 받는다.

답변 전달(/chat): 화면에서 받은 답변을 대화 기록(messages)에 먼저 저장하고, 백그라운드에서 에이전트(send_message)로 넘긴다.
- AI 가 답을 기다리는 상태(LangGraph 입력 대기)가 아니면 받지 않는다(409). 오류로 멈췄으면 '다시 시도' 안내.
- reply_to(화면에 띄운 질문의 메시지 ID)를 주면 그 질문이 아직 답을 기다리는지 확인한다. 그사이 질문이 바뀌었으면
  (새 자료 반영, 다른 창에서 답함) 엉뚱한 질문에 답이 붙지 않게 409 로 돌려보낸다.
- 저장한 답변의 meta 에 어떤 질문(reply_to · gap_id · slot)에 대한 답인지 남겨 대화 기록에서 짝을 볼 수 있다.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import HandoverSession, Message
from ..schemas import Accepted, ChatIn, SessionDetail
from ..services.container import Services
from ..services.runner import SessionBusy
from .conversations import _current_question
from .deps import detail, get_db, get_services, load_session, message_out

router = APIRouter(prefix="/api/sessions/{session_id}", tags=["agent"])

BUSY = "AI가 이전 요청을 처리하고 있어요. 잠시 후 다시 시도해 주세요."


def _ensure_idle(services: Services, row: HandoverSession) -> None:
    if services.runner.is_busy(row.id):
        raise HTTPException(status_code=409, detail=BUSY)
    _ensure_budget(services)


def _ensure_budget(services: Services) -> None:
    """Claude 엔진이고 예산(AMIGO_LLM_BUDGET_USD)을 다 썼으면 새 AI 작업을 막는다(진행 중인 작업은 끝까지 간다)."""
    exhausted = services.budget_exhausted()
    if exhausted is not None:
        spent, budget = exhausted
        raise HTTPException(status_code=429, detail=budget_message(spent, budget))


def budget_message(spent: float, budget: float) -> str:
    return (
        f"설정한 Claude API 예산(${budget:.2f})을 모두 썼어요(추정 ${spent:.2f}). "
        "BackEnd/.env 의 AMIGO_LLM_BUDGET_USD 를 늘리거나 0(제한 없음)으로 바꾼 뒤 서버를 다시 시작해 주세요."
    )


def _ensure_started(row: HandoverSession) -> None:
    if row.stage == "setup":
        raise HTTPException(status_code=400, detail="먼저 자료를 등록하고 '분석 시작'을 눌러 주세요.")


STALE_QUESTION = "그사이 질문이 바뀌었어요. 새 질문을 확인한 뒤 다시 답해 주세요."


def _ensure_waiting(services: Services, row: HandoverSession) -> None:
    """에이전트가 사용자 입력을 기다릴 때만 답변·건너뛰기·문서 생성 요청을 넘긴다."""
    if services.agent.is_waiting(row.id):
        return
    if row.status == "error":
        raise HTTPException(status_code=409, detail="AI 처리 중 오류로 멈춰 있어요. '다시 시도'를 누른 뒤 답해 주세요.")
    raise HTTPException(status_code=409, detail="AI가 아직 답변을 받을 준비가 되지 않았어요. 잠시 후 다시 시도해 주세요.")


def _pending_question(db: Session, row: HandoverSession, reply_to: int | None) -> dict[str, Any]:
    """지금 답을 기다리는 질문을 찾아 답변 meta 로 돌려준다. reply_to 가 그 질문이 아니면 409."""
    last_user_id = db.scalar(select(func.max(Message.id)).where(Message.session_id == row.id, Message.role == "user")) or 0
    question = _current_question(db, row, last_user_id) if row.stage == "qna" else None
    if reply_to is not None and (question is None or question.message_id != reply_to):
        raise HTTPException(status_code=409, detail=STALE_QUESTION)
    if question is None:
        return {}
    return {"reply_to": question.message_id, "question_kind": question.kind, "gap_id": question.gap_id, "slot": question.slot}


def _submit(services: Services, session_id: str, action: str, call, progress: str = "") -> None:
    try:
        services.runner.run_agent(session_id, action, call, progress=progress)
    except SessionBusy as exc:
        raise HTTPException(status_code=409, detail=BUSY) from exc


def _user_message(db: Session, session_id: str, kind: str, content: str, meta: dict[str, Any] | None = None) -> Message:
    message = Message(session_id=session_id, role="user", kind=kind, content=content, meta_json=json.dumps(meta or {}, ensure_ascii=False))
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


@router.post("/analyze", response_model=SessionDetail, status_code=status.HTTP_202_ACCEPTED, summary="STAGE 1 분석 시작(다시 분석)")
def analyze(session_id: str, db: Session = Depends(get_db), services: Services = Depends(get_services)):
    row = load_session(db, session_id)
    _ensure_idle(services, row)
    if any(s.status in ("pending", "processing") for s in row.sources):
        raise HTTPException(status_code=409, detail="자료를 처리하는 중입니다. 처리가 끝나면 다시 눌러 주세요.")
    if not any(s.status == "ready" for s in row.sources):
        raise HTTPException(status_code=400, detail="분석할 자료가 없습니다. 파일이나 링크를 먼저 등록해 주세요.")
    try:
        services.runner.start_analysis(session_id, restart=row.stage != "setup")
    except SessionBusy as exc:
        raise HTTPException(status_code=409, detail=BUSY) from exc
    db.refresh(row)
    return detail(db, row)


@router.post("/chat", response_model=Accepted, status_code=status.HTTP_202_ACCEPTED, summary="STAGE 3 답변/검토 의견 보내기")
def chat(session_id: str, body: ChatIn, db: Session = Depends(get_db), services: Services = Depends(get_services)):
    """인계자 답변(또는 검토 의견)을 저장하고 AI 에 넘긴다. AI 의 정리·다음 질문은 /state 폴링으로 온다."""
    row = load_session(db, session_id)
    _ensure_started(row)
    _ensure_idle(services, row)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="메시지가 비어 있습니다.")
    _ensure_waiting(services, row)
    meta = _pending_question(db, row, body.reply_to)
    message = _user_message(db, session_id, "answer", text, meta)
    kb, agent = services.kb(session_id), services.agent
    _submit(services, session_id, "chat", lambda h: agent.send_message(session_id, text, kb, on_event=h), "답변을 정리하고 있어요.")
    return Accepted(message=message_out(message))


@router.post("/skip", response_model=Accepted, status_code=status.HTTP_202_ACCEPTED, summary="현재 질문 건너뛰기")
def skip(
    session_id: str,
    reply_to: int | None = Query(default=None, ge=1, description="건너뛸 질문의 메시지 ID(그사이 질문이 바뀌었으면 409)"),
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
):
    row = load_session(db, session_id)
    _ensure_started(row)
    _ensure_idle(services, row)
    _ensure_waiting(services, row)
    meta = _pending_question(db, row, reply_to)
    message = _user_message(db, session_id, "skip", "이 질문은 건너뛸게요.", meta)
    kb, agent = services.kb(session_id), services.agent
    _submit(services, session_id, "skip", lambda h: agent.skip(session_id, kb, on_event=h))
    return Accepted(message=message_out(message))


@router.post("/generate", response_model=Accepted, status_code=status.HTTP_202_ACCEPTED, summary="STAGE 4 지금 바로 문서 생성")
def generate(session_id: str, db: Session = Depends(get_db), services: Services = Depends(get_services)):
    row = load_session(db, session_id)
    _ensure_started(row)
    _ensure_idle(services, row)
    _ensure_waiting(services, row)
    message = _user_message(db, session_id, "command", "지금까지 내용으로 인수인계서를 만들어 주세요.")
    kb, agent = services.kb(session_id), services.agent
    _submit(services, session_id, "generate", lambda h: agent.finish(session_id, kb, on_event=h), "인수인계서를 작성하고 있어요.")
    return Accepted(message=message_out(message))


@router.post("/retry", response_model=SessionDetail, status_code=status.HTTP_202_ACCEPTED, summary="오류로 멈춘 작업 다시 시도")
def retry(session_id: str, db: Session = Depends(get_db), services: Services = Depends(get_services)):
    row = load_session(db, session_id)
    _ensure_idle(services, row)
    kb, agent = services.kb(session_id), services.agent
    if not agent.snapshot(session_id)["exists"]:
        profile = row.profile()
        _submit(services, session_id, "analyze", lambda h: agent.start(session_id, profile, kb, on_event=h), "자료를 분석하고 있어요.")
    else:
        _submit(services, session_id, "retry", lambda h: agent.retry(session_id, kb, on_event=h), "다시 시도하고 있어요.")
    db.refresh(row)
    return detail(db, row)
