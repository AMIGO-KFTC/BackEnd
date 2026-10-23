"""로그인: 회원가입, 로그인(세션 생성), 로그아웃, 내 정보, 비밀번호 찾기(재설정).

비밀번호 찾기는 아이디와 등록한 이메일이 맞으면 재설정 토큰을 만들어 안내 메일을 보낸다(지금은 가상 발송).
아이디나 이메일이 틀려도 같은 응답을 돌려줘 가입 여부를 알 수 없게 한다(개발용 토큰 노출 설정은 예외).
"""

from __future__ import annotations

import re

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import AuthSession, PasswordReset, User, utcnow
from ..schemas import LoginIn, LoginOut, MeOut, PasswordForgotIn, PasswordForgotOut, PasswordResetIn, Result, SignupIn
from ..security import DUMMY_HASH, hash_password, new_token, token_hash, verify_password
from ..services.accounts import issue_reset_token, set_password
from ..services.container import Services
from .deps import COOKIE_NAME, as_utc, current_user, get_db, get_services, request_token, user_out
from .me import email_taken, employee_no_taken

router = APIRouter(prefix="/api/auth", tags=["auth"])

LOGIN_FAILED = "사번 또는 비밀번호가 올바르지 않습니다."


def find_account(db: Session, value: str) -> User | None:
    """로그인 칸에 적은 값으로 계정을 찾는다. 아이디(login_id)가 맞으면 그 계정, 아니면 사번(employee_no)이 같은 계정.
    사번이 겹쳐 하나로 정해지지 않으면 찾지 않는다(남의 계정으로 들어가는 일이 없게)."""
    key = value.strip().lower()
    if not key:
        return None
    user = db.scalar(select(User).where(User.login_id == key))
    if user is not None:
        return user
    matches = list(db.scalars(select(User).where(func.lower(User.employee_no) == key).limit(2)))
    return matches[0] if len(matches) == 1 else None


@router.post("/signup", response_model=MeOut, status_code=status.HTTP_201_CREATED, summary="회원가입(사번·비밀번호·이름)")
def signup(body: SignupIn, db: Session = Depends(get_db), services: Services = Depends(get_services)):
    if not services.settings.allow_signup:
        raise HTTPException(status_code=403, detail="회원가입이 비활성화되어 있습니다. 관리자에게 계정을 요청해 주세요.")
    login_id = body.login_id.lower()
    if db.scalar(select(User.id).where(User.login_id == login_id)) is not None:
        raise HTTPException(status_code=409, detail="이미 가입된 사번입니다.")
    email = body.email.lower()
    if email_taken(db, email):
        raise HTTPException(status_code=409, detail="다른 계정이 쓰고 있는 이메일입니다.")
    employee_no = login_id if re.fullmatch(r"\d{7}", login_id) else body.employee_no  # 사번으로 가입하면 사번도 같은 값
    if employee_no_taken(db, employee_no):
        raise HTTPException(status_code=409, detail="이미 다른 계정이 쓰고 있는 사번입니다.")
    user = User(login_id=login_id, password_hash=hash_password(body.password), name=body.name, email=email, employee_no=employee_no)
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:  # 같은 아이디로 동시에 가입한 경우
        db.rollback()
        raise HTTPException(status_code=409, detail="이미 가입된 사번입니다.") from exc
    return MeOut(message="회원가입이 완료되었습니다. 로그인해 주세요.", user=user_out(user))


@router.post("/login", response_model=LoginOut, summary="로그인: 성공하면 이름과 세션 토큰(쿠키)을 돌려준다")
def login(body: LoginIn, response: Response, db: Session = Depends(get_db), services: Services = Depends(get_services)):
    user = find_account(db, body.login_id)
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


FORGOT_SENT = "입력한 사번과 이메일이 맞으면 비밀번호 재설정 안내를 보냈습니다. 메일의 안내에 따라 {minutes}분 안에 새 비밀번호를 정해 주세요."


@router.post("/password/forgot", response_model=PasswordForgotOut, summary="비밀번호 찾기: 사번 + 등록한 이메일 → 재설정 안내")
def forgot_password(body: PasswordForgotIn, db: Session = Depends(get_db), services: Services = Depends(get_services)):
    settings = services.settings
    user = find_account(db, body.login_id)
    token = None
    if user is not None and user.email and user.email.lower() == body.email.lower():
        token, _ = issue_reset_token(db, user, settings.password_reset_ttl_minutes)
        db.commit()
        services.reset_notifier.send(user, token)
    minutes = settings.password_reset_ttl_minutes
    return PasswordForgotOut(
        message=FORGOT_SENT.format(minutes=minutes),
        expires_in_minutes=minutes,
        reset_token=token if settings.expose_reset_token else None,
    )


@router.post("/password/reset", response_model=Result, summary="비밀번호 재설정: 안내 메일의 토큰 + 새 비밀번호")
def reset_password(body: PasswordResetIn, db: Session = Depends(get_db)):
    """재설정하면 모든 기기의 로그인이 끊기고, 토큰은 다시 쓸 수 없다."""
    reset = db.get(PasswordReset, token_hash(body.token))
    if reset is None or reset.used_at is not None or as_utc(reset.expires_at) <= utcnow():
        raise HTTPException(status_code=400, detail="재설정 링크가 만료되었거나 이미 사용되었습니다. 비밀번호 찾기를 다시 해 주세요.")
    user = reset.user
    reset.used_at = utcnow()
    set_password(db, user, body.new_password)
    db.commit()
    return Result(message="비밀번호를 새로 정했습니다. 새 비밀번호로 로그인해 주세요.")
