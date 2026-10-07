"""에이전트 실행: 분석 시작(/analyze), 대화(/chat), 건너뛰기, 문서 생성, 재시도.

모든 실행은 백그라운드에서 돌고 202 를 즉시 돌려준다. 결과(진행률·AI 메시지)는 /state 폴링으로 받는다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..models import HandoverSession, Message
from ..schemas import Accepted, ChatIn, SessionDetail
from ..services.container import Services
from ..services.runner import SessionBusy
from .deps import detail, get_db, get_services, load_session, message_out

router = APIRouter(prefix="/api/sessions/{session_id}", tags=["agent"])

BUSY = "AI가 이전 요청을 처리하고 있어요. 잠시 후 다시 시도해 주세요."


def _ensure_idle(services: Services, row: HandoverSession) -> None:
    if services.runner.is_busy(row.id):
        raise HTTPException(status_code=409, detail=BUSY)


def _ensure_started(row: HandoverSession) -> None:
    if row.stage == "setup":
        raise HTTPException(status_code=400, detail="먼저 자료를 등록하고 '분석 시작'을 눌러 주세요.")


def _submit(services: Services, session_id: str, action: str, call, progress: str = "") -> None:
    try:
        services.runner.run_agent(session_id, action, call, progress=progress)
    except SessionBusy as exc:
        raise HTTPException(status_code=409, detail=BUSY) from exc


def _user_message(db: Session, session_id: str, kind: str, content: str) -> Message:
    message = Message(session_id=session_id, role="user", kind=kind, content=content)
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
    if row.stage != "setup":
        db.add(Message(session_id=session_id, role="system", kind="info", content="등록된 자료로 분석을 처음부터 다시 시작합니다."))
    row.stage = "analyzing"
    row.slots_json, row.gaps_json, row.pending_files_json = "{}", "[]", "[]"
    row.progress_current, row.progress_total = 0, 6
    db.commit()
    profile, kb, agent = row.profile(), services.kb(session_id), services.agent
    _submit(services, session_id, "analyze", lambda h: agent.start(session_id, profile, kb, on_event=h), "자료를 분석하고 있어요.")
    db.refresh(row)
    return detail(db, row)


@router.post("/chat", response_model=Accepted, status_code=status.HTTP_202_ACCEPTED, summary="STAGE 3 답변/검토 의견 보내기")
def chat(session_id: str, body: ChatIn, db: Session = Depends(get_db), services: Services = Depends(get_services)):
    row = load_session(db, session_id)
    _ensure_started(row)
    _ensure_idle(services, row)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="메시지가 비어 있습니다.")
    message = _user_message(db, session_id, "answer", text)
    kb, agent = services.kb(session_id), services.agent
    _submit(services, session_id, "chat", lambda h: agent.send_message(session_id, text, kb, on_event=h), "답변을 정리하고 있어요.")
    return Accepted(message=message_out(message))


@router.post("/skip", response_model=Accepted, status_code=status.HTTP_202_ACCEPTED, summary="현재 질문 건너뛰기")
def skip(session_id: str, db: Session = Depends(get_db), services: Services = Depends(get_services)):
    row = load_session(db, session_id)
    _ensure_started(row)
    _ensure_idle(services, row)
    message = _user_message(db, session_id, "skip", "이 질문은 건너뛸게요.")
    kb, agent = services.kb(session_id), services.agent
    _submit(services, session_id, "skip", lambda h: agent.skip(session_id, kb, on_event=h))
    return Accepted(message=message_out(message))


@router.post("/generate", response_model=Accepted, status_code=status.HTTP_202_ACCEPTED, summary="STAGE 4 지금 바로 문서 생성")
def generate(session_id: str, db: Session = Depends(get_db), services: Services = Depends(get_services)):
    row = load_session(db, session_id)
    _ensure_started(row)
    _ensure_idle(services, row)
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
