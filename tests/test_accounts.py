"""마이페이지(내 정보 수정) · 비밀번호 바꾸기/찾기 · 관리자 권한."""

from conftest import PROFILE
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import Settings
from app.main import create_app
from app.models import PasswordReset

ACCOUNT = {"login_id": "minsu.kim", "password": "correct-horse-1", "name": "김민수", "email": "Minsu.Kim@amigo.example"}


def login(client, login_id="minsu.kim", password="correct-horse-1"):
    return client.post("/api/auth/login", json={"login_id": login_id, "password": password})


def signup(client, account=ACCOUNT):
    res = client.post("/api/auth/signup", json=account)
    assert res.status_code == 201, res.text
    return res.json()["user"]


def test_mypage_view_and_edit(client):
    assert client.get("/api/me").status_code == 401
    user = signup(client)
    assert user["email"] == "minsu.kim@amigo.example" and user["is_admin"] is False
    assert client.post("/api/auth/signup", json={**ACCOUNT, "login_id": "other"}).status_code == 409  # 같은 이메일
    assert login(client).json()["user"]["is_admin"] is False

    me = client.get("/api/me").json()
    assert me["user"]["profile"] is None and me["stats"] == {"unit_tasks": 0, "documents": 0, "document_versions": 0, "sent_transfers": 0, "received_transfers": 0, "received_unread": 0}

    # 처음에는 부서·직위를 함께 보내야 한다
    first = client.patch("/api/me", json={"team": "웹서비스팀"})
    assert first.status_code == 400 and "부서와 직위" in first.json()["message"]
    res = client.patch("/api/me", json={"department": "디지털전략부", "team": "웹서비스팀", "position": "과장"})
    assert res.status_code == 200 and res.json()["message"] == "내 정보를 수정했습니다."
    task = client.post("/api/unit-tasks", json={"tasks": [{"name": "홈페이지 운영"}]}).json()["tasks"][0]

    # 보낸 항목만 바뀐다 · 단위업무 인수인계서의 인계자 정보에도 반영된다
    res = client.patch("/api/me", json={"name": "김민수2", "position": "차장", "email": "ms@amigo.example"}).json()
    assert res["user"]["name"] == "김민수2" and res["user"]["email"] == "ms@amigo.example"
    assert res["user"]["profile"] == {"department": "디지털전략부", "position": "차장", "team": "웹서비스팀", "name": "김민수2", "employee_no": ""}
    assert res["stats"]["unit_tasks"] == 1
    session = client.get(f"/api/sessions/{task['session_id']}").json()
    assert session["owner_name"] == "김민수2" and session["position"] == "차장"
    assert client.get("/api/auth/me").json()["user"]["profile"]["position"] == "차장"

    assert client.patch("/api/me", json={"team": ""}).json()["user"]["profile"]["team"] == ""
    assert client.patch("/api/me", json={}).status_code == 400
    assert client.patch("/api/me", json={"name": None}).status_code == 400
    assert client.patch("/api/me", json={"position": ""}).status_code == 422
    bad = client.patch("/api/me", json={"email": "not-an-email"})
    assert bad.status_code == 422 and bad.json()["message"] == "입력값을 확인해 주세요: 이메일"
    assert client.patch("/api/me", json={"is_admin": True}).status_code == 400  # 관리자 권한은 바꿀 수 없음(무시 → 바꿀 항목 없음)
    assert client.get("/api/me").json()["user"]["is_admin"] is False


