"""단위업무 입력과 단위업무별 업무파일 업로드.

단위업무 1건마다 인수인계 세션(sessions) 1건을 만들어 자료·지식베이스(ChromaDB 컬렉션)·질의응답·인수인계서를 따로 둔다.
파일을 올리면(또는 메일 조회 API 에서 고른 메일을 가져오면) 원본을 DB 에 저장하고, 적재(파싱→청킹→임베딩)가 끝나는 대로
AI 분석을 자동으로 시작한다.
질의응답·문서 조회는 응답의 session_id 로 기존 /api/sessions/{session_id}/... API 를 그대로 쓴다.
"""

from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import FileBlob, HandoverSession, Source, UnitTask, User
from ..schemas import (
    FileUploadResult,
    MailImportIn,
    MailImportResult,
    Result,
    SourceOut,
    UnitTaskOut,
    UnitTaskResult,
    UnitTasksIn,
    UnitTasksResult,
    UnitTaskUpdate,
)
from ..services.container import Services
from .deps import current_user, get_db, get_services
from .mail import import_mails, import_message
from .sessions import new_handover_session, remove_handover_session
from .sources import store_uploads

router = APIRouter(prefix="/api/unit-tasks", tags=["unit-tasks"])


def _welcome(name: str) -> str:
    return (
        f"'{name}' 업무의 자료(업무정의서·회의자료·메일·소스코드 등)를 올려 주세요. "
        "자료 처리가 끝나면 바로 분석을 시작하고, 인수인계서 필수 항목 중 자료에 없는 내용을 하나씩 여쭤볼게요."
    )


def _duties(name: str, description: str) -> str:
    return f"{name}: {description}" if description else name


def task_out(task: UnitTask) -> UnitTaskOut:
    row = task.session
    return UnitTaskOut(
        id=task.id,
        name=task.name,
        description=task.description,
        sort_order=task.sort_order,
        session_id=row.id,
        mode=row.mode or "reassignment",
        successor=row.successor,
        successor_login_id=row.successor_login_id or "",
        handover_date=row.handover_date,
        stage=row.stage,
        status=row.status,
        file_count=sum(1 for s in row.sources if s.kind == "file"),
        document_version=row.document_version,
        created_at=task.created_at,
    )


def load_task(db: Session, user: User, task_id: str) -> UnitTask:
    task = db.get(UnitTask, task_id)
    if task is None or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="단위업무를 찾을 수 없습니다.")
    return task


def _name_taken(db: Session, user: User, name: str, exclude_id: str = "") -> bool:
    query = select(UnitTask.id).where(UnitTask.user_id == user.id, UnitTask.name == name, UnitTask.id != exclude_id)
    return db.scalar(query) is not None


