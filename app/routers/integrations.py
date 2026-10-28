"""외부 협업 도구 연동(가상): Mattermost 최근 대화 목록·Confluence 페이지 검색·Jira 이슈/진행 현황과, 고른 항목을 인수인계 자료로 등록.

실제 Mattermost·Confluence API 는 쓸 수 없으므로 app/mock/mattermost.py · app/mock/confluence.py 의 예시 데이터로 같은 화면 흐름을 만든다.
고른 대화·페이지는 Markdown 텍스트 파일 자료(kind=file, link_type=mattermost|confluence)로 저장해, 가상 모드든 실제 모드든
다른 업로드 파일과 똑같이 지식베이스에 적재되고 인수인계서 근거로 쓰인다.
"""

from __future__ import annotations

import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..mock import confluence, jira, mattermost
from ..models import FileBlob, HandoverSession, Source, User
from ..schemas import ConfluenceImportIn, IntegrationImportResult, JiraImportIn, JiraMoveIn, MattermostImportIn, SourceOut
from ..services.container import Services
from .deps import get_db, get_services, load_session, optional_user

router = APIRouter(prefix="/api/integrations", tags=["integrations"])
session_router = APIRouter(prefix="/api/sessions/{session_id}/integrations", tags=["integrations"])

MOCK_NOTE = "가상 데이터입니다. 실제 Mattermost·Confluence·Jira 는 연동되어 있지 않습니다."


def _team(user: User | None) -> str:
    return user.profile.team if user is not None and user.profile else ""


# ------------------------------------------------------------------ 조회(가상 Mattermost)
@router.get("/mattermost/conversations", summary="Mattermost 최근 대화 목록(가상)")
def mattermost_conversations(q: str = Query("", max_length=100, description="대화 이름·참여자·내용 검색"), limit: int = Query(50, ge=1, le=100)):
    rows = mattermost.list_conversations(q, limit)
    return {"mock": True, "note": MOCK_NOTE, "me": mattermost.USERS[mattermost.ME]["name"], "total": len(rows), "conversations": rows}


