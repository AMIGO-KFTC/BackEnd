"""scripts/seed_accounts.py: 시연 계정 만들기와 예전 계정 이름 바꾸기."""

import importlib.util
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Base, HandoverSession, HandoverTransfer, User, UserProfile

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "seed_accounts.py"


def load_seed():
    spec = importlib.util.spec_from_file_location("seed_accounts_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'seed.db'}")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_it_amigo_team_members_are_seeded_under_the_team_leaders_department(tmp_path):
    seed = load_seed()
    with make_db(tmp_path) as db:
        seed.upsert_accounts(db)
        seed.upsert_accounts(db)  # 여러 번 실행해도 같은 상태
        db.commit()
        team = db.scalars(select(User).join(UserProfile).where(UserProfile.team == "IT아미고팀", UserProfile.department == "IT서비스부").order_by(User.employee_no)).all()
        names = {user.name: user for user in team}
        assert set(names) == {"최고미", "한서진", "윤지호", "배수아", "노은우"}
        assert [u.name for u in team if u.is_admin] == ["최고미"]
        assert names["한서진"].login_id == "1210815" and names["한서진"].profile.position == "과장"
    employee_numbers = [account[0] for account in seed.ACCOUNTS]
    assert len(employee_numbers) == len(set(employee_numbers))  # 사번이 겹치지 않는다


def test_digital_business_team_has_its_leader_and_choe_geumgyeol(tmp_path):
    seed = load_seed()
    with make_db(tmp_path) as db:
        seed.upsert_accounts(db)
        db.commit()
        team = db.scalars(select(User).join(UserProfile).where(UserProfile.team == "디지털사업팀", UserProfile.department == "디지털금융부").order_by(User.employee_no)).all()
        assert {u.name for u in team} == {"장하준", "최금결", "오세준"}
        assert [(u.name, u.login_id) for u in team if u.is_admin] == [("장하준", "1100812")]
        assert next(u for u in team if u.name == "최금결").login_id == "1111111"  # 최금결은 사번 그대로 팀만 옮겨진다


def test_rename_updates_the_account_and_the_names_copied_into_handovers(tmp_path):
    seed = load_seed()
    with make_db(tmp_path) as db:
        hwan = User(login_id="hwan", password_hash="x", name="최환", email="hwan@example.com")
        yunhui = User(login_id="yunhui", password_hash="x", name="임윤희", email="yunhui@example.com")
        db.add_all([hwan, yunhui])
        db.flush()
        session = HandoverSession(id="s1", user_id=f"user:{hwan.id}", title="t", owner_name="최환", successor="임윤희 계장")
        transfer = HandoverTransfer(id="t1", sender_id=hwan.id, recipient_id=yunhui.id, title="t", sender_name="최환", markdown="# 최환이 쓴 문서")
        db.add_all([session, transfer])
        db.commit()

        seed.rename_accounts(db)
        db.commit()
        db.refresh(session)
        db.refresh(transfer)
        assert (hwan.name, yunhui.name) == ("최기아", "임삼성")
        assert session.owner_name == "최기아" and session.successor == "임삼성 계장"
        assert transfer.sender_name == "최기아"
        assert transfer.markdown == "# 최환이 쓴 문서"  # 이미 만들어진 문서 본문은 고치지 않는다

        seed.rename_accounts(db)  # 다시 실행해도 그대로
        assert hwan.name == "최기아"


def test_rename_skips_accounts_that_do_not_exist(tmp_path):
    seed = load_seed()
    with make_db(tmp_path) as db:
        seed.rename_accounts(db)  # 계정이 하나도 없어도 오류 없이 지나간다
        assert db.scalars(select(User)).all() == []


def test_seed_account_with_an_old_name_is_renamed_along_with_the_copied_names(tmp_path):
    """이미 있는 시드 계정(1111111)의 이름이 ACCOUNTS 와 다르면 바꾸고, 인계자 이름·인수자 글도 같이 맞춘다. 예전 demo 계정도 같은 이름이 된다."""
    seed = load_seed()
    with make_db(tmp_path) as db:
        minjun = User(login_id="1111111", password_hash="x", name="김민준", email="1111111@amigo.example")
        demo = User(login_id="demo", password_hash="x", name="김민준", email="demo@example.com")
        other = User(login_id="other", password_hash="x", name="다른사람", email="other@example.com")
        db.add_all([minjun, demo, other])
        db.flush()
        mine = HandoverSession(id="s1", user_id=f"user:{minjun.id}", title="t", owner_name="김민준", successor="")
        theirs = HandoverSession(id="s2", user_id=f"user:{other.id}", title="t", owner_name="다른사람", successor="김민준 계장")
        db.add_all([mine, theirs])
        db.commit()

        seed.upsert_accounts(db)
        seed.rename_accounts(db)
        db.commit()
        db.refresh(mine)
        db.refresh(theirs)
        assert (minjun.name, demo.name) == ("최금결", "최금결")
        assert mine.owner_name == "최금결" and theirs.successor == "최금결 계장"
        assert other.name == "다른사람"
