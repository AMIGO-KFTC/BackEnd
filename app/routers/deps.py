"""라우터 공용 의존성과 변환 함수."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AuthSession, HandoverSession, Message, User, utcnow
from ..schemas import MessageOut, ProfileOut, Progress, SessionDetail, SessionSummary, SourceOut, UserOut
from ..security import token_hash
from ..services.container import Services

COOKIE_NAME = "amigo_session"
LOGIN_REQUIRED = "로그인이 필요합니다."
bearer = HTTPBearer(auto_error=False, description="로그인 응답의 token (쿠키 amigo_session 으로도 인증됩니다)")


def get_services(request: Request) -> Services:
    return request.app.state.services


def get_db(services: Services = Depends(get_services)):
    with services.db() as db:
        yield db


def _aware(value: datetime) -> datetime:
    """SQLite 는 시간대 정보를 버리고 저장하므로 읽은 값을 UTC 로 맞춘다."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def request_token(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
    """Authorization: Bearer 헤더 또는 로그인 쿠키에서 토큰을 꺼낸다."""
    return credentials.credentials if credentials else request.cookies.get(COOKIE_NAME, "")


def optional_user(token: str = Depends(request_token), db: Session = Depends(get_db)) -> User | None:
    if not token:
        return None
    auth = db.get(AuthSession, token_hash(token))
    if auth is None or _aware(auth.expires_at) <= utcnow():
        return None
    return auth.user


def current_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail=LOGIN_REQUIRED, headers={"WWW-Authenticate": "Bearer"})
    return user


def get_user_id(x_user_id: str | None = Header(default=None), user: User | None = Depends(optional_user)) -> str:
    """세션 소유자 구분. 로그인했으면 계정 기준, 아니면 프로토타입용 X-User-Id(브라우저가 만든 임의 ID)."""
    if user is not None:
        return user.owner_key
    value = (x_user_id or "").strip()[:64]
    return "" if value.startswith("user:") else value  # 헤더로 로그인 사용자 행세를 못 하게


def guard_session_owner(request: Request, db: Session = Depends(get_db), user: User | None = Depends(optional_user)) -> None:
    """/api/sessions/{session_id}/... 공통 검사: 로그인 계정이 만든 세션은 그 계정만 쓸 수 있다."""
    session_id = request.path_params.get("session_id")
    row = db.get(HandoverSession, session_id) if session_id else None
    if row is None or not row.user_id.startswith("user:"):
        return
    if user is None:
        raise HTTPException(status_code=401, detail=LOGIN_REQUIRED, headers={"WWW-Authenticate": "Bearer"})
    if user.owner_key != row.user_id:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")


def profile_out(user: User) -> ProfileOut | None:
    if user.profile is None:
        return None
    p = user.profile
    return ProfileOut(department=p.department, position=p.position, team=p.team, name=user.name)


def user_out(user: User) -> UserOut:
    return UserOut(id=user.id, login_id=user.login_id, name=user.name, profile=profile_out(user))


def load_session(db: Session, session_id: str) -> HandoverSession:
    row = db.get(HandoverSession, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    return row


def summary(row: HandoverSession) -> SessionSummary:
    return SessionSummary(
        id=row.id,
        title=row.title,
        owner_name=row.owner_name,
        organization=row.organization,
        position=row.position,
        stage=row.stage,
        status=row.status,
        document_version=row.document_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def detail(db: Session, row: HandoverSession) -> SessionDetail:
    gaps = row.gaps
    last_id = db.scalar(select(func.max(Message.id)).where(Message.session_id == row.id)) or 0
    return SessionDetail(
        **summary(row).model_dump(),
        duties=row.duties,
        successor=row.successor,
        handover_date=row.handover_date,
        progress=Progress(message=row.progress_message, current=row.progress_current, total=row.progress_total),
        error=row.error,
        engine=row.engine,
        slots=row.slots,
        gaps=gaps,
        open_gap_count=sum(1 for g in gaps if g.get("status") == "open"),
        question_count=row.question_count,
        sources=[SourceOut.model_validate(s) for s in row.sources],
        last_message_id=last_id,
    )


def message_out(message: Message) -> MessageOut:
    return MessageOut.model_validate(message)