@router.post("", response_model=UnitTasksResult, status_code=status.HTTP_201_CREATED, summary="단위업무 입력(여러 건)")
def create_tasks(body: UnitTasksIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.profile is None:
        raise HTTPException(status_code=400, detail="먼저 프로필(부서·직위·팀·이름)을 입력해 주세요.")
    names = [t.name for t in body.tasks]
    repeated = sorted({n for n in names if names.count(n) > 1})
    if repeated:
        raise HTTPException(status_code=400, detail=f"같은 이름의 단위업무가 중복되었습니다: {', '.join(repeated)}")
    taken = [n for n in names if _name_taken(db, user, n)]
    if taken:
        raise HTTPException(status_code=409, detail=f"이미 등록된 단위업무입니다: {', '.join(taken)}")

    profile = user.profile
    order = db.scalar(select(func.max(UnitTask.sort_order)).where(UnitTask.user_id == user.id)) or 0
    created: list[UnitTask] = []
    for item in body.tasks:
        order += 1
        row = new_handover_session(
            db,
            user_id=user.owner_key,
            owner_name=user.name,
            organization=profile.organization,
            position=profile.position,
            duties=_duties(item.name, item.description),
            title=f"{item.name} 인수인계",
            task_name=item.name,
            mode=item.mode,
            successor=item.successor,
            successor_login_id=item.successor_login_id,
            handover_date=item.handover_date,
            welcome=_welcome(item.name),
        )
        task = UnitTask(id=uuid.uuid4().hex, user_id=user.id, session=row, name=item.name, description=item.description, sort_order=order)
        db.add(task)
        created.append(task)
    db.commit()
    return UnitTasksResult(message=f"단위업무 {len(created)}건을 등록했습니다.", tasks=[task_out(t) for t in created])


@router.get("", response_model=UnitTasksResult, summary="내 단위업무 목록")
def list_tasks(user: User = Depends(current_user), db: Session = Depends(get_db)):
    tasks = db.scalars(select(UnitTask).where(UnitTask.user_id == user.id).order_by(UnitTask.sort_order, UnitTask.created_at))
    return UnitTasksResult(tasks=[task_out(t) for t in tasks])


@router.get("/{task_id}", response_model=UnitTaskResult, summary="단위업무 상세(진행 단계·상태·파일 수)")
def get_task(task_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return UnitTaskResult(task=task_out(load_task(db, user, task_id)))


@router.patch("/{task_id}", response_model=UnitTaskResult, summary="단위업무 수정(이름·설명·인계 유형·인수자·인계일)")
def update_task(task_id: str, body: UnitTaskUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    task = load_task(db, user, task_id)
    if body.name is not None and body.name != task.name:
        if _name_taken(db, user, body.name, exclude_id=task.id):
            raise HTTPException(status_code=409, detail=f"이미 등록된 단위업무입니다: {body.name}")
        task.name = body.name
    if body.description is not None:
        task.description = body.description
    row = task.session
    mode_changed = body.mode is not None and body.mode != (row.mode or "reassignment")
    row.title = f"{task.name} 인수인계"
    row.task_name = task.name
    row.duties = _duties(task.name, task.description)
    if body.mode is not None:
        row.mode = body.mode
    if body.successor is not None:
        row.successor = body.successor
    if body.successor_login_id is not None:
        row.successor_login_id = body.successor_login_id.lower()
    if body.handover_date is not None:
        row.handover_date = body.handover_date
    db.commit()
    message = "단위업무를 수정했습니다."
    if mode_changed and row.stage != "setup":  # 양식(장 구성)이 바뀌므로 다시 분석해야 문서에 반영된다
        message += " 인계 유형이 바뀌었으니 '다시 분석'을 눌러야 새 양식으로 정리됩니다."
    return UnitTaskResult(message=message, task=task_out(task))


@router.delete("/{task_id}", response_model=Result, summary="단위업무 삭제(파일·지식베이스·대화·인수인계서 포함)")
def delete_task(task_id: str, user: User = Depends(current_user), db: Session = Depends(get_db), services: Services = Depends(get_services)):
    task = load_task(db, user, task_id)
    row, name = task.session, task.name
    if services.runner.is_busy(row.id):
        raise HTTPException(status_code=409, detail="AI가 처리 중이라 삭제할 수 없습니다. 잠시 후 다시 시도해 주세요.")
    db.delete(task)
    db.flush()
    remove_handover_session(db, services, row)
    return Result(message=f"'{name}' 단위업무를 삭제했습니다.")


@router.post("/{task_id}/files", response_model=FileUploadResult, status_code=status.HTTP_201_CREATED, summary="업무파일 업로드 → DB 저장 → AI 분석 요청")
async def upload_task_files(
    task_id: str,
    response: Response,
    files: list[UploadFile] = File(..., description="PDF, HWPX, HWP, DOCX, PPTX, XLSX, EML, 소스코드, ZIP 등"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
):
    task = load_task(db, user, task_id)
    row = task.session
    created, accepted = await store_uploads(db, services, row, files)
    scheduled, over_budget = _ingest(services, row, accepted)
    failed = [s for s in created if s.status == "failed"]
    if not accepted:
        response.status_code = status.HTTP_400_BAD_REQUEST
        message = "파일을 저장하지 못했습니다. " + " / ".join(f"{s.name}: {s.error}" for s in failed)
    else:
        message = f"파일 {len(accepted)}개를 저장했습니다."
        if failed:
            message += f" {len(failed)}개는 저장하지 못했습니다({', '.join(s.name for s in failed)})."
        message += _analysis_note(scheduled, over_budget)
    return FileUploadResult(
        success=bool(accepted),
        message=message,
        task_id=task.id,
        session_id=row.id,
        files=[SourceOut.model_validate(s) for s in created],
        analysis_scheduled=scheduled,
    )


def _ingest(services: Services, row: HandoverSession, accepted: list[str]) -> tuple[bool, bool]:
    """저장한 자료를 적재한다. (AI 분석이 이어지는지, 예산 초과로 자동 분석을 건너뛰는지) 를 돌려준다."""
    over_budget = services.budget_exhausted() is not None
    auto = services.settings.auto_analyze and not over_budget
    services.runner.run_ingest(row.id, accepted, auto_analyze=auto)
    # 분석 전이면 자동 분석(설정), 분석 뒤면 새 자료로 재분석(기존 규칙)이 이어진다
    return bool(accepted) and (auto or row.stage != "setup"), over_budget


def _analysis_note(scheduled: bool, over_budget: bool) -> str:
    if scheduled:
        return " 자료 처리가 끝나면 AI 분석을 시작합니다."
    if over_budget:
        return " Claude API 예산을 모두 써서 AI 분석은 시작하지 않습니다."
    return " '분석 시작'을 눌러 AI 분석을 요청해 주세요."


@router.post("/{task_id}/mails", response_model=MailImportResult, status_code=status.HTTP_201_CREATED, summary="고른 메일을 업무 자료로 가져오기")
def import_task_mails(
    task_id: str,
    body: MailImportIn,
    response: Response,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
):
    """GET /api/mail/messages?task_id= 로 찾은 메일 ID 를 보내면 .eml 로 저장하고 파일 업로드와 똑같이 적재·분석한다."""
    task = load_task(db, user, task_id)
    row = task.session
    created, accepted, skipped = import_mails(db, services, row, user, body.message_ids)
    scheduled, over_budget = _ingest(services, row, accepted)
    message = import_message(created, accepted, skipped)
    if accepted:
        message += _analysis_note(scheduled, over_budget)
    elif not skipped:
        response.status_code = status.HTTP_400_BAD_REQUEST
    return MailImportResult(
        success=bool(accepted) or bool(skipped),
        message=message,
        session_id=row.id,
        task_id=task.id,
        files=[SourceOut.model_validate(s) for s in created],
        skipped=skipped,
        analysis_scheduled=scheduled,
    )


@router.get("/{task_id}/files", response_model=list[SourceOut], summary="단위업무에 올린 파일 목록(처리 상태 포함)")
def list_task_files(task_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    task = load_task(db, user, task_id)
    return [SourceOut.model_validate(s) for s in task.session.sources if s.kind == "file"]


@router.get("/{task_id}/files/{file_id}", summary="올린 파일 원본 내려받기(DB 에 저장된 원본)")
def download_task_file(task_id: str, file_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    task = load_task(db, user, task_id)
    source = db.get(Source, file_id)
    blob = db.get(FileBlob, file_id) if source is not None and source.session_id == task.session_id else None
    if blob is None:
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
    headers = {
        "Content-Disposition": f"attachment; filename=\"file{source.extension}\"; filename*=UTF-8''{quote(source.name)}",
        "X-Content-Type-Options": "nosniff",
    }
    return Response(content=blob.content, media_type="application/octet-stream", headers=headers)
