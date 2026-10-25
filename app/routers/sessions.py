"""세션(인수인계 건) 생성·조회·삭제, 폴링용 상태 조회."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import HandoverSession, Message, User
from ..schemas import MessageOut, SessionCreate, SessionDetail, SessionSummary, StateOut
from ..services.container import Services
from .deps import detail, get_db, get_services, get_user_id, is_successor, is_team_admin, load_session, message_out, optional_user, summary, usage_total

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
    successor_login_id: str = "",
    handover_date: str = "",
    leave_start: str = "",
    leave_end: str = "",
    title: str = "",
    mode: str = "reassignment",
    task_name: str = "",
    units: list[dict[str, Any]] | None = None,
    welcome: str = DEFAULT_WELCOME,
) -> HandoverSession:
    """인수인계 세션과 첫 안내 메시지를 만든다(커밋은 호출한 쪽에서)."""
    row = HandoverSession(
        id=uuid.uuid4().hex,
        user_id=user_id,
        # 단위업무를 여러 개 고르면 task_name 이 길어지므로 제목 칸(200자)에 맞춰 자른다
        title=(title or (f"{owner_name} · {task_name} 인수인계" if task_name else " ".join(x for x in (owner_name, position) if x) + " 인수인계"))[:200],
        owner_name=owner_name,
        organization=organization,
        position=position,
        duties=duties,
        successor=successor,
        successor_login_id=successor_login_id.lower(),
        handover_date=handover_date,
        leave_start=leave_start,
        leave_end=leave_end,
        mode=mode,
        task_name=task_name,
        units_json=json.dumps(units or [], ensure_ascii=False),
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
def get_session(session_id: str, db: Session = Depends(get_db), user: User | None = Depends(optional_user)):
    row = load_session(db, session_id)
    out = detail(db, row)
    if user is not None and row.user_id.startswith("user:"):  # 화면이 읽기 전용 표시(전달받은 문서 / 팀원 문서)를 고르는 데 쓴다
        if user.owner_key == row.user_id:
            out.viewer = "owner"
        elif is_successor(db, user, row):
            out.viewer = "recipient"
        elif is_team_admin(db, user, row):
            out.viewer = "team"
    return out


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
