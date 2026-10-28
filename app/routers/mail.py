"""메일 조회 API: 업무 자료를 올릴 때 메일함에서 업무 메일을 찾아 골라 가져온다.

- GET  /api/mail/messages                 메일 목록(검색어·제목·관련 사람·기간·보낸 사람·첨부 여부, task_id 를 주면 단위업무 관련도순)
- GET  /api/mail/messages/{message_id}    메일 상세(본문·첨부 목록)
- POST /api/sessions/{session_id}/mails   고른 메일을 업무 자료(.eml)로 가져오기 → 적재
- POST /api/unit-tasks/{task_id}/mails    단위업무로 가져오기 → 적재가 끝나면 AI 분석 자동 시작(app/routers/unit_tasks.py)

지금 연결된 메일 조회 API 는 가상 메일함(app/services/mail.py 의 MockMailProvider)이다. 메일함은 로그인 계정 기준이다.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from ..models import FileBlob, HandoverSession, Source, UnitTask, User
from ..schemas import MailAddressOut, MailAttachmentOut, MailDetail, MailImportIn, MailImportResult, MailListOut, MailSummary, SourceOut
from ..services.container import Services
from ..services.mail import KST, MailAddress, MailMessage, MailQuery, build_eml, eml_file_name, owner_address, score, task_keywords
from ..services.storage import UploadRejected
from .deps import current_user, get_db, get_services, load_session

router = APIRouter(prefix="/api/mail", tags=["mail"])
session_router = APIRouter(prefix="/api/sessions/{session_id}", tags=["mail"])

MAIL_LINK_TYPE = "mail"  # 메일에서 가져온 자료는 sources.link_type=mail, url=mail:<메일 ID> 로 표시한다


def mailbox_of(user: User) -> MailAddress:
    return owner_address(user.login_id, user.name)


def _address(a: MailAddress) -> MailAddressOut:
    return MailAddressOut(name=a.name, email=a.email)


def _summary(m: MailMessage, relevance: int, imported: set[str]) -> MailSummary:
    return MailSummary(
        id=m.id,
        folder=m.folder,
        subject=m.subject,
        sender=_address(m.sender),
        to=[_address(a) for a in m.to],
        sent_at=m.sent_at,
        snippet=m.snippet,
        has_attachments=bool(m.attachments),
        attachment_count=len(m.attachments),
        important=m.important,
        relevance=relevance,
        recommended=relevance > 0,
        imported=m.id in imported,
    )


def imported_ids(row: HandoverSession | None) -> set[str]:
    if row is None:
        return set()
    return {s.url.split(":", 1)[1] for s in row.sources if s.link_type == MAIL_LINK_TYPE and s.status != "failed" and ":" in s.url}


def _context(db: Session, user: User, task_id: str | None, session_id: str | None) -> tuple[HandoverSession | None, list[str]]:
    """관련도·'이미 가져옴' 표시에 쓸 인수인계 세션과 단위업무 낱말."""
    if task_id:
        task = db.get(UnitTask, task_id)
        if task is None or task.user_id != user.id:
            raise HTTPException(status_code=404, detail="단위업무를 찾을 수 없습니다.")
        return task.session, task_keywords(task.name, task.description)
    if session_id:
        row = db.get(HandoverSession, session_id)
        if row is None or (row.user_id.startswith("user:") and row.user_id != user.owner_key):
            raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
        return row, task_keywords(row.task_name or row.title, row.duties)
    return None, []


@router.get("/messages", response_model=MailListOut, summary="메일 목록 조회(가상 메일 조회 API)")
def list_messages(
    q: str = Query(default="", max_length=100, description="제목·본문·보낸 사람·첨부파일 이름 검색(띄어 쓴 낱말은 모두 포함)"),
    subject: str = Query(default="", max_length=100, description="제목 검색(제목에 모든 낱말이 들어 있는 메일만)"),
    person: str = Query(default="", max_length=100, description="관련 사람 이름 또는 주소 일부(보낸 사람·받는 사람·참조 중 하나라도 맞으면)"),
    sender: str = Query(default="", max_length=100, description="보낸 사람 이름 또는 주소 일부"),
    folder: Literal["", "inbox", "sent"] = Query(default="", description="inbox(받은 메일) | sent(보낸 메일) | 비우면 전체"),
    date_from: date | None = Query(default=None, description="이 날짜부터(YYYY-MM-DD)"),
    date_to: date | None = Query(default=None, description="이 날짜까지(YYYY-MM-DD)"),
    has_attachments: bool | None = Query(default=None, description="true 면 첨부가 있는 메일만"),
    important: bool | None = Query(default=None, description="true 면 중요 표시한 메일만"),
    task_id: str | None = Query(default=None, description="단위업무 ID. 주면 관련도순으로 정렬하고 이미 가져온 메일을 표시"),
    session_id: str | None = Query(default=None, description="세션 ID(task_id 대신)"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=50),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
):
    row, keywords = _context(db, user, task_id, session_id)
    query = MailQuery(
        q=q.strip(),
        subject=subject.strip(),
        person=person.strip(),
        sender=sender,
        folder=folder,
        date_from=datetime.combine(date_from, time.min, KST) if date_from else None,
        date_to=datetime.combine(date_to, time.max, KST) if date_to else None,
        has_attachments=has_attachments,
        important=important,
        keywords=keywords,
        team=user.profile.team if user.profile else "",
        page=page,
        size=size,
    )
    owner = mailbox_of(user)
    result = services.mail.search(owner, query)
    done = imported_ids(row)
    return MailListOut(
        message=f"메일 {result.total}건을 찾았습니다." if result.total else "조건에 맞는 메일이 없습니다.",
        provider=services.mail.name,
        mailbox=_address(owner),
        messages=[_summary(m, rel, done) for m, rel in result.items],
        total=result.total,
        page=page,
        size=size,
        has_next=page * size < result.total,
        keywords=keywords,
    )


@router.get("/messages/{message_id}", response_model=MailDetail, summary="메일 상세(본문·첨부 목록)")
def get_message(
    message_id: str,
    task_id: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
):
    row, keywords = _context(db, user, task_id, session_id)
    m = services.mail.get(mailbox_of(user), message_id)
    if m is None:
        raise HTTPException(status_code=404, detail="메일을 찾을 수 없습니다.")
    return MailDetail(
        **_summary(m, score(m, keywords) if keywords else 0, imported_ids(row)).model_dump(),
        cc=[_address(a) for a in m.cc],
        body=m.body,
        attachments=[MailAttachmentOut(name=a.name, size=a.size, content_type=a.content_type) for a in m.attachments],
    )


def import_mails(db: Session, services: Services, row: HandoverSession, user: User, message_ids: list[str]) -> tuple[list[Source], list[str], list[str]]:
    """고른 메일을 .eml 로 만들어 업로드 파일과 같이 저장한다(원본은 file_blobs, 파싱용 사본은 디스크).

    (만든 자료, 적재할 source_id, 이미 가져와서 건너뛴 메일 제목) 을 돌려준다. 없는 메일 ID 가 있으면 아무것도 저장하지 않고 404.
    """
    owner = mailbox_of(user)
    ids = list(dict.fromkeys(message_ids))
    messages = [services.mail.get(owner, mid) for mid in ids]
    missing = [mid for mid, m in zip(ids, messages) if m is None]
    if missing:
        raise HTTPException(status_code=404, detail=f"메일을 찾을 수 없습니다: {', '.join(missing)}")
    done = imported_ids(row)
    created: list[Source] = []
    accepted: list[str] = []
    skipped: list[str] = []
    for m in messages:
        if m.id in done:
            skipped.append(m.subject)
            continue
        source_id = uuid.uuid4().hex[:12]
        content = build_eml(m)
        source = Source(
            id=source_id, session_id=row.id, kind="file", name=eml_file_name(m)[:300], extension=".eml",
            url=f"mail:{m.id}", link_type=MAIL_LINK_TYPE, added_stage=row.stage,
        )
        blob: FileBlob | None = None
        try:
            path, name, size = services.storage.save_bytes(row.id, source_id, source.name, content)
            blob = FileBlob(source_id=source_id, content_type="message/rfc822", sha256=hashlib.sha256(content).hexdigest(), content=content)
            source.name, source.stored_path, source.size, source.status = name, str(path), size, "pending"
            accepted.append(source_id)
        except UploadRejected as exc:
            source.status, source.error = "failed", str(exc)
        db.add(source)
        if blob is not None:
            db.flush()  # sources 행이 먼저 있어야 file_blobs 외래키가 맞는다
            db.add(blob)
        created.append(source)
    db.commit()
    return created, accepted, skipped


def import_message(created: list[Source], accepted: list[str], skipped: list[str]) -> str:
    parts = []
    if accepted:
        parts.append(f"메일 {len(accepted)}건을 업무 자료로 가져왔습니다.")
    if skipped:
        parts.append(f"{len(skipped)}건은 이미 가져온 메일이라 건너뛰었습니다.")
    failed = [s for s in created if s.status == "failed"]
    if failed:
        parts.append(f"{len(failed)}건은 가져오지 못했습니다({' / '.join(f'{s.name}: {s.error}' for s in failed)}).")
    return " ".join(parts) or "가져올 메일이 없습니다."


@session_router.post("/mails", response_model=MailImportResult, status_code=status.HTTP_201_CREATED, summary="고른 메일을 업무 자료로 가져오기")
def import_session_mails(
    session_id: str,
    body: MailImportIn,
    response: Response,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
):
    row = load_session(db, session_id)
    created, accepted, skipped = import_mails(db, services, row, user, body.message_ids)
    services.runner.run_ingest(session_id, accepted)  # 분석이 끝난 세션이면 새 자료로 재분석이 이어진다
    if not accepted and not skipped:
        response.status_code = status.HTTP_400_BAD_REQUEST
    return MailImportResult(
        success=bool(accepted) or bool(skipped),
        message=import_message(created, accepted, skipped),
        session_id=session_id,
        files=[SourceOut.model_validate(s) for s in created],
        skipped=skipped,
        analysis_scheduled=bool(accepted) and row.stage != "setup",
    )
