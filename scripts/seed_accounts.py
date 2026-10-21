r"""시연용 계정과 인수인계 관계를 실제 DB(data/app.db 또는 AMIGO_DATABASE_URL)에 넣는다. 여러 번 실행해도 같은 상태가 된다.

    .venv\Scripts\python.exe scripts\seed_accounts.py            # 계정·프로필·역할을 맞추고 인수인계 2건을 만든다
    .venv\Scripts\python.exe scripts\seed_accounts.py --clean    # + 시드 계정의 빈 인수인계(자료·문서 없음)를 지운다

계정(아이디 / 비밀번호)
  IT아미고팀  팀장 김미고   amigoteamjang / amigoteamjang1234   (관리자)
              계장 김민준   demo / demo1234                     (시연용)
              계장 최환     hwan / hwan1234
  AI개발팀    팀장 최고미   aiteamjang / aiteamjang1234         (관리자)
              계장 임윤희   yunhui / yunhui1234
인수인계(인계자 → 인수자)
  계장 김민준 → 계장 임윤희   AMIGO 인수인계 서비스 운영 · 협력사 유지보수 계약 관리
  계장 임윤희 → 계장 최환     AI 모델 평가 파이프라인 관리 · 임베딩 서버 운영
  팀장 김미고 → 팀장 최고미   IT아미고팀 운영 총괄 · 연간 정보화 예산 관리
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.models import Base, HandoverSession, UnitTask, User, UserProfile  # noqa: E402
from app.security import hash_password  # noqa: E402

DIVISION = "디지털본부"  # 부서(본부)는 요청에 없어 임시로 둔다. 프로필 수정에서 바꿀 수 있다.

# (아이디, 비밀번호, 이름, 부서, 팀, 직위, 역할: admin = 관리자(users.is_admin) / member)
ACCOUNTS = [
    ("amigoteamjang", "amigoteamjang1234", "김미고", DIVISION, "IT아미고팀", "팀장", "admin"),
    ("demo", "demo1234", "김민준", DIVISION, "IT아미고팀", "계장", "member"),
    ("hwan", "hwan1234", "최환", DIVISION, "IT아미고팀", "계장", "member"),
    ("aiteamjang", "aiteamjang1234", "최고미", DIVISION, "AI개발팀", "팀장", "admin"),
    ("yunhui", "yunhui1234", "임윤희", DIVISION, "AI개발팀", "계장", "member"),
]

# (인계자 아이디, 단위업무명, 업무 설명, 인수자 아이디, 인계 예정일)
HANDOVERS = [
    # 계장 김민준 → 계장 임윤희
    ("demo", "AMIGO 인수인계 서비스 운영", "서비스 배포·장애 대응, 모니터링 알림 관리, 월간 운영 보고서 작성", "yunhui", "2026-11-14"),
    ("demo", "협력사 유지보수 계약 관리", "(주)한빛웹솔루션 유지보수 계약 검수·대금 지급, 분기 실적 보고", "yunhui", "2026-11-14"),
    # 계장 임윤희 → 계장 최환
    ("yunhui", "AI 모델 평가 파이프라인 관리", "평가 데이터셋 관리, 주간 품질 리포트, 모델 교체 전 회귀 검증", "hwan", "2026-11-21"),
    ("yunhui", "임베딩 서버 운영", "임베딩 API 서버 배포·용량 관리, 장애 대응 연락망 유지", "hwan", "2026-11-21"),
    # 팀장 김미고 → 팀장 최고미
    ("amigoteamjang", "IT아미고팀 운영 총괄", "팀 업무 분장·주간 회의 주재, 본부 보고, 외부 기관 협의 창구", "aiteamjang", "2026-12-01"),
    ("amigoteamjang", "연간 정보화 예산 관리", "정보화 예산 편성·집행 점검, 분기 결산 보고, 계약 심의 참석", "aiteamjang", "2026-12-01"),
]


def upsert_accounts(db: Session) -> dict[str, User]:
    users: dict[str, User] = {}
    for login_id, password, name, department, team, position, role in ACCOUNTS:
        user = db.scalar(select(User).where(User.login_id == login_id))
        if user is None:
            user = User(login_id=login_id, password_hash=hash_password(password), name=name)
            db.add(user)
            print(f"계정 생성  {login_id:14} {name}")
        else:
            user.password_hash = hash_password(password)
            user.name = name
            print(f"계정 갱신  {login_id:14} {name}")
        user.is_admin = role == "admin"  # 관리자 권한(users.is_admin): 팀장
        profile = user.profile or UserProfile(user_id=user.id)
        profile.department, profile.team, profile.position = department, team, position
        user.profile = profile
        db.flush()
        users[login_id] = user
    return users


def clean_empty_sessions(db: Session, users: dict[str, User]) -> None:
    """시드 계정의 빈 인수인계(자료도 문서도 없는 것)를 지운다. Swagger 로 만든 테스트 껍데기를 치우는 용도."""
    for user in users.values():
        rows = db.scalars(select(HandoverSession).where(HandoverSession.user_id == user.owner_key)).all()
        for row in rows:
            if row.sources or row.document_version > 0:
                continue
            task = db.scalar(select(UnitTask).where(UnitTask.session_id == row.id))
            if task is not None and any(task.name == h[1] for h in HANDOVERS if h[0] == user.login_id):
                continue  # 이 스크립트가 만든 단위업무는 둔다
            print(f"빈 인수인계 삭제  {user.name}: {row.title}")
            if task is not None:
                db.delete(task)
            db.delete(row)
    db.flush()


def upsert_handovers(db: Session, users: dict[str, User]) -> None:
    from app.routers.sessions import new_handover_session
    from app.routers.unit_tasks import _duties, _welcome

    for order, (owner_id, name, description, successor_id, date) in enumerate(HANDOVERS, start=1):
        owner, successor = users[owner_id], users[successor_id]
        profile = owner.profile
        task = db.scalar(select(UnitTask).where(UnitTask.user_id == owner.id, UnitTask.name == name))
        if task is None:
            row = new_handover_session(
                db, user_id=owner.owner_key, owner_name=owner.name, organization=profile.organization, position=profile.position,
                duties=_duties(name, description), title=f"{name} 인수인계", task_name=name,
                successor=f"{successor.name} {successor.profile.position}", successor_login_id=successor.login_id,
                handover_date=date, welcome=_welcome(name),
            )
            db.add(UnitTask(id=uuid.uuid4().hex, user_id=owner.id, session=row, name=name, description=description, sort_order=order))
            print(f"인수인계 생성  {owner.name} → {successor.name}: {name}")
        else:
            row = task.session
            row.successor, row.successor_login_id, row.handover_date = f"{successor.name} {successor.profile.position}", successor.login_id, date
            row.owner_name, row.organization, row.position = owner.name, profile.organization, profile.position
            task.description, row.duties = description, _duties(name, description)
            print(f"인수인계 갱신  {owner.name} → {successor.name}: {name}")


def main() -> None:
    settings = get_settings()
    engine = create_engine(settings.db_url)
    Base.metadata.create_all(engine)
    from app.services.container import _add_missing_columns

    _add_missing_columns(engine)
    with Session(engine) as db:
        users = upsert_accounts(db)
        if "--clean" in sys.argv:
            clean_empty_sessions(db, users)
        upsert_handovers(db, users)
        db.commit()
    print("완료")


if __name__ == "__main__":
    main()
