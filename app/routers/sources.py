"""자료 등록: 파일 업로드(/upload)와 링크 등록(/links)."""

from __future__ import annotations

import hashlib
import uuid
from urllib.parse import urlparse

from amigo_rag import detect_link_type
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from ..models import FileBlob, HandoverSession, Message, Source
from ..schemas import LinksIn, SourceOut
from ..services.container import Services
from ..services.storage import UploadRejected, display_name, extension_of
from .deps import get_db, get_services, load_session

router = APIRouter(prefix="/api/sessions/{session_id}", tags=["sources"])


async def store_uploads(db: Session, services: Services, row: HandoverSession, files: list[UploadFile]) -> tuple[list[Source], list[str]]:
    """업로드 파일을 저장한다: 원본 바이트는 DB(file_blobs), 메타데이터는 sources, RAG 파싱용 사본은 디스크.

    (만든 자료 목록, 적재할 source_id 목록) 을 돌려준다. 거절된 파일은 status=failed 로 남긴다.
    """
    if len(files) > services.settings.max_files_per_upload:
        raise HTTPException(status_code=400, detail=f"한 번에 최대 {services.settings.max_files_per_upload}개까지 올릴 수 있습니다.")
    created: list[Source] = []
    accepted: list[str] = []
    for upload in files:
        source_id = uuid.uuid4().hex[:12]
        name = display_name(upload.filename)
        source = Source(id=source_id, session_id=row.id, kind="file", name=name, extension=extension_of(name), added_stage=row.stage)
        blob: FileBlob | None = None
        try:
            path, name, size = await services.storage.save(row.id, source_id, upload)
            content = path.read_bytes()
            blob = FileBlob(
                source_id=source_id,
                content_type=(upload.content_type or "application/octet-stream")[:200],
                sha256=hashlib.sha256(content).hexdigest(),
                content=content,
            )
            source.stored_path, source.size, source.status = str(path), size, "pending"
            accepted.append(source_id)
        except UploadRejected as exc:
            source.status, source.error = "failed", str(exc)
        finally:
            await upload.close()
        db.add(source)
        if blob is not None:
            db.flush()  # sources 행이 먼저 있어야 file_blobs 외래키가 맞는다
            db.add(blob)
        created.append(source)
    db.commit()
    return created, accepted


@router.post("/upload", response_model=list[SourceOut], status_code=status.HTTP_201_CREATED, summary="파일 업로드(여러 개)")
async def upload_files(
    session_id: str,
    files: list[UploadFile] = File(..., description="PDF, HWPX, HWP, DOCX, PPTX, XLSX, EML, 소스코드, ZIP 등"),
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
):
    created, accepted = await store_uploads(db, services, load_session(db, session_id), files)
    services.runner.run_ingest(session_id, accepted)
    return [SourceOut.model_validate(s) for s in created]


@router.post("/links", response_model=list[SourceOut], status_code=status.HTTP_201_CREATED, summary="컨플루언스·나누미·웹 링크 등록")
def add_links(session_id: str, body: LinksIn, db: Session = Depends(get_db), services: Services = Depends(get_services)):
    row = load_session(db, session_id)
    created: list[Source] = []
    for raw in body.urls:
        url = raw.strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise HTTPException(status_code=400, detail=f"올바른 링크가 아닙니다: {url}")
        link_type = body.link_type if body.link_type != "auto" else detect_link_type(url, services.rag_settings)
        source = Source(
            id=uuid.uuid4().hex[:12], session_id=session_id, kind="link", name=url[:300], url=url, link_type=link_type, added_stage=row.stage
        )
        db.add(source)
        created.append(source)
    db.commit()
    services.runner.run_ingest(session_id, [s.id for s in created])
    return [SourceOut.model_validate(s) for s in created]


@router.get("/sources", response_model=list[SourceOut], summary="등록 자료 목록")
def list_sources(session_id: str, db: Session = Depends(get_db)):
    return [SourceOut.model_validate(s) for s in load_session(db, session_id).sources]


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT, summary="자료 삭제")
def delete_source(session_id: str, source_id: str, db: Session = Depends(get_db), services: Services = Depends(get_services)):
    row = load_session(db, session_id)
    source = db.get(Source, source_id)
    if source is None or source.session_id != session_id:
        raise HTTPException(status_code=404, detail="자료를 찾을 수 없습니다.")
    if source.status in ("pending", "processing"):
        raise HTTPException(status_code=409, detail="자료를 처리하는 중입니다. 처리가 끝난 뒤 삭제해 주세요.")
    services.kb(session_id).delete_source(source_id)
    services.storage.remove(source.stored_path)
    db.delete(source)
    if row.stage not in ("setup",):
        db.add(
            Message(
                session_id=session_id,
                role="system",
                kind="info",
                content=f"'{source.name}' 자료를 삭제했습니다. 이미 정리된 내용에서도 빼려면 '다시 분석'을 눌러 주세요.",
            )
        )
    db.commit()