def test_change_password_logs_out_other_devices(client, settings):
    signup(client)
    token = login(client).json()["token"]
    with TestClient(client.app) as other:  # 다른 기기
        login(other)
        assert other.get("/api/me").status_code == 200

        wrong = client.put("/api/me/password", json={"current_password": "nope", "new_password": "new-password-1"})
        assert wrong.status_code == 400 and wrong.json()["message"] == "지금 비밀번호가 올바르지 않습니다."
        same = client.put("/api/me/password", json={"current_password": "correct-horse-1", "new_password": "correct-horse-1"})
        assert same.status_code == 400
        short = client.put("/api/me/password", json={"current_password": "correct-horse-1", "new_password": "short"})
        assert short.status_code == 422 and short.json()["message"] == "입력값을 확인해 주세요: 새 비밀번호"

        ok = client.put("/api/me/password", json={"current_password": "correct-horse-1", "new_password": "new-password-1"})
        assert ok.status_code == 200
        assert client.get("/api/me").status_code == 200  # 지금 기기는 그대로
        assert client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).json()["password_changed_at"]
        assert other.get("/api/me").status_code == 401  # 다른 기기는 끊김
    assert login(client).status_code == 401
    assert login(client, password="new-password-1").status_code == 200


def test_forgot_and_reset_password(client, settings):
    signup(client)
    services = client.app.state.services
    services.settings.expose_reset_token = True  # 개발 모드: 메일 대신 응답으로 토큰을 받는다

    # 아이디·이메일이 틀려도 같은 안내(가입 여부를 알 수 없게)
    unknown = client.post("/api/auth/password/forgot", json={"login_id": "nobody", "email": "x@amigo.example"}).json()
    mismatch = client.post("/api/auth/password/forgot", json={"login_id": "minsu.kim", "email": "x@amigo.example"}).json()
    assert unknown["message"] == mismatch["message"] and unknown["reset_token"] is None and mismatch["reset_token"] is None

    first = client.post("/api/auth/password/forgot", json={"login_id": "Minsu.Kim", "email": "MINSU.KIM@amigo.example"}).json()
    res = client.post("/api/auth/password/forgot", json={"login_id": "minsu.kim", "email": "minsu.kim@amigo.example"}).json()
    assert res["reset_token"] and res["expires_in_minutes"] == 30 and res["message"] == unknown["message"]
    with services.db() as db:  # 토큰 원문은 저장하지 않고, 새로 받으면 이전 토큰은 못 쓴다
        assert db.query(PasswordReset).count() == 1 and db.get(PasswordReset, res["reset_token"]) is None
    assert client.post("/api/auth/password/reset", json={"token": first["reset_token"], "new_password": "brand-new-pw"}).status_code == 400

    login(client)
    reset = client.post("/api/auth/password/reset", json={"token": res["reset_token"], "new_password": "brand-new-pw"})
    assert reset.status_code == 200 and reset.json()["message"] == "비밀번호를 새로 정했습니다. 새 비밀번호로 로그인해 주세요."
    assert client.get("/api/me").status_code == 401  # 모든 기기 로그아웃
    again = client.post("/api/auth/password/reset", json={"token": res["reset_token"], "new_password": "another-pw-1"})
    assert again.status_code == 400 and "만료" in again.json()["message"]
    assert login(client, password="brand-new-pw").status_code == 200

    # 운영 설정에서는 응답에 토큰이 없다
    services.settings.expose_reset_token = False
    hidden = client.post("/api/auth/password/forgot", json={"login_id": "minsu.kim", "email": "minsu.kim@amigo.example"}).json()
    assert hidden["reset_token"] is None


def make_admin(client, login_id: str) -> None:
    """처음 관리자는 DB 에서 직접 지정한다(관리자 아이디 설정 기능 없음)."""
    from app.models import User

    with client.app.state.services.db() as db:
        db.query(User).filter(User.login_id == login_id).update({"is_admin": True})
        db.commit()


