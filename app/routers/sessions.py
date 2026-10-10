"""세션(인수인계 건) 생성·조회·삭제, 폴링용 상태 조회."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import HandoverSession, Message
from ..schemas import MessageOut, SessionCreate, SessionDetail, SessionSummary, StateOut
from ..services.container import Services
from .deps import detail, get_db, get_services, get_user_id, load_session, message_out, summary, usage_total

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


DEFAULT_WELCOME = (
    "업무정의서·회의자료·메일·소스코드 같은 자료를 올리거나 컨플루언스/나누미 링크를 등록한 뒤 "
    "'분석 시작'을 눌러 주세요."
)


def new_handover_session(
    db: Session,
    *,
    user_id: str,
    owner_name: str,
    organization: str = "",
    position: str = "",
    duties: str = "",
    successor: str = "",
    handover_date: str = "",
    title: str = "",
    welcome: str = DEFAULT_WELCOME,
) -> HandoverSession:
    """인수인계 세션과 첫 안내 메시지를 만든다(커밋은 호출한 쪽에서)."""
    row = HandoverSession(
        id=uuid.uuid4().hex,
        user_id=user_id,
        title=title or " ".join(x for x in (owner_name, position) if x) + " 인수인계",
        owner_name=owner_name,
        organization=organization,
        position=position,
        duties=duties,
        successor=successor,
        handover_date=handover_date,
    )
    db.add(row)
    content = f"안녕하세요, {owner_name}님. 인수인계서 작성을 도와드릴 AMIGO 입니다.\n{welcome}"
    db.add(Message(session_id=row.id, role="assistant", kind="info", content=content))
    return row


def remove_handover_session(db: Session, services: Services, row: HandoverSession) -> None:
    """세션과 업로드 파일·지식베이스·에이전트 상태를 함께 지운다."""
    if services.runner.is_busy(row.id):
        raise HTTPException(status_code=409, detail="AI가 처리 중이라 삭제할 수 없습니다. 잠시 후 다시 시도해 주세요.")
    session_id = row.id
    db.delete(row)
    db.commit()
    services.storage.remove_session(session_id)
    services.drop_kb(session_id)
    services.agent.delete(session_id)


@router.post("", response_model=SessionDetail, status_code=status.HTTP_201_CREATED, summary="인수인계 세션 생성(기초 정보 등록)")
def create_session(body: SessionCreate, db: Session = Depends(get_db), user_id: str = Depends(get_user_id)):
    row = new_handover_session(db, user_id=user_id, **body.model_dump())
    db.commit()
    db.refresh(row)
    return detail(db, row)


@router.get("", response_model=list[SessionSummary], summary="세션 목록(최근 순)")
def list_sessions(db: Session = Depends(get_db), user_id: str = Depends(get_user_id)):
    query = select(HandoverSession).order_by(HandoverSession.updated_at.desc()).limit(50)
    if user_id:
        query = query.where(HandoverSession.user_id == user_id)
    else:  # 사용자 구분 없이 부르면 로그인 계정의 세션은 빼고 보여 준다
        query = query.where(~HandoverSession.user_id.startswith("user:"))
    return [summary(row) for row in db.scalars(query)]


@router.get("/{session_id}", response_model=SessionDetail, summary="세션 상세(단계, 진행률, 슬롯 현황, 자료 목록)")
def get_session(session_id: str, db: Session = Depends(get_db)):
    return detail(db, load_session(db, session_id))


@router.get("/{session_id}/state", response_model=StateOut, summary="폴링: 세션 상태 + after 이후의 새 메시지")
def get_state(session_id: str, after: int = Query(default=0, ge=0), db: Session = Depends(get_db), services: Services = Depends(get_services)):
    row = load_session(db, session_id)
    messages = db.scalars(
        select(Message).where(Message.session_id == session_id, Message.id > after).order_by(Message.id).limit(200)
    ).all()
    return StateOut(session=detail(db, row), messages=[message_out(m) for m in messages], usage_total=usage_total(db, services.settings.llm_budget_usd))


@router.get("/{session_id}/messages", response_model=list[MessageOut], summary="대화 기록")
def list_messages(session_id: str, after: int = Query(default=0, ge=0), db: Session = Depends(get_db)):
    load_session(db, session_id)
    rows = db.scalars(select(Message).where(Message.session_id == session_id, Message.id > after).order_by(Message.id)).all()
    return [message_out(m) for m in rows]


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT, summary="세션 삭제(자료·지식베이스·대화 상태 포함)")
def delete_session(session_id: str, db: Session = Depends(get_db), services: Services = Depends(get_services)):
    remove_handover_session(db, services, load_session(db, session_id))
