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

from sqlalchemy import create_engine, select, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.models import Base, DocumentVersion, HandoverSession, HandoverTransfer, User, UserProfile  # noqa: E402
from app.security import hash_password  # noqa: E402

PASSWORD = "amigo1234"  # 시연용 공통 비밀번호(시드는 DB 에 직접 넣어서 가입 규칙 검사를 거치지 않는다)

# (사번, 이름, 부서(본부), 팀, 직위, 역할: admin = 팀장(users.is_admin) / member). 로그인 아이디는 항상 사번이다.
ACCOUNTS = [
    ("1100404", "최고미", "IT서비스부", "IT아미고팀", "팀장", "admin"),
    ("1111111", "최금결", "디지털금융부", "디지털사업팀", "계장", "member"),
    ("1100812", "장하준", "디지털금융부", "디지털사업팀", "팀장", "admin"),
    ("1180521", "오세준", "디지털금융부", "디지털사업팀", "차장", "member"),
    ("1200703", "정서윤", "총무부", "법무팀", "과장", "member"),
    ("1230704", "문지혁", "결제사업부", "페이먼트사업팀", "대리", "member"),
    ("1240705", "서도하", "경영전략부", "기획조정팀", "계장", "member"),
    # 팀장 화면의 '우리 팀'은 부서 + 팀이 같은 계정이라, 팀 구성원은 팀장과 같은 부서로 맞춘다. 아래는 IT아미고팀 구성원
    ("1210815", "한서진", "IT서비스부", "IT아미고팀", "과장", "member"),
    ("1220318", "윤지호", "IT서비스부", "IT아미고팀", "대리", "member"),
    ("1250602", "배수아", "IT서비스부", "IT아미고팀", "계장", "member"),
    ("1261104", "노은우", "IT서비스부", "IT아미고팀", "사원", "member"),
]

# 예전부터 있던 계정의 이름 바꾸기(로그인 아이디 → 새 이름). 계정이 없으면 건너뛴다.
# 이름을 복사해 저장해 둔 곳(인계자 이름·인수자·전송한 사람)도 같이 바꾼다. 이미 만들어진 인수인계서 본문·대화 기록은 고치지 않는다.
FORMER_NAMES = {"hwan": "최환", "yunhui": "임윤희", "demo": "김민준", "1111111": "김민준"}  # 로그인 아이디 → 그 계정이 예전에 쓰던 이름(이미 이름만 바뀐 DB 도 인수인계서 제목·문서를 맞춘다)
RENAMES = {
    "hwan": "최기아",
    "yunhui": "임삼성",
    "demo": "최금결",  # 예전 데모 계정(사번 1111111, 시드 계정과 같은 사람)
}


def replace_name_in_documents(db: Session, user: User, old_name: str, new_name: str) -> None:
    """이 사람 인수인계서의 제목('김민준 · …인수인계')과 문서 안의 인계자 이름을 새 이름으로 바꾼다(여러 번 실행해도 같다)."""
    for row in db.scalars(select(HandoverSession).where(HandoverSession.user_id == f"user:{user.id}")):
        row.title = row.title.replace(old_name, new_name)
        for column in ("document_md", "ai_document_md"):
            if getattr(row, column, None):
                setattr(row, column, getattr(row, column).replace(old_name, new_name))
        for version in db.scalars(select(DocumentVersion).where(DocumentVersion.session_id == row.id)):
            version.markdown = version.markdown.replace(old_name, new_name)


def set_name(db: Session, user: User, new_name: str) -> None:
    """계정 이름을 바꾸고, 이름을 복사해 저장해 둔 곳(인계자 이름·인수자 글·전송한 사람)도 같이 바꾼다."""
    old_name = user.name
    if old_name == new_name:
        return
    user.name = new_name
    db.execute(update(HandoverSession).where(HandoverSession.user_id == f"user:{user.id}").values(owner_name=new_name))
    db.execute(update(HandoverTransfer).where(HandoverTransfer.sender_id == user.id).values(sender_name=new_name))
    replace_name_in_documents(db, user, old_name, new_name)
    for row in db.scalars(select(HandoverSession).where(HandoverSession.successor.contains(old_name))):  # '임윤희 계장' 같은 인수자 글
        row.successor = row.successor.replace(old_name, new_name)
    print(f"이름 변경  {user.login_id:10} {old_name} → {new_name}")


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
            set_name(db, user, name)
            print(f"계정 갱신  {login_id:10} {name}")
        user.is_admin = role == "admin"
        user.employee_no = employee_no
        profile = user.profile or UserProfile(user_id=user.id)
        profile.department, profile.team, profile.position = department, team, position
        user.profile = profile
        db.flush()


def rename_accounts(db: Session) -> None:
    for login_id, new_name in RENAMES.items():
        user = db.scalar(select(User).where(User.login_id == login_id))
        if user is not None:
            set_name(db, user, new_name)
    for login_id, old_name in FORMER_NAMES.items():
        user = db.scalar(select(User).where(User.login_id == login_id))
        if user is not None and user.name != old_name:
            replace_name_in_documents(db, user, old_name, user.name)


def main() -> None:
    settings = get_settings()
    engine = create_engine(settings.db_url)
    Base.metadata.create_all(engine)
    from app.services.container import _add_missing_columns

    _add_missing_columns(engine)
    with Session(engine) as db:
        upsert_accounts(db)
        rename_accounts(db)
        db.commit()
    print("완료")


if __name__ == "__main__":
    main()
