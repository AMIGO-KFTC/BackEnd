"""로그인: 회원가입, 로그인(세션 생성), 로그아웃, 내 정보."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import AuthSession, User, utcnow
from ..schemas import LoginIn, LoginOut, MeOut, Result, SignupIn
from ..security import DUMMY_HASH, hash_password, new_token, token_hash, verify_password
from ..services.container import Services
from .deps import COOKIE_NAME, current_user, get_db, get_services, request_token, user_out

router = APIRouter(prefix="/api/auth", tags=["auth"])

LOGIN_FAILED = "아이디 또는 비밀번호가 올바르지 않습니다."


@router.post("/signup", response_model=MeOut, status_code=status.HTTP_201_CREATED, summary="회원가입(아이디·비밀번호·이름)")
def signup(body: SignupIn, db: Session = Depends(get_db), services: Services = Depends(get_services)):
    if not services.settings.allow_signup:
        raise HTTPException(status_code=403, detail="회원가입이 비활성화되어 있습니다. 관리자에게 계정을 요청해 주세요.")
    login_id = body.login_id.lower()
    if db.scalar(select(User.id).where(User.login_id == login_id)) is not None:
        raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다.")
    user = User(login_id=login_id, password_hash=hash_password(body.password), name=body.name)
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:  # 같은 아이디로 동시에 가입한 경우
        db.rollback()
        raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다.") from exc
    return MeOut(message="회원가입이 완료되었습니다. 로그인해 주세요.", user=user_out(user))


@router.post("/login", response_model=LoginOut, summary="로그인: 성공하면 이름과 세션 토큰(쿠키)을 돌려준다")
def login(body: LoginIn, response: Response, db: Session = Depends(get_db), services: Services = Depends(get_services)):
    user = db.scalar(select(User).where(User.login_id == body.login_id.lower()))
    # 아이디가 없어도 해시 비교를 해서 응답 시간으로 아이디 존재 여부를 알 수 없게 한다
    password_ok = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
    if user is None or not password_ok:
        raise HTTPException(status_code=401, detail=LOGIN_FAILED)

    now = utcnow()
    ttl = timedelta(hours=services.settings.session_ttl_hours)
    token = new_token()
    db.execute(delete(AuthSession).where(AuthSession.expires_at < now))  # 만료된 세션 정리
    db.add(AuthSession(token_hash=token_hash(token), user_id=user.id, created_at=now, expires_at=now + ttl))
    user.last_login_at = now
    db.commit()
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=services.settings.cookie_secure,
        path="/",
    )
    return LoginOut(message=f"{user.name}님, 환영합니다.", name=user.name, user=user_out(user), token=token, expires_at=now + ttl)


@router.post("/logout", response_model=Result, summary="로그아웃(세션 삭제)")
def logout(response: Response, token: str = Depends(request_token), db: Session = Depends(get_db)):
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(token)))
        db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return Result(message="로그아웃되었습니다.")


@router.get("/me", response_model=MeOut, summary="로그인한 사용자 정보와 프로필")
def me(user: User = Depends(current_user)):
    return MeOut(user=user_out(user))
