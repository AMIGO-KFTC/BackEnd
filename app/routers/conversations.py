"""대화 진행 상태와 기록 불러오기: 로그인한 사용자가 각 대화(단위업무)를 어디까지 진행했는지, 무엇을 주고받았는지.

대화 메시지(messages), 세션 상태(sessions), 에이전트 체크포인트(agent_checkpoints), 읽음 위치(conversation_reads)가
모두 같은 DB 에 있으므로, 다시 접속하거나 서버가 재시작돼도 이 API 로 화면을 멈춘 곳 그대로 되살릴 수 있다.
"""

from __future__ import annotations

from amigo_agent import STAGE_LABEL, STAGE_NUMBER
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import ConversationRead, HandoverSession, Message, UnitTask, User
from ..schemas import (
    ConversationProgress,
    ConversationResult,
    ConversationsResult,
    CurrentQuestion,
    MessagesPage,
    QuestionStats,
    ReadIn,
)
from .deps import as_utc, current_user, get_db, message_out

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

NEXT_ACTIONS = {
    "upload_files": "업무 자료를 올려 주세요.",
    "processing_files": "올린 자료를 읽고 있어요. 잠시만 기다려 주세요.",
    "start_analysis": "'분석 시작'을 눌러 AI 분석을 시작해 주세요.",
    "ai_working": "AI가 처리하고 있어요.",
    "answer_question": "AI의 질문에 답해 주세요. 모르면 건너뛰어도 됩니다.",
    "confirm_answer": "AI가 정리한 내용이 맞는지 확인해 주세요.",
    "reply": "AI 안내에 답하거나 '지금 문서 생성'을 눌러 주세요.",
    "review_document": "인수인계서를 검토하고, 고칠 내용을 채팅으로 알려 주세요.",
    "retry": "작업이 멈췄습니다. '다시 시도'를 눌러 이어서 진행해 주세요.",
}


def load_owned(db: Session, user: User, session_id: str) -> HandoverSession:
    row = db.get(HandoverSession, session_id)
    if row is None or row.user_id != user.owner_key:
        raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다.")
    return row


def _current_question(db: Session, row: HandoverSession, last_user_id: int) -> CurrentQuestion | None:
    """답을 기다리는 질문: 사용자의 마지막 메시지 뒤에 AI 가 보낸 가장 최근 질문/확인 요청."""
    if row.status != "waiting":
        return None
    message = db.scalars(
        select(Message)
        .where(Message.session_id == row.id, Message.role == "assistant", Message.kind.in_(("question", "confirm")), Message.id > last_user_id)
        .order_by(Message.id.desc())
        .limit(1)
    ).first()
    if message is None:
        return None
    meta = message.meta
    return CurrentQuestion(
        message_id=message.id,
        kind=message.kind,
        content=message.content,
        gap_id=str(meta.get("gap_id", "")),
        slot=str(meta.get("slot", "")),
        number=int(meta.get("number", 0) or 0),
        quick_replies=list(meta.get("quick_replies") or []),
        asked_at=message.created_at,
    )


def _next_action(row: HandoverSession, question: CurrentQuestion | None) -> str:
    if row.status == "error":
        return "retry"
    if row.status == "running":
        return "ai_working"
    if row.stage == "setup":
        statuses = {s.status for s in row.sources}
        if not statuses or statuses == {"failed"}:
            return "upload_files"
        if statuses & {"pending", "processing"}:
            return "processing_files"
        return "start_analysis"
    if question is not None:
        return "confirm_answer" if question.kind == "confirm" else "answer_question"
    if row.stage == "review":
        return "review_document"
    return "reply" if row.status == "waiting" else "ai_working"


