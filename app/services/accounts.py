"""계정 관리: 비밀번호 바꾸기 · 비밀번호 찾기(재설정 토큰).

- 재설정 토큰은 추측할 수 없는 난수이고 DB 에는 SHA-256 만 저장한다. 유효 시간(AMIGO_PASSWORD_RESET_TTL_MINUTES)이 지나거나
  한 번 쓰면 다시 쓸 수 없다. 새 토큰을 만들면 같은 사용자의 쓰지 않은 토큰은 지운다.
- 안내 메일은 아직 실제로 보내지 않는다(사내 메일 연동 전). ResetNotifier 가 서버 로그에 '가상 발송' 기록만 남기며,
  개발용 설정(AMIGO_EXPOSE_RESET_TOKEN, 가상 응답 모드)에서는 API 응답에 토큰을 넣어 화면에서 바로 재설정할 수 있다.
- 비밀번호를 바꾸거나 재설정하면 다른 기기의 로그인 세션을 끊는다.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..models import AuthSession, PasswordReset, User, utcnow
from ..security import hash_password, new_token, token_hash

logger = logging.getLogger(__name__)


def issue_reset_token(db: Session, user: User, ttl_minutes: int, requested_by: str = "self") -> tuple[str, PasswordReset]:
    """재설정 토큰을 만든다(커밋은 호출한 쪽에서). 토큰 원문은 이때만 알 수 있다."""
    db.execute(delete(PasswordReset).where(PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None)))
    token = new_token()
    now = utcnow()
    reset = PasswordReset(token_hash=token_hash(token), user_id=user.id, created_at=now, expires_at=now + timedelta(minutes=ttl_minutes), requested_by=requested_by)
    db.add(reset)
    return token, reset


def set_password(db: Session, user: User, password: str, *, keep_token_hash: str | None = None) -> None:
    """비밀번호를 바꾸고 다른 로그인 세션을 끊는다(keep_token_hash 의 세션만 남김). 남은 재설정 토큰도 지운다."""
    user.password_hash = hash_password(password)
    user.password_changed_at = utcnow()
    query = delete(AuthSession).where(AuthSession.user_id == user.id)
    if keep_token_hash:
        query = query.where(AuthSession.token_hash != keep_token_hash)
    db.execute(query)
    db.execute(delete(PasswordReset).where(PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None)))


class ResetNotifier:
    """비밀번호 재설정 안내 '가상 발송'. 사내 메일을 붙이면 send() 만 바꾼다."""

    def __init__(self, url_template: str, log_token: bool) -> None:
        self.url_template = url_template
        self.log_token = log_token

    def url(self, token: str) -> str:
        return self.url_template.replace("{token}", token)

    def send(self, user: User, token: str) -> None:
        link = self.url(token) if self.log_token else "(운영 설정이라 링크를 로그에 남기지 않음)"
        logger.warning("비밀번호 재설정 안내(가상 발송, 실제 메일 연동 전): %s <%s> %s", user.login_id, user.email, link)