def test_admin_role_and_admin_api(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", workers=2)
    with TestClient(create_app(settings)) as c:
        signup(c, {"login_id": "boss", "password": "boss-password-1", "name": "최관리"})
        signup(c, {**ACCOUNT})
        # 가입만 해서는 관리자가 아니다
        assert login(c, "boss", "boss-password-1").json()["user"]["is_admin"] is False
        assert c.get("/api/admin/users").status_code == 403
        make_admin(c, "boss")
        res = login(c, "boss", "boss-password-1").json()
        assert res["user"]["is_admin"] is True
        users = c.get("/api/admin/users").json()
        assert users["total"] == 2 and [(u["login_id"], u["is_admin"]) for u in users["users"]] == [("boss", True), ("minsu.kim", False)]
        assert c.get("/api/admin/users?q=민수").json()["total"] == 1
        minsu = next(u for u in users["users"] if u["login_id"] == "minsu.kim")

        granted = c.patch(f"/api/admin/users/{minsu['id']}/role", json={"is_admin": True}).json()
        assert granted["user"]["is_admin"] is True and "관리자 권한을 주었습니다" in granted["message"]
        assert c.get("/api/admin/users?admin_only=true").json()["total"] == 2
        boss_id = res["user"]["id"]
        assert c.patch(f"/api/admin/users/{boss_id}/role", json={"is_admin": False}).status_code == 400
        assert c.patch("/api/admin/users/999/role", json={"is_admin": True}).status_code == 404

        # 메일을 받을 수 없는 회원은 관리자가 재설정 토큰을 발급해 전달한다
        issued = c.post(f"/api/admin/users/{minsu['id']}/password-reset").json()
        assert issued["reset_token"] in issued["reset_url"]
        assert c.post("/api/auth/password/reset", json={"token": issued["reset_token"], "new_password": "from-admin-1"}).status_code == 200

        c.patch(f"/api/admin/users/{minsu['id']}/role", json={"is_admin": False})
        assert login(c, "minsu.kim", "from-admin-1").json()["user"]["is_admin"] is False
        assert c.get("/api/admin/users").status_code == 403
        assert c.post(f"/api/admin/users/{boss_id}/password-reset").status_code == 403


def test_old_database_gets_new_columns(tmp_path):
    """관리자 권한·이메일 컬럼이 없던 예전 DB 도 서버를 켜면 컬럼이 추가되고 데이터는 그대로다."""
    data = tmp_path / "data"
    data.mkdir()
    engine = create_engine(f"sqlite:///{data / 'app.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, login_id VARCHAR(50) UNIQUE, password_hash VARCHAR(200), name VARCHAR(50), created_at DATETIME, last_login_at DATETIME)"))
        conn.execute(text("INSERT INTO users (login_id, password_hash, name, created_at) VALUES ('old', 'x', '예전', '2026-01-01 00:00:00')"))
    engine.dispose()
    with TestClient(create_app(Settings(data_dir=data, workers=2))) as c:
        with c.app.state.services.db() as db:
            row = db.execute(text("SELECT is_admin, email FROM users WHERE login_id = 'old'")).one()
        assert row == (0, "")  # 새 컬럼은 기본값(관리자 아님 · 이메일 없음)으로 채워진다
        c.post("/api/auth/signup", json={"login_id": "new.user", "password": "new-password-9", "name": "새사람"})
        assert login(c, "new.user", "new-password-9").json()["user"]["is_admin"] is False


def test_mock_mode_has_admin_account(tmp_path):
    settings = Settings(data_dir=tmp_path / "real", mock_mode=True, mock_delay=0, workers=2)
    with TestClient(create_app(settings)) as c:
        assert login(c, "admin", "admin1234").json()["user"]["is_admin"] is True
        assert [u["login_id"] for u in c.get("/api/admin/users").json()["users"]] == ["demo", "seoyeon.lee", "junho.park", "admin"]
        forgot = c.post("/api/auth/password/forgot", json={"login_id": "demo", "email": "minsu.kim@amigo.example"}).json()
        assert forgot["reset_token"]  # 가상 응답 모드는 메일 대신 응답으로 토큰을 준다
        assert login(c, "demo", "demo1234").json()["user"]["profile"]["department"] == "디지털전략부"


def test_profile_api_still_works(client):
    signup(client)
    login(client)
    assert client.put("/api/profile", json={"department": "디지털전략부", "position": "과장", "team": "", "name": "김민수"}).status_code == 200
    assert client.get("/api/me").json()["user"]["profile"]["department"] == "디지털전략부"
    assert PROFILE["owner_name"] == "김민수"
