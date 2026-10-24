"""인수인계 챗봇: 올린 자료(파일·메일·링크)·인계자 답변·완성된 인수인계서를 근거로 질문에 답한다.

- 질문 범위(scope)는 업무(세션) 하나: 근거는 그 업무의 지식베이스(올린 자료 + 인계자 답변)와 인수인계서다. 인수인계서는 근거 중 하나다.
- 내 업무(mine): 자료·인계자 답변·인수인계서를 모두 쓴다. 자료만 있고 아직 문서가 없는 작업 중인 업무도 물어볼 수 있다.
- 받은 인수인계(received): 인계자가 전송한 것은 인수인계서뿐이라 그것만 쓴다. 원본 자료는 인계자만 볼 수 있다.
- GET  /api/handover-chat/scopes  질문할 수 있는 업무 목록
- POST /api/handover-chat         질문 → 답변 + 근거 자료. 대화는 서버에 저장하지 않고 화면이 history 로 보낸다(꼬리 질문용).
아무것도 바꾸지 않는다(문서 수정은 인계자의 기존 문서 채팅·직접 수정으로 한다).
"""

from __future__ import annotations

from dataclasses import dataclass

from amigo_agent import LLMError, QAScope
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import HandoverSession, HandoverTransfer, User
from ..schemas import HandoverChatIn, HandoverChatOut, HandoverChatScope, HandoverChatScopesOut, HandoverChatSource
from ..services.container import Services
from ..services.exporters import strip_citation_marks
from .chat import _ensure_budget
from .deps import as_utc, current_user, get_db, get_services

router = APIRouter(prefix="/api/handover-chat", tags=["handover-chat"])


@dataclass
class Material:
    """질문 범위 하나와 그 근거(인수인계서 본문, 지식베이스를 쓸 세션)."""

    scope: HandoverChatScope
    markdown: str
    kb_session_id: str | None  # 지식베이스에서 찾을 세션(받은 인수인계는 None)


def _doc_text(markdown: str) -> str:
    """챗봇이 읽는 인수인계서 본문. 값 뒤에 붙은 근거 번호(' [1] [3]')는 챗봇이 풀 수 없으므로 내려받는 문서처럼 뺀다."""
    return strip_citation_marks(markdown)


def _ready_sources(row: HandoverSession) -> int:
    return sum(1 for s in row.sources if s.status == "ready")


def accessible_materials(db: Session, user: User) -> list[Material]:
    """질문할 수 있는 업무와 그 근거(최근 수정순). 같은 세션은 한 번만 나온다."""
    found: dict[str, Material] = {}
    for row in db.scalars(select(HandoverSession).where(HandoverSession.user_id == user.owner_key)):
        sources = _ready_sources(row)
        if not row.document_md and not sources:
            continue
        scope = HandoverChatScope(
            id=row.id, title=row.title or row.task_name or "인수인계", task_name=row.task_name, owner_name=row.owner_name, kind="mine",
            version=row.document_version if row.document_md else 0, has_document=bool(row.document_md), source_count=sources,
            session_id=row.id, updated_at=as_utc(row.updated_at),
        )
        found[row.id] = Material(scope, _doc_text(row.document_md), row.id if sources else None)
    for t in db.scalars(select(HandoverTransfer).where(HandoverTransfer.recipient_id == user.id)):
        row = t.session
        if row is not None and row.document_md:  # 인계자가 고친 최신 문서
            scope = HandoverChatScope(
                id=row.id, title=row.title or row.task_name or "인수인계", task_name=row.task_name, owner_name=row.owner_name, kind="received",
                version=row.document_version, has_document=True, source_count=0, session_id=row.id, updated_at=as_utc(row.updated_at),
            )
            found.setdefault(row.id, Material(scope, _doc_text(row.document_md), None))
        elif row is None and t.markdown:  # 인계자가 지웠으면 보낸 시점 문서
            scope = HandoverChatScope(
                id=f"transfer:{t.id}", title=t.title, task_name=t.task_name, owner_name=t.sender_name, kind="received",
                version=t.document_version, has_document=True, source_count=0, session_id=None, updated_at=as_utc(t.created_at),
            )
            found[scope.id] = Material(scope, _doc_text(t.markdown), None)
    return sorted(found.values(), key=lambda m: m.scope.updated_at, reverse=True)


def _label(scope: HandoverChatScope) -> str:
    return " · ".join(x for x in (scope.title, f"인계자 {scope.owner_name}" if scope.owner_name else "") if x)


@router.get("/scopes", response_model=HandoverChatScopesOut, summary="챗봇에게 물어볼 수 있는 업무 목록(올린 자료 · 인수인계서)")
def scopes(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return HandoverChatScopesOut(scopes=[m.scope for m in accessible_materials(db, user)])


@router.post("", response_model=HandoverChatOut, summary="올린 자료·인계자 답변·인수인계서를 근거로 질문에 답하기")
def ask(body: HandoverChatIn, user: User = Depends(current_user), db: Session = Depends(get_db), services: Services = Depends(get_services)):
    available = accessible_materials(db, user)
    if body.scope_ids is not None:
        by_id = {m.scope.id: m for m in available}
        if any(i not in by_id for i in body.scope_ids):  # 남의 업무 ID 를 넣어도 있는지 없는지 알려 주지 않는다
            raise HTTPException(status_code=404, detail="질문할 수 없는 업무가 포함되어 있어요.")
        available = [by_id[i] for i in dict.fromkeys(body.scope_ids)]
    _ensure_budget(services)
    history = [{"role": t.role, "content": t.content} for t in body.history if t.content.strip()]
    scopes_in = [
        QAScope(label=_label(m.scope), markdown=m.markdown, version=m.scope.version, kb=services.kb(m.kb_session_id) if m.kb_session_id else None)
        for m in available
    ]
    try:
        result = services.agent.answer_question(body.question, history, scopes_in)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    sources = [
        HandoverChatSource(
            scope_id=scope.id, scope_title=scope.title, scope_task=scope.task_name, scope_kind=scope.kind, scope_owner=scope.owner_name,
            session_id=scope.session_id, kind=s["kind"], label=s["label"], source_id=s.get("source_id") or None,
        )
        for s in result["sources"]
        for scope in [available[s["scope"]].scope]
    ]
    return HandoverChatOut(
        answer=result["answer"], grounded=result["grounded"], sources=sources, searched=result["searched"],
        engine=services.agent.describe().get("engine") or "",
    )
