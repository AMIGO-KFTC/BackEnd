"""최종 인수인계서: 조회 · 직접 수정 저장 · 버전 기록(조회·복원) · 다운로드(/download?format=pdf|docx|md).

AI 가 만든 초안과 사용자가 화면에서 고친 문서는 모두 document_versions 에 버전으로 쌓인다(app/services/documents.py).
/document 는 항상 마지막 버전을 돌려주므로, 직접 고친 문서를 저장하면 다시 접속해도 고친 문서가 열린다.
"""

from __future__ import annotations

import json
import re
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import DocumentVersion, HandoverSession, Message, UnitTask, User
from ..schemas import (
    DocumentOut,
    DocumentRestoreIn,
    DocumentSaved,
    DocumentSaveIn,
    DocumentVersionOut,
    DocumentVersionsOut,
    DocumentVersionSummary,
    MyDocument,
    MyDocumentsOut,
)
from ..services.container import Services
from ..services.documents import ORIGIN_LABELS, add_version, latest_version
from ..services.exporters import to_docx, to_pdf
from .deps import current_user, get_db, get_services, load_session, optional_user

router = APIRouter(prefix="/api/sessions/{session_id}", tags=["documents"])
mine_router = APIRouter(prefix="/api/documents", tags=["documents"])

MEDIA_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}
NO_DOCUMENT = "아직 생성된 인수인계서가 없습니다."


def _version_count(db: Session, session_id: str) -> int:
    return db.scalar(select(func.count(DocumentVersion.id)).where(DocumentVersion.session_id == session_id)) or 0


def _summary(v: DocumentVersion, current: int) -> DocumentVersionSummary:
    return DocumentVersionSummary(
        version=v.version,
        origin=v.origin,
        origin_label=ORIGIN_LABELS.get(v.origin, v.origin),
        ai_version=v.ai_version,
        based_on=v.based_on,
        note=v.note,
        author_name=v.author_name,
        char_count=len(v.markdown),
        is_current=v.version == current,
        created_at=v.created_at,
    )


def _version_out(v: DocumentVersion, current: int) -> DocumentVersionOut:
    return DocumentVersionOut(**_summary(v, current).model_dump(), markdown=v.markdown)


def _load_version(db: Session, session_id: str, version: int) -> DocumentVersion:
    found = db.scalar(select(DocumentVersion).where(DocumentVersion.session_id == session_id, DocumentVersion.version == version))
    if found is None:
        raise HTTPException(status_code=404, detail=f"v{version} 인수인계서를 찾을 수 없습니다.")
    return found


def _ensure_editable(services: Services, row: HandoverSession, base_version: int | None) -> None:
    if not row.document_md:
        raise HTTPException(status_code=404, detail=NO_DOCUMENT)
    if services.runner.is_busy(row.id):
        raise HTTPException(status_code=409, detail="AI가 처리 중입니다. 끝난 뒤 다시 저장해 주세요.")
    if base_version is not None and base_version != row.document_version:
        raise HTTPException(
            status_code=409,
            detail=f"그사이 인수인계서가 v{row.document_version}(으)로 바뀌었습니다. 최신 문서를 다시 불러온 뒤 수정해 주세요.",
        )


def _commit_version(db: Session, row: HandoverSession, version: DocumentVersion, notice: str) -> None:
    db.add(Message(session_id=row.id, role="system", kind="info", content=notice, meta_json=json.dumps({"document_version": version.version})))
    try:
        db.commit()
    except IntegrityError:  # 같은 번호를 동시에 저장한 경우
        db.rollback()
        raise HTTPException(status_code=409, detail="다른 곳에서 먼저 저장했습니다. 최신 문서를 다시 불러온 뒤 수정해 주세요.") from None


@router.get("/document", response_model=DocumentOut, summary="현재 인수인계서(Markdown, 직접 고친 문서 포함)")
def get_document(session_id: str, db: Session = Depends(get_db)):
    row = load_session(db, session_id)
    if not row.document_md:
        raise HTTPException(status_code=404, detail=NO_DOCUMENT)
    current = latest_version(db, session_id)
    origin = current.origin if current is not None else "ai"
    return DocumentOut(
        markdown=row.document_md,
        version=row.document_version,
        origin=origin,
        origin_label=ORIGIN_LABELS.get(origin, origin),
        author_name=current.author_name if current is not None else "",
        updated_at=current.created_at if current is not None else row.updated_at,
        version_count=_version_count(db, session_id),
    )


@router.put("/document", response_model=DocumentSaved, summary="화면에서 직접 고친 인수인계서 저장(새 버전)")
def save_document(
    session_id: str,
    body: DocumentSaveIn,
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
    user: User | None = Depends(optional_user),
):
    """고친 문서 전체를 새 버전으로 저장하고 현재 문서로 삼는다. 이전 버전은 /document/versions 에 남는다.

    base_version 이 현재 version 과 다르면(그사이 AI 가 새 초안을 만들었거나 다른 창에서 저장) 409 로 덮어쓰기를 막는다.
    """
    row = load_session(db, session_id)
    _ensure_editable(services, row, body.base_version)
    current = latest_version(db, session_id)
    if body.markdown == row.document_md and current is not None:
        return DocumentSaved(message="바뀐 내용이 없어 저장하지 않았습니다.", saved=False, document=_version_out(current, row.document_version))
    version = add_version(
        db, row, body.markdown, origin="user", based_on=body.base_version, note=body.note or "직접 수정", author=user
    )
    notice = (
        f"인수인계서를 직접 수정해 v{version.version}(으)로 저장했습니다. "
        "채팅으로 수정을 요청하면 AI가 새 버전을 다시 작성하며, 직접 고친 문서는 버전 기록에서 다시 불러올 수 있습니다."
    )
    _commit_version(db, row, version, notice)
    return DocumentSaved(message=f"인수인계서를 v{version.version}(으)로 저장했습니다.", saved=True, document=_version_out(version, version.version))


