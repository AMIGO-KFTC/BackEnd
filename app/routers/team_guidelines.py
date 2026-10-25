"""팀 인수인계서 지침: 관리자가 '우리 팀 인수인계서에 꼭 들어가야 하는 항목'과 '팀 특징·작성 지침'을 적는다.

지침은 소속(프로필의 부서 + 팀) 단위로 저장하고, 그 소속의 인수인계 세션이 AI 분석을 시작할 때 에이전트 프롬프트에 반영된다
(app/services/runner.py → AI 의 prompts.team_guidelines_block). 이미 분석을 마친 세션에는 다음 분석(다시 분석)부터 적용된다.

관리자 권한: 아직 사용자 역할이 없어, AMIGO_TEAM_GUIDELINE_EDITORS(쉼표로 구분한 로그인 아이디)를 정하면 그 계정만 저장할 수 있다.
비워 두면(기본) 로그인한 누구나 자기 소속의 지침을 저장할 수 있으므로, 운영할 때는 꼭 정해 두어야 한다.
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import TeamDocument, TeamGuideline, User
from ..schemas import TeamDocumentOut, TeamGlossaryTerm, TeamGuidelineIn, TeamGuidelineItem, TeamGuidelineOut
from ..services.container import Services
from ..services.guidelines import organization_key, prompt_preview, stored_glossary, stored_items
from ..services.storage import display_name, extension_of
from .deps import current_user, get_db, get_services

router = APIRouter(prefix="/api/team-guidelines", tags=["team-guidelines"])

NO_ORGANIZATION = "프로필에 부서·팀이 없어 팀 지침을 쓸 수 없습니다. 먼저 프로필을 입력해 주세요."
NOT_EDITOR = "팀 지침을 수정할 권한이 없습니다. 관리자에게 요청해 주세요."


def _organization(user: User) -> str:
    organization = organization_key(user.profile.organization) if user.profile else ""
    if not organization:
        raise HTTPException(status_code=400, detail=NO_ORGANIZATION)
    return organization


def _editable(user: User, settings: Settings) -> bool:
    editors = settings.team_guideline_editor_ids
    return not editors or user.login_id.lower() in editors


MAX_TEAM_DOCUMENTS = 30  # 소속마다 올려 둘 수 있는 팀 공통 업무 자료 수


def _documents(db: Session, organization: str) -> list[TeamDocumentOut]:
    rows = db.scalars(select(TeamDocument).where(TeamDocument.organization == organization).order_by(TeamDocument.uploaded_at.desc()))
    return [TeamDocumentOut(id=r.id, name=r.name, size=r.size, uploaded_by=r.uploaded_by, uploaded_at=r.uploaded_at) for r in rows]


def _out(row: TeamGuideline | None, organization: str, editable: bool, message: str = "", documents: list[TeamDocumentOut] | None = None) -> TeamGuidelineOut:
    items = [TeamGuidelineItem(**item) for item in stored_items(row)]
    return TeamGuidelineOut(
        message=message,
        organization=organization,
        items=items,
        notes=row.notes if row else "",
        prompt=(row.prompt or "") if row else "",
        intro=(row.intro or "") if row else "",
        glossary=[TeamGlossaryTerm(**t) for t in stored_glossary(row)],
        documents=documents or [],
        updated_at=row.updated_at if row else None,
        updated_by=row.updated_by if row else "",
        editable=editable,
        prompt_preview=prompt_preview(row),
    )


@router.get("/me", response_model=TeamGuidelineOut, summary="내 소속(부서 + 팀)의 인수인계서 지침 조회")
def get_my_guidelines(user: User = Depends(current_user), db: Session = Depends(get_db), services: Services = Depends(get_services)):
    organization = _organization(user)
    return _out(db.get(TeamGuideline, organization), organization, _editable(user, services.settings), documents=_documents(db, organization))


@router.put("/me", response_model=TeamGuidelineOut, summary="내 소속의 인수인계서 지침 저장(항목과 팀 특징·작성 지침)")
def save_my_guidelines(
    body: TeamGuidelineIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
):
    organization = _organization(user)
    if not _editable(user, services.settings):
        raise HTTPException(status_code=403, detail=NOT_EDITOR)
    row = db.get(TeamGuideline, organization) or TeamGuideline(organization=organization)
    if body.items is not None:
        seen: set[str] = set()
        items: list[dict] = []
        for item in body.items:
            key = " ".join(item.name.split()).lower()
            if not key:
                continue
            if key in seen:
                raise HTTPException(status_code=422, detail=f"같은 이름의 항목이 둘 이상 있습니다: {item.name}")
            seen.add(key)
            items.append({"id": item.id or uuid.uuid4().hex[:8], "name": " ".join(item.name.split()), "instruction": item.instruction})
        row.items_json = json.dumps(items, ensure_ascii=False)
    if body.notes is not None:
        row.notes = body.notes
    if body.prompt is not None:
        row.prompt = body.prompt
    if body.intro is not None:
        row.intro = body.intro
    if body.glossary is not None:
        seen_terms: set[str] = set()
        terms: list[dict] = []
        for entry in body.glossary:
            key = " ".join(entry.term.split()).lower()
            if not key:
                continue
            if key in seen_terms:
                raise HTTPException(status_code=422, detail=f"같은 용어가 둘 이상 있습니다: {entry.term}")
            seen_terms.add(key)
            terms.append({"term": " ".join(entry.term.split()), "meaning": " ".join(entry.meaning.split())})
        row.glossary_json = json.dumps(terms, ensure_ascii=False)
    row.updated_by = user.name
    db.add(row)
    db.commit()
    db.refresh(row)
    return _out(row, organization, True, "팀 지침이 저장되었습니다. 이후 AI 분석부터 반영돼요.", _documents(db, organization))


@router.post("/me/documents", response_model=list[TeamDocumentOut], status_code=status.HTTP_201_CREATED, summary="팀 공통 업무 자료 올리기(STEP3)")
async def upload_team_documents(
    files: list[UploadFile] = File(..., description="업무정의서·업무 설계서·팀원 업무분장표·업무 규약 및 시행세칙 등"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
):
    organization = _organization(user)
    if not _editable(user, services.settings):
        raise HTTPException(status_code=403, detail=NOT_EDITOR)
    storage = services.storage
    current = len(_documents(db, organization))
    if current + len(files) > MAX_TEAM_DOCUMENTS:
        raise HTTPException(status_code=400, detail=f"팀 자료는 최대 {MAX_TEAM_DOCUMENTS}개까지 올릴 수 있어요. 안 쓰는 자료를 지우고 올려 주세요.")
    created: list[TeamDocument] = []
    for upload in files:
        name = display_name(upload.filename)
        ext = extension_of(name)
        try:
            if ext not in storage.allowed:
                raise HTTPException(status_code=400, detail=f"{name}: 지원하지 않는 형식입니다({ext or '확장자 없음'}).")
            content = await upload.read(storage.max_bytes + 1)
            if not content:
                raise HTTPException(status_code=400, detail=f"{name}: 빈 파일입니다.")
            if len(content) > storage.max_bytes:
                raise HTTPException(status_code=400, detail=f"{name}: 파일이 너무 큽니다(최대 {storage.max_bytes // (1024 * 1024)}MB).")
        finally:
            await upload.close()
        created.append(
            TeamDocument(
                id=uuid.uuid4().hex[:12],
                organization=organization,
                name=name,
                extension=ext,
                size=len(content),
                content_type=(upload.content_type or "application/octet-stream")[:200],
                content=content,
                uploaded_by=user.name,
            )
        )
    db.add_all(created)
    db.commit()
    return [TeamDocumentOut(id=d.id, name=d.name, size=d.size, uploaded_by=d.uploaded_by, uploaded_at=d.uploaded_at) for d in created]


@router.delete("/me/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT, summary="팀 공통 업무 자료 지우기")
def delete_team_document(document_id: str, user: User = Depends(current_user), db: Session = Depends(get_db), services: Services = Depends(get_services)):
    organization = _organization(user)
    if not _editable(user, services.settings):
        raise HTTPException(status_code=403, detail=NOT_EDITOR)
    document = db.get(TeamDocument, document_id)
    if document is None or document.organization != organization:
        raise HTTPException(status_code=404, detail="자료를 찾을 수 없습니다.")
    db.delete(document)
    db.commit()
