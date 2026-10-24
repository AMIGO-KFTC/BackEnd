"""자료 등록: 파일 업로드(/upload)와 링크 등록(/links)."""

from __future__ import annotations

import hashlib
import io
import re
import uuid
import zipfile
from urllib.parse import quote, urlparse

from amigo_rag import detect_link_type
from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import FileBlob, HandoverSession, Message, Source
from ..schemas import LinksIn, ReindexResult, SourceOut
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
    files: list[UploadFile] = File(..., description="PDF, HWPX, HWP, DOCX, PPTX, XLSX, EML, PNG·JPG(스크린샷), 소스코드, ZIP 등"),
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


def _archive_name(name: str, used: set[str]) -> str:
    """ZIP 안의 파일 이름. 경로 문자는 바꾸고, 같은 이름이 또 나오면 '이름 (2).확장자' 로 구분한다."""
    clean = re.sub(r'[\\/:*?"<>|\r\n]+', "_", name).strip() or "파일"
    stem, dot, ext = clean.rpartition(".")
    if not dot:
        stem, ext = clean, ""
    candidate, n = clean, 2
    while candidate.lower() in used:
        candidate = f"{stem} ({n}){'.' + ext if ext else ''}"
        n += 1
    used.add(candidate.lower())
    return candidate


@router.get("/sources/archive", summary="올린 파일 원본을 ZIP 한 개로 내려받기(완성 문서의 '사용한 자료' 전체)")
def download_sources_archive(session_id: str, db: Session = Depends(get_db)):
    """이 인수인계서를 만드는 데 올린 파일(처리에 실패한 파일·링크 제외) 원본을 한 번에 받는다. 메일(.eml)·메일 캡처 그림도 파일이라 함께 들어간다."""
    row = load_session(db, session_id)
    files = [s for s in row.sources if s.kind == "file" and s.status != "failed"]
    blobs = {b.source_id: b for b in db.scalars(select(FileBlob).where(FileBlob.source_id.in_([s.id for s in files])))} if files else {}
    buffer, used, count = io.BytesIO(), set(), 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(files, key=lambda s: s.created_at):
            blob = blobs.get(source.id)
            if blob is not None:
                archive.writestr(_archive_name(source.name, used), blob.content)
                count += 1
    if not count:
        raise HTTPException(status_code=404, detail="내려받을 자료 파일이 없습니다.")
    name = re.sub(r"[^\w가-힣.-]+", "_", f"인수인계_사용자료_{row.owner_name or row.id[:6]}_{count}개").strip("_") + ".zip"
    headers = {"Content-Disposition": f"attachment; filename=\"sources.zip\"; filename*=UTF-8''{quote(name)}", "X-Content-Type-Options": "nosniff"}
    return Response(content=buffer.getvalue(), media_type="application/zip", headers=headers)


@router.get("/sources/{source_id}/file", summary="올린 파일 원본 내려받기(완성 문서 화면의 '출처' 목록)")
def download_source_file(session_id: str, source_id: str, db: Session = Depends(get_db)):
    row = load_session(db, session_id)
    source = db.get(Source, source_id)
    blob = db.get(FileBlob, source_id) if source is not None and source.session_id == row.id else None
    if blob is None:
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
    headers = {
        "Content-Disposition": f"attachment; filename=\"file{source.extension}\"; filename*=UTF-8''{quote(source.name)}",
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=blob.content, media_type="application/octet-stream", headers=headers)


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


@router.post("/reindex", response_model=ReindexResult, status_code=status.HTTP_202_ACCEPTED, summary="지식베이스 다시 적재(원본으로)")
def reindex(session_id: str, db: Session = Depends(get_db), services: Services = Depends(get_services)):
    """임베딩 설정을 바꿨거나 서버를 옮겨 검색 결과가 비었을 때, 처리 완료된 자료를 원본으로 다시 적재한다.

    대화와 정리된 내용은 그대로 두고 지식베이스만 다시 만든다. 진행 상황은 /state 의 sources[].status 로 본다.
    """
    row = load_session(db, session_id)
    if services.runner.is_busy(session_id):
        raise HTTPException(status_code=409, detail="AI가 처리 중입니다. 끝난 뒤 다시 시도해 주세요.")
    if any(s.status in ("pending", "processing") for s in row.sources):
        raise HTTPException(status_code=409, detail="자료를 처리하는 중입니다. 처리가 끝나면 다시 시도해 주세요.")
    count = services.runner.reindex(session_id)
    if not count:
        raise HTTPException(status_code=400, detail="다시 적재할 자료가 없습니다. 처리가 끝난 자료가 있어야 합니다.")
    return ReindexResult(message=f"자료 {count}건을 다시 적재합니다.", source_count=count)