@router.get("/document/versions", response_model=DocumentVersionsOut, summary="인수인계서 버전 기록(최신순)")
def list_versions(session_id: str, db: Session = Depends(get_db)):
    row = load_session(db, session_id)
    versions = db.scalars(select(DocumentVersion).where(DocumentVersion.session_id == session_id).order_by(DocumentVersion.version.desc()))
    return DocumentVersionsOut(
        session_id=session_id, current_version=row.document_version, versions=[_summary(v, row.document_version) for v in versions]
    )


@router.get("/document/versions/{version}", response_model=DocumentVersionOut, summary="특정 버전의 인수인계서")
def get_version(session_id: str, version: int, db: Session = Depends(get_db)):
    row = load_session(db, session_id)
    return _version_out(_load_version(db, session_id, version), row.document_version)


@router.post("/document/versions/{version}/restore", response_model=DocumentSaved, summary="이전 버전을 현재 문서로 복원(새 버전)")
def restore_version(
    session_id: str,
    version: int,
    body: DocumentRestoreIn | None = None,
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
    user: User | None = Depends(optional_user),
):
    body = body or DocumentRestoreIn()
    row = load_session(db, session_id)
    _ensure_editable(services, row, body.base_version)
    source = _load_version(db, session_id, version)
    if source.version == row.document_version:
        raise HTTPException(status_code=400, detail=f"v{version} 은(는) 이미 현재 문서입니다.")
    restored = add_version(
        db, row, source.markdown, origin="restore", based_on=source.version, note=body.note or f"v{source.version} 복원", author=user
    )
    _commit_version(db, row, restored, f"인수인계서 v{source.version}을(를) 복원해 v{restored.version}(으)로 저장했습니다.")
    return DocumentSaved(
        message=f"v{source.version}을(를) 복원해 v{restored.version}(으)로 저장했습니다.", saved=True, document=_version_out(restored, restored.version)
    )


@router.get("/download", summary="인수인계서 내려받기(version 을 주면 그 버전)")
def download(
    session_id: str,
    format: Literal["pdf", "docx", "md"] = Query(default="pdf"),
    version: int | None = Query(default=None, ge=1, description="내려받을 버전. 비우면 현재 문서"),
    db: Session = Depends(get_db),
):
    row = load_session(db, session_id)
    if not row.document_md:
        raise HTTPException(status_code=404, detail=NO_DOCUMENT)
    markdown, number = row.document_md, row.document_version
    if version is not None and version != row.document_version:
        markdown, number = _load_version(db, session_id, version).markdown, version
    title = f"{row.owner_name} 인수인계서"
    if format == "pdf":
        content = to_pdf(markdown, title=title)
    elif format == "docx":
        content = to_docx(markdown, title=title)
    else:
        content = markdown.encode("utf-8")
    base = re.sub(r"[^\w가-힣.-]+", "_", f"인수인계서_{row.owner_name}_v{number}").strip("_")
    filename = f"{base}.{format}"
    headers = {"Content-Disposition": f"attachment; filename=\"handover.{format}\"; filename*=UTF-8''{quote(filename)}"}
    return Response(content=content, media_type=MEDIA_TYPES[format], headers=headers)


@mine_router.get("", response_model=MyDocumentsOut, summary="내가 작성한 인수인계서 목록(최근 수정순)")
def my_documents(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """지금까지 만든(또는 고친) 인수인계서를 단위업무와 함께 보여준다. 내용은 /api/sessions/{session_id}/document 로 불러온다."""
    counts = (
        select(DocumentVersion.session_id, func.count(DocumentVersion.id).label("n"), func.max(DocumentVersion.created_at).label("at"))
        .group_by(DocumentVersion.session_id)
        .subquery()
    )
    query = (
        select(HandoverSession, UnitTask.id, counts.c.n, counts.c.at)
        .join(counts, counts.c.session_id == HandoverSession.id)
        .outerjoin(UnitTask, UnitTask.session_id == HandoverSession.id)
        .where(HandoverSession.user_id == user.owner_key, HandoverSession.document_md != "")
        .order_by(counts.c.at.desc())
    )
    documents = []
    for row, task_id, count, at in db.execute(query):
        current = latest_version(db, row.id)
        origin = current.origin if current is not None else "ai"
        documents.append(
            MyDocument(
                session_id=row.id,
                task_id=task_id,
                title=row.title,
                task_name=row.task_name,
                stage=row.stage,
                version=row.document_version,
                origin=origin,
                origin_label=ORIGIN_LABELS.get(origin, origin),
                version_count=count,
                updated_at=at,
            )
        )
    return MyDocumentsOut(documents=documents)