def build_progress(db: Session, row: HandoverSession) -> ConversationProgress:
    counts = db.execute(
        select(Message.role, func.count(), func.max(Message.id)).where(Message.session_id == row.id).group_by(Message.role)
    ).all()
    message_count = sum(n for _, n, _ in counts)
    last_user_id = next((last for role, _, last in counts if role == "user"), 0) or 0
    last = db.scalars(select(Message).where(Message.session_id == row.id).order_by(Message.id.desc()).limit(1)).first()
    read = db.get(ConversationRead, row.id)
    last_read = read.last_read_message_id if read else 0
    unread = db.scalar(
        select(func.count()).select_from(Message).where(Message.session_id == row.id, Message.id > last_read, Message.role != "user")
    )
    question = _current_question(db, row, last_user_id)
    action = _next_action(row, question)
    gaps = row.gaps
    status_count = {s: sum(1 for g in gaps if g.get("status") == s) for s in ("open", "answered", "resolved", "skipped")}
    activity = [as_utc(row.updated_at)] + ([as_utc(last.created_at)] if last else [])
    return ConversationProgress(
        session_id=row.id,
        task_id=db.scalar(select(UnitTask.id).where(UnitTask.session_id == row.id)),
        title=row.title,
        stage=row.stage,
        stage_number=STAGE_NUMBER.get(row.stage, 0),
        stage_label=STAGE_LABEL.get(row.stage, "자료 준비"),
        status=row.status,
        next_action=action,
        next_action_label=row.progress_message if action == "ai_working" and row.progress_message else NEXT_ACTIONS[action],
        progress_message=row.progress_message,
        current_question=question,
        questions=QuestionStats(
            asked=row.question_count,
            answered=status_count["answered"] + status_count["resolved"],
            skipped=status_count["skipped"],
            remaining=status_count["open"],
            total=len(gaps),
        ),
        file_count=sum(1 for s in row.sources if s.kind == "file"),
        document_version=row.document_version,
        message_count=message_count,
        last_message=message_out(last) if last else None,
        last_read_message_id=last_read,
        unread_count=unread or 0,
        started_at=row.created_at,
        last_activity_at=max(activity),
    )


def messages_page(db: Session, session_id: str, before: int | None, limit: int) -> MessagesPage:
    query = select(Message).where(Message.session_id == session_id)
    if before:
        query = query.where(Message.id < before)
    rows = db.scalars(query.order_by(Message.id.desc()).limit(limit + 1)).all()
    return MessagesPage(messages=[message_out(m) for m in reversed(rows[:limit])], has_more_before=len(rows) > limit)


@router.get("", response_model=ConversationsResult, summary="내 대화 목록과 각각 어디까지 진행했는지(최근 활동 순)")
def list_conversations(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(HandoverSession).where(HandoverSession.user_id == user.owner_key))
    items = sorted((build_progress(db, row) for row in rows), key=lambda p: p.last_activity_at, reverse=True)
    return ConversationsResult(conversations=items)


@router.get("/{session_id}", response_model=ConversationResult, summary="대화 불러오기: 진행 상태 + 최근 대화 기록")
def get_conversation(
    session_id: str,
    limit: int = Query(default=50, ge=1, le=200, description="함께 돌려줄 최근 메시지 수"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    row = load_owned(db, user, session_id)
    return ConversationResult(progress=build_progress(db, row), history=messages_page(db, session_id, None, limit))


@router.get("/{session_id}/messages", response_model=MessagesPage, summary="대화 기록 페이지(before 보다 오래된 메시지)")
def list_conversation_messages(
    session_id: str,
    before: int | None = Query(default=None, ge=1, description="이 메시지 ID 보다 오래된 것만(비우면 가장 최근부터)"),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    load_owned(db, user, session_id)
    return messages_page(db, session_id, before, limit)


@router.put("/{session_id}/read", response_model=ConversationResult, summary="읽음 위치 저장(여기까지 읽었음)")
def mark_read(
    session_id: str,
    body: ReadIn,
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    row = load_owned(db, user, session_id)
    newest = db.scalar(select(func.max(Message.id)).where(Message.session_id == session_id)) or 0
    read = db.get(ConversationRead, session_id) or ConversationRead(session_id=session_id, user_id=user.id, last_read_message_id=0)
    read.last_read_message_id = max(read.last_read_message_id, min(body.message_id, newest))  # 뒤로 돌아가지 않게
    db.add(read)
    db.commit()
    return ConversationResult(message="읽음 위치를 저장했습니다.", progress=build_progress(db, row), history=messages_page(db, session_id, None, limit))