@router.get("/mattermost/conversations/{conversation_id}", summary="Mattermost 대화 내용(가상)")
def mattermost_conversation(conversation_id: str):
    row = mattermost.get_conversation(conversation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다.")
    return {"mock": True, **row.detail()}


# ------------------------------------------------------------------ 조회(가상 Confluence)
@router.get("/confluence/search", summary="Confluence 페이지 검색(가상): 제목·본문·라벨에 검색어가 들어간 페이지")
def confluence_search(
    q: str = Query("", max_length=100, description="검색어(띄어쓰기로 여러 개, 모두 포함)"),
    space: str = Query("", max_length=20, description="스페이스 키로 좁히기(비우면 전체)"),
    limit: int = Query(20, ge=1, le=50),
    user: User | None = Depends(optional_user),
):
    """로그인한 사용자의 팀(profile.team)에 맞는 팀 스페이스를 보여 준다(팀별 데이터가 없는 팀은 기본 스페이스)."""
    return {"mock": True, "note": MOCK_NOTE, **confluence.search_pages(q, space, limit, team=_team(user))}


@router.get("/confluence/pages/{page_id}", summary="Confluence 페이지 본문(가상)")
def confluence_page(page_id: str):
    page = confluence.get_page(page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="페이지를 찾을 수 없습니다.")
    return {"mock": True, **page.detail()}


# ------------------------------------------------------------------ 조회(가상 Jira)
@router.get("/jira/projects", summary="Jira 프로젝트 목록과 진행 현황(가상): 현재 스프린트 진척·에픽별 진행률·담당자별 남은 일·막힌 이슈")
def jira_projects(user: User | None = Depends(optional_user)):
    """로그인한 사용자의 팀(profile.team)에 맞는 프로젝트를 보여 준다(팀별 데이터가 없는 팀은 기본 프로젝트)."""
    return {"mock": True, "note": MOCK_NOTE, "today": jira.TODAY.isoformat(), "projects": jira.list_projects(_team(user))}


@router.get("/jira/search", summary="Jira 이슈 검색(가상): 검색어·프로젝트·상태·담당자·유형으로 거르기")
def jira_search(
    q: str = Query("", max_length=100, description="검색어(띄어쓰기로 여러 개, 모두 포함). 키·요약·설명·라벨·사람·댓글"),
    project: str = Query("", max_length=20),
    status: str = Query("", max_length=20),
    assignee: str = Query("", max_length=50),
    type: str = Query("", max_length=20, description="에픽 | 스토리 | 작업 | 버그 | 하위 작업"),
    limit: int = Query(100, ge=1, le=200),
    user: User | None = Depends(optional_user),
):
    return {"mock": True, "note": MOCK_NOTE, **jira.search_issues(q, project, status, assignee, type, limit, team=_team(user))}


@router.get("/jira/issues/{key}", summary="Jira 이슈 상세(가상): 설명·댓글·하위 이슈(에픽)·하위 작업")
def jira_issue(key: str):
    issue = jira.get_issue(key)
    if issue is None:
        raise HTTPException(status_code=404, detail="이슈를 찾을 수 없습니다.")
    return {"mock": True, **jira.issue_detail(issue)}


@router.patch("/jira/issues/{key}", summary="Jira 이슈 상태 바꾸기(가상): 보드에서 카드를 다른 열로 옮길 때. 서버 메모리에만 반영")
def jira_move_issue(key: str, body: JiraMoveIn):
    issue = jira.get_issue(key)
    if issue is None:
        raise HTTPException(status_code=404, detail="이슈를 찾을 수 없습니다.")
    try:
        jira.move_issue(issue, body.status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"mock": True, **jira.issue_summary(issue)}


# ------------------------------------------------------------------ 세션 자료로 등록
def _add_text_source(db: Session, services: Services, row: HandoverSession, *, name: str, url: str, link_type: str, markdown: str) -> Source:
    """Markdown 텍스트를 업로드 파일과 같은 방식(DB 원본 + 디스크 사본)으로 자료 등록한다."""
    source_id = uuid.uuid4().hex[:12]
    content = markdown.encode("utf-8")
    target_dir = services.storage.session_dir(row.id)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{source_id}.md"
    path.write_bytes(content)
    source = Source(
        id=source_id, session_id=row.id, kind="file", name=name[:300], url=url, link_type=link_type, extension=".md",
        size=len(content), stored_path=str(path), status="pending", added_stage=row.stage,
    )
    db.add(source)
    db.flush()
    db.add(FileBlob(source_id=source_id, content_type="text/markdown", sha256=hashlib.sha256(content).hexdigest(), content=content))
    return source


def _existing_urls(row: HandoverSession, link_type: str) -> set[str]:
    return {s.url for s in row.sources if s.link_type == link_type}


def _result(services: Services, row: HandoverSession, created: list[Source], skipped: list[str], label: str) -> IntegrationImportResult:
    if not created and skipped:
        raise HTTPException(status_code=409, detail=f"이미 등록된 {label}입니다: {', '.join(skipped)}")
    services.runner.run_ingest(row.id, [s.id for s in created])
    message = f"{label} {len(created)}건을 자료로 등록했습니다."
    if skipped:
        message += f" 이미 등록된 {len(skipped)}건은 건너뛰었습니다."
    return IntegrationImportResult(message=message, sources=[SourceOut.model_validate(s) for s in created], skipped=skipped)


@session_router.post("/mattermost", response_model=IntegrationImportResult, status_code=status.HTTP_201_CREATED, summary="고른 Mattermost 대화를 자료로 등록")
def import_mattermost(session_id: str, body: MattermostImportIn, db: Session = Depends(get_db), services: Services = Depends(get_services)):
    row = load_session(db, session_id)
    existing = _existing_urls(row, "mattermost")
    created: list[Source] = []
    skipped: list[str] = []
    for conversation_id in dict.fromkeys(body.conversation_ids):
        conv = mattermost.get_conversation(conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail=f"대화를 찾을 수 없습니다: {conversation_id}")
        url = conv.summary()["url"]
        if url in existing:
            skipped.append(conv.display_name)
            continue
        created.append(_add_text_source(db, services, row, name=f"Mattermost · {conv.display_name}", url=url, link_type="mattermost", markdown=conv.to_markdown()))
    db.commit()
    return _result(services, row, created, skipped, "Mattermost 대화")


@session_router.post("/confluence", response_model=IntegrationImportResult, status_code=status.HTTP_201_CREATED, summary="고른 Confluence 페이지를 자료로 등록")
def import_confluence(session_id: str, body: ConfluenceImportIn, db: Session = Depends(get_db), services: Services = Depends(get_services)):
    row = load_session(db, session_id)
    existing = _existing_urls(row, "confluence")
    created: list[Source] = []
    skipped: list[str] = []
    for page_id in dict.fromkeys(body.page_ids):
        page = confluence.get_page(page_id)
        if page is None:
            raise HTTPException(status_code=404, detail=f"페이지를 찾을 수 없습니다: {page_id}")
        if page.url in existing:
            skipped.append(page.title)
            continue
        created.append(_add_text_source(db, services, row, name=f"컨플루언스 · {page.title}", url=page.url, link_type="confluence", markdown=page.to_markdown()))
    db.commit()
    return _result(services, row, created, skipped, "컨플루언스 페이지")


@session_router.post("/jira", response_model=IntegrationImportResult, status_code=status.HTTP_201_CREATED, summary="고른 Jira 이슈·에픽·프로젝트 진행 현황을 자료로 등록")
def import_jira(session_id: str, body: JiraImportIn, db: Session = Depends(get_db), services: Services = Depends(get_services)):
    """항목 ID 는 이슈 키(AMG-13) 또는 'project:<키>'(프로젝트 진행 현황). 에픽은 하위 이슈와 진행률을 함께 담는다."""
    row = load_session(db, session_id)
    existing = _existing_urls(row, "jira")
    created: list[Source] = []
    skipped: list[str] = []
    for item_id in dict.fromkeys(body.item_ids):
        found = jira.resolve(item_id)
        if found is None:
            raise HTTPException(status_code=404, detail=f"지라 항목을 찾을 수 없습니다: {item_id}")
        name, url, markdown = found
        if url in existing:
            skipped.append(name)
            continue
        created.append(_add_text_source(db, services, row, name=name, url=url, link_type="jira", markdown=markdown))
    db.commit()
    return _result(services, row, created, skipped, "지라 항목")

