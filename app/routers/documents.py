"""최종 인수인계서 조회와 다운로드(/download?format=pdf|docx|md)."""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..schemas import DocumentOut
from ..services.exporters import to_docx, to_pdf
from .deps import get_db, load_session

router = APIRouter(prefix="/api/sessions/{session_id}", tags=["documents"])

MEDIA_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


@router.get("/document", response_model=DocumentOut, summary="생성된 인수인계서(Markdown)")
def get_document(session_id: str, db: Session = Depends(get_db)):
    row = load_session(db, session_id)
    if not row.document_md:
        raise HTTPException(status_code=404, detail="아직 생성된 인수인계서가 없습니다.")
    return DocumentOut(markdown=row.document_md, version=row.document_version)


@router.get("/download", summary="인수인계서 내려받기")
def download(session_id: str, format: Literal["pdf", "docx", "md"] = Query(default="pdf"), db: Session = Depends(get_db)):
    row = load_session(db, session_id)
    if not row.document_md:
        raise HTTPException(status_code=404, detail="아직 생성된 인수인계서가 없습니다.")
    title = f"{row.owner_name} 인수인계서"
    if format == "pdf":
        content = to_pdf(row.document_md, title=title)
    elif format == "docx":
        content = to_docx(row.document_md, title=title)
    else:
        content = row.document_md.encode("utf-8")
    base = re.sub(r"[^\w가-힣.-]+", "_", f"인수인계서_{row.owner_name}_v{row.document_version}").strip("_")
    filename = f"{base}.{format}"
    headers = {"Content-Disposition": f"attachment; filename=\"handover.{format}\"; filename*=UTF-8''{quote(filename)}"}
    return Response(content=content, media_type=MEDIA_TYPES[format], headers=headers)
