"""근거 원문: 인수인계서의 근거 번호([1], [2] …)가 가리키는 자료 조각과, 올린 자료의 조각 원문 조회.

RAG 의 KnowledgeBase.get_chunks() 를 쓴다. 근거 번호는 AI 렌더러(amigo_agent.render._Citations)와 같은 규칙으로
매긴다: 양식의 장 순서 → 항목 순서 → 항목의 근거 순서, 같은 (출처 표기, 조각) 은 같은 번호.
근거 열이 없는 장(`SlotSpec.evidence=False`)과 다른 장 안에 들어가는 장은 번호를 달지 않는다.
"""

from __future__ import annotations

import re
from typing import Any

from amigo_agent.template import SLOTS, slots_for
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..models import HandoverSession, Source
from ..schemas import ChunkOut, ChunksOut, CitationOut, CitationsOut
from ..services.container import Services
from .deps import get_db, get_services, load_session

router = APIRouter(prefix="/api/sessions/{session_id}", tags=["evidence"])


def _chunk_out(result: Any, fallback_index: int) -> ChunkOut:
    meta = getattr(result, "metadata", {}) or {}
    page = meta.get("page")
    return ChunkOut(
        chunk_id=str(getattr(result, "chunk_id", "")),
        index=int(meta.get("chunk_index", fallback_index) or 0),
        text=str(getattr(result, "text", "")),
        citation=str(getattr(result, "citation", "") or meta.get("source_name", "")),
        page=int(page) if isinstance(page, (int, float)) or (isinstance(page, str) and page.isdigit()) else None,
        sheet=str(meta.get("sheet") or ""),
        section=str(meta.get("section") or ""),
    )


def _rendered_slots(row: HandoverSession) -> list:
    """번호를 매길 장 목록. 문서가 있으면 문서에 실제로 들어간 장(작성 당시의 인계 유형)을 따른다."""
    if row.document_md:
        headings = set(re.findall(r"^## \d+\. (.+?)\s*$", row.document_md, re.M))
        rendered = [spec for spec in SLOTS if spec.title in headings]
        if rendered:
            return rendered
    return list(slots_for(row.profile()))


def number_citations(row: HandoverSession) -> list[dict[str, Any]]:
    """문서와 같은 순서로 근거에 번호를 매긴다(첫 등장 위치의 장·항목을 함께 기록)."""
    slots = row.slots
    numbered: dict[str, dict[str, Any]] = {}
    for spec in _rendered_slots(row):
        if not spec.evidence or spec.embed_into:  # 근거 번호를 달지 않는 장(업무 단위 요약, 다른 장 안에 들어가는 협업관계)
            continue
        for item in (slots.get(spec.key) or {}).get("items") or []:
            for c in item.get("citations") or []:
                key = f"{c.get('label')}|{c.get('chunk_id') or c.get('quote', '')[:40]}"
                if key not in numbered:
                    numbered[key] = {**c, "number": len(numbered) + 1, "slot": spec.key, "item_title": str(item.get("title") or "")}
    return list(numbered.values())


@router.get("/citations", response_model=CitationsOut, summary="인수인계서 근거 번호별 원문")
def list_citations(session_id: str, db: Session = Depends(get_db), services: Services = Depends(get_services)):
    row = load_session(db, session_id)
    sources = {s.id: s for s in row.sources}
    kb = services.kb(session_id)
    chunk_cache: dict[str, dict[str, Any]] = {}

    def find_chunk(source_id: str, chunk_id: str) -> Any | None:
        if not source_id or not chunk_id:
            return None
        if source_id not in chunk_cache:
            try:
                chunk_cache[source_id] = {c.chunk_id: c for c in kb.get_chunks(source_id)}
            except Exception:  # 지식베이스가 비어 있거나(재적재 전) 읽지 못해도 목록은 돌려준다
                chunk_cache[source_id] = {}
        return chunk_cache[source_id].get(chunk_id)

    out = []
    for c in number_citations(row):
        source_id, chunk_id = str(c.get("source_id") or ""), str(c.get("chunk_id") or "")
        source = sources.get(source_id)
        label = str(c.get("label") or "출처")
        # 인계자 답변은 AI 가 지식베이스에 따로 넣은 자료라 BackEnd 자료 목록에 없다. 올린 자료를 지운 경우와 구분한다.
        is_answer = source_id == "chat" or label.startswith("인계자 답변")
        chunk = find_chunk(source_id, chunk_id) if source or is_answer else None
        quote = str(c.get("quote") or "")
        out.append(
            CitationOut(
                number=c["number"],
                label=label,
                quote=quote,
                kind="answer" if is_answer else "document",
                source_id=source_id,
                source_name=source.name if source else "",
                chunk_id=chunk_id,
                text=str(chunk.text) if chunk is not None else quote,
                slot=c["slot"],
                item_title=c["item_title"],
            )
        )
    return CitationsOut(document_version=row.document_version, citations=out)


@router.get("/sources/{source_id}/chunks", response_model=ChunksOut, summary="올린 자료의 조각 원문(AI 가 읽은 내용)")
def list_chunks(
    session_id: str,
    source_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
):
    load_session(db, session_id)
    source = db.get(Source, source_id)
    if source is None or source.session_id != session_id:
        raise HTTPException(status_code=404, detail="자료를 찾을 수 없습니다.")
    if source.status != "ready":
        raise HTTPException(status_code=409, detail="아직 처리되지 않았거나 처리에 실패한 자료입니다.")
    chunks = services.kb(session_id).get_chunks(source_id)
    return ChunksOut(
        source_id=source_id,
        source_name=source.name,
        chunks=[_chunk_out(c, i) for i, c in enumerate(chunks[:limit])],
        total=len(chunks),
    )
