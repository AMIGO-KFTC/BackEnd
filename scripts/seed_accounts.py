r"""시연용 계정만 실제 DB(data/app.db 또는 AMIGO_DATABASE_URL)에 넣는다. 여러 번 실행해도 같은 상태가 된다.

    .venv/bin/python scripts/seed_accounts.py            # 계정·프로필·관리자 권한을 맞춘다
    (Windows: .venv\Scripts\python.exe scripts\seed_accounts.py)

인수인계서·자료·팀 업무 같은 데이터는 넣지 않는다. 서비스를 직접 써 보며 만든다.
팀 업무분장·사업계획·메일·지라·컨플루언스는 코드(app/resources, app/mock)에 들어 있어 계정만 있으면 보인다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.models import Base, User, UserProfile  # noqa: E402
from app.security import hash_password  # noqa: E402

PASSWORD = "amigo1234"  # 시연용 공통 비밀번호(시드는 DB 에 직접 넣어서 가입 규칙 검사를 거치지 않는다)

# (사번, 이름, 부서(본부), 팀, 직위, 역할: admin = 팀장(users.is_admin) / member). 로그인 아이디는 항상 사번이다.
ACCOUNTS = [
    ("1100404", "강민재", "디지털사업본부", "IT업무운영팀", "팀장", "admin"),
    ("1111111", "김민준", "디지털사업본부", "IT업무운영팀", "계장", "member"),
    ("1180521", "오세준", "디지털금융부", "디지털사업팀", "차장", "member"),
    ("1200703", "정서윤", "총무부", "법무팀", "과장", "member"),
    ("1230704", "문지혁", "결제사업부", "페이먼트사업팀", "대리", "member"),
    ("1240705", "서도하", "경영전략부", "기획조정팀", "계장", "member"),
]


def upsert_accounts(db: Session) -> None:
    for employee_no, name, department, team, position, role in ACCOUNTS:
        login_id, password = employee_no, PASSWORD
        user = db.scalar(select(User).where(User.login_id == login_id))
        if user is None:
            user = User(login_id=login_id, password_hash=hash_password(password), name=name, email=f"{login_id}@amigo.example")
            db.add(user)
            print(f"계정 생성  {login_id:10} {name}")
        else:
            user.password_hash = hash_password(password)
            user.name = name
            print(f"계정 갱신  {login_id:10} {name}")
        user.is_admin = role == "admin"
        user.employee_no = employee_no
        profile = user.profile or UserProfile(user_id=user.id)
        profile.department, profile.team, profile.position = department, team, position
        user.profile = profile
        db.flush()


def main() -> None:
    settings = get_settings()
    engine = create_engine(settings.db_url)
    Base.metadata.create_all(engine)
    from app.services.container import _add_missing_columns

    _add_missing_columns(engine)
    with Session(engine) as db:
        upsert_accounts(db)
        db.commit()
    print("완료")


if __name__ == "__main__":
    main()
