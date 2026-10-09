"""AMIGO 인수인계 시스템 API 서버(FastAPI).

    uvicorn app.main:app --reload --port 8000      # 개발 서버
    http://localhost:8000/docs                     # API 문서(Swagger)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from amigo_agent import SLOTS, STAGE_LABEL, STAGE_NUMBER
from amigo_rag import FORMAT_LABELS
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings, get_settings
from .routers import auth, chat, documents, profile, sessions, sources, unit_tasks
from .routers.deps import get_services, guard_session_owner
from .services.container import Services

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

VERSION = "0.1.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.services = Services(settings)
        yield
        app.state.services.shutdown()

    app = FastAPI(title="AMIGO 인수인계 API", version=VERSION, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for module in (auth, profile, unit_tasks):
        app.include_router(module.router)
    for module in (sessions, sources, chat, documents):  # 로그인 계정이 만든 세션은 그 계정만 접근
        app.include_router(module.router, dependencies=[Depends(guard_session_owner)])
    _install_error_handlers(app)

    @app.get("/api/health", tags=["meta"], summary="서버 상태와 AI 엔진 정보")
    def health(services: Services = Depends(get_services)):
        return {"status": "ok", "version": VERSION, **services.agent.describe()}

    @app.get("/api/config", tags=["meta"], summary="화면 구성용 설정(업로드 제한, 양식, 단계)")
    def config(services: Services = Depends(get_services)):
        return {
            "max_upload_mb": settings.max_upload_mb,
            "max_files_per_upload": settings.max_files_per_upload,
            "allowed_extensions": sorted(services.allowed_extensions),
            "format_labels": FORMAT_LABELS,
            "link_types": [
                {"key": "auto", "label": "자동 판별"},
                {"key": "confluence", "label": "컨플루언스"},
                {"key": "nanumi", "label": "나누미"},
                {"key": "web", "label": "일반 웹"},
            ],
            "stages": [
                {"key": key, "number": STAGE_NUMBER[key], "label": STAGE_LABEL[key]}
                for key in ("analyzing", "summary", "qna", "composing", "review")
            ],
            "slots": [
                {
                    "key": s.key,
                    "title": s.title,
                    "description": s.description,
                    "fields": [{"key": f.key, "label": f.label} for f in s.fields],
                    "required": list(s.required),
                }
                for s in SLOTS
            ],
            **services.agent.describe(),
        }

    _mount_frontend(app, settings)
    return app


FIELD_LABELS = {
    "login_id": "아이디",
    "password": "비밀번호",
    "name": "이름",
    "department": "부서",
    "position": "직위",
    "team": "팀",
    "tasks": "단위업무",
    "tasks.name": "단위업무명",
    "tasks.description": "업무 설명",
    "description": "업무 설명",
    "files": "파일",
}


def _install_error_handlers(app: FastAPI) -> None:
    """오류 응답을 {"success": false, "message": "...", "detail": ...} 로 통일한다(기존 화면이 읽는 detail 은 유지)."""

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException):
        message = exc.detail if isinstance(exc.detail, str) else "요청을 처리할 수 없습니다."
        body = {"success": False, "message": message, "detail": exc.detail}
        return JSONResponse(body, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        errors = jsonable_encoder(exc.errors())
        fields = []
        for err in errors:
            loc = [str(x) for x in err.get("loc", []) if x not in ("body", "query", "path") and not isinstance(x, int)]
            label = (FIELD_LABELS.get(".".join(loc)) or FIELD_LABELS.get(loc[-1], loc[-1])) if loc else "요청"
            if label not in fields:
                fields.append(label)
        message = f"입력값을 확인해 주세요: {', '.join(fields)}" if fields else "입력값을 확인해 주세요."
        return JSONResponse({"success": False, "message": message, "detail": errors}, status_code=422)


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    """빌드된 프론트엔드가 있으면 같은 서버에서 제공한다(SPA 라우팅 지원)."""
    if not settings.frontend_dist:
        return
    dist = Path(settings.frontend_dist).resolve()
    index = dist / "index.html"
    if not index.is_file():
        logging.getLogger(__name__).warning("AMIGO_FRONTEND_DIST 에 index.html 이 없습니다: %s", dist)
        return
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path == "api" or path.startswith("api/"):  # 없는 API 주소는 화면 대신 404
            raise HTTPException(status_code=404, detail="없는 API 경로입니다.")
        candidate = (dist / path).resolve()
        if path and dist in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()
