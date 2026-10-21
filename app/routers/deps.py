"""라우터 공용 의존성과 변환 함수."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AuthSession, HandoverSession, Message, User, utcnow
from ..schemas import MessageOut, ProfileOut, Progress, SessionDetail, SessionSummary, SourceOut, Usage, UsageTotal, UserOut
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


def as_utc(value: datetime) -> datetime:
    """SQLite 는 시간대 정보를 버리고 저장하므로 읽은 값을 UTC 로 맞춘다."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def request_token(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
    """Authorization: Bearer 헤더 또는 로그인 쿠키에서 토큰을 꺼낸다."""
    return credentials.credentials if credentials else request.cookies.get(COOKIE_NAME, "")


def optional_user(token: str = Depends(request_token), db: Session = Depends(get_db)) -> User | None:
    if not token:
        return None
    auth = db.get(AuthSession, token_hash(token))
    if auth is None or as_utc(auth.expires_at) <= utcnow():
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
    """/api/sessions/{session_id}/... 공통 검사: 로그인 계정이 만든 세션은 그 계정만 쓸 수 있다.

    인수자로 지정된 계정(successor_login_id)은 읽기(GET: 상태·문서·내려받기·근거)만 할 수 있다.
    """
    session_id = request.path_params.get("session_id")
    row = db.get(HandoverSession, session_id) if session_id else None
    if row is None or not row.user_id.startswith("user:"):
        return
    if user is None:
        raise HTTPException(status_code=401, detail=LOGIN_REQUIRED, headers={"WWW-Authenticate": "Bearer"})
    if user.owner_key == row.user_id:
        return
    if request.method == "GET" and (is_successor(user, row) or is_team_admin(db, user, row)):
        return
    raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")


def is_successor(user: User, row: HandoverSession) -> bool:
    return bool(row.successor_login_id) and row.successor_login_id.lower() == user.login_id.lower()


def is_team_admin(db: Session, user: User, row: HandoverSession) -> bool:
    """팀 관리자(role=admin)는 같은 소속(부서 + 팀) 구성원의 인수인계를 읽을 수 있다."""
    from ..services.guidelines import organization_key  # 순환 import 방지

    if not user.is_admin or user.profile is None:
        return False
    try:
        owner = db.get(User, int(row.user_id.split(":", 1)[1]))
    except ValueError:
        return False
    if owner is None or owner.profile is None:
        return False
    return organization_key(owner.profile.organization) == organization_key(user.profile.organization)


def admin_user(user: User = Depends(current_user)) -> User:
    """관리자(팀장) 전용 API. 일반 구성원은 403."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="관리자(팀장)만 쓸 수 있는 기능입니다.")
    return user


def profile_out(user: User) -> ProfileOut | None:
    if user.profile is None:
        return None
    p = user.profile
    return ProfileOut(department=p.department, position=p.position, team=p.team, name=user.name)


def user_out(user: User) -> UserOut:
    return UserOut(id=user.id, login_id=user.login_id, name=user.name, role=user.role or "member", profile=profile_out(user))


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
        mode=row.mode or "reassignment",
        task_name=row.task_name,
        successor=row.successor,
        successor_login_id=row.successor_login_id or "",
        handover_date=row.handover_date,
        stage=row.stage,
        status=row.status,
        document_version=row.document_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def owner_login_id(db: Session, row: HandoverSession) -> str:
    """세션을 만든 로그인 계정의 아이디(user:N 소유자만). 화면이 인계자 본인인지 가려 읽기 전용 여부를 정한다."""
    if not row.user_id.startswith("user:"):
        return ""
    try:
        return db.scalar(select(User.login_id).where(User.id == int(row.user_id.split(":", 1)[1]))) or ""
    except ValueError:
        return ""


def detail(db: Session, row: HandoverSession) -> SessionDetail:
    gaps = row.gaps
    last_id = db.scalar(select(func.max(Message.id)).where(Message.session_id == row.id)) or 0
    return SessionDetail(
        **summary(row).model_dump(),
        duties=row.duties,
        owner_login_id=owner_login_id(db, row),
        progress=Progress(message=row.progress_message, current=row.progress_current, total=row.progress_total),
        error=row.error,
        engine=row.engine,
        slots=row.slots,
        gaps=gaps,
        open_gap_count=sum(1 for g in gaps if g.get("status") == "open"),
        question_count=row.question_count,
        sources=[SourceOut.model_validate(s) for s in row.sources],
        last_message_id=last_id,
        usage=session_usage(row),
    )


def session_usage(row: HandoverSession) -> Usage:
    return Usage(
        requests=row.llm_requests or 0,
        input_tokens=row.input_tokens or 0,
        output_tokens=row.output_tokens or 0,
        cache_read_tokens=row.cache_read_tokens or 0,
        cache_write_tokens=row.cache_write_tokens or 0,
        cost_usd=round(row.cost_usd or 0.0, 4),
    )


def usage_total(db: Session, budget_usd: float) -> UsageTotal:
    s = HandoverSession
    totals = db.execute(
        select(
            func.count(s.id), func.coalesce(func.sum(s.llm_requests), 0), func.coalesce(func.sum(s.input_tokens), 0),
            func.coalesce(func.sum(s.output_tokens), 0), func.coalesce(func.sum(s.cache_read_tokens), 0),
            func.coalesce(func.sum(s.cache_write_tokens), 0), func.coalesce(func.sum(s.cost_usd), 0.0),
        )
    ).one()
    cost = round(float(totals[6]), 4)
    return UsageTotal(
        sessions=totals[0], requests=totals[1], input_tokens=totals[2], output_tokens=totals[3],
        cache_read_tokens=totals[4], cache_write_tokens=totals[5], cost_usd=cost,
        budget_usd=budget_usd, remaining_usd=round(max(budget_usd - cost, 0.0), 4) if budget_usd > 0 else None,
    )


def message_out(message: Message) -> MessageOut:
    return MessageOut.model_validate(message)
