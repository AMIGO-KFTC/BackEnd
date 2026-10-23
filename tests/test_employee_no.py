"""사번(employee_no): 로그인 아이디(login_id)와 별개 값. 가입·프로필 수정·내 정보 수정에서 다루고, 다른 계정과 겹치면 막는다."""

from test_accounts import ACCOUNT, login, signup

PROFILE_BODY = {"name": "김민수", "department": "디지털전략부", "position": "과장", "team": "웹서비스팀"}


def test_signup_saves_employee_no_separately_from_login_id(client):
    user = signup(client, {**ACCOUNT, "employee_no": "1111111"})
    assert user["login_id"] == "minsu.kim" and user["employee_no"] == "1111111"
    me = login(client).json()["user"]
    assert me["login_id"] == "minsu.kim" and me["employee_no"] == "1111111"


def test_signup_without_employee_no_is_allowed(client):
    assert signup(client)["employee_no"] == ""


def test_employee_no_must_be_unique(client):
    signup(client, {**ACCOUNT, "employee_no": "1111111"})
    other = {"login_id": "other", "password": "correct-horse-2", "name": "이서연", "employee_no": "1111111"}
    res = client.post("/api/auth/signup", json=other)
    assert res.status_code == 409 and "사번" in res.json()["detail"]
    assert client.post("/api/auth/signup", json={**other, "employee_no": "1251023"}).status_code == 201


def test_profile_edit_never_changes_employee_no(client):
    signup(client, {**ACCOUNT, "employee_no": "1251023"})
    login(client)
    # 보내지 않거나 같은 값을 보내는 것만 된다. 다른 값·빈 값은 막는다
    assert client.put("/api/profile", json=PROFILE_BODY).json()["profile"]["employee_no"] == "1251023"
    assert client.put("/api/profile", json={**PROFILE_BODY, "employee_no": "1251023"}).status_code == 200
    for other in ("7654321", ""):
        res = client.put("/api/profile", json={**PROFILE_BODY, "employee_no": other})
        assert res.status_code == 409 and res.json()["message"] == "사번은 바꿀 수 없습니다."
    assert client.get("/api/auth/me").json()["user"]["employee_no"] == "1251023"


def test_profile_edit_rejects_someone_elses_employee_no(client):
    signup(client, {**ACCOUNT, "employee_no": "1111111"})
    client.post("/api/auth/logout")
    signup(client, {"login_id": "other", "password": "correct-horse-2", "name": "이서연"})
    login(client, "other", "correct-horse-2")
    res = client.put("/api/profile", json={**PROFILE_BODY, "name": "이서연", "employee_no": "1111111"})
    assert res.status_code == 409
    assert client.get("/api/auth/me").json()["user"]["employee_no"] == ""


def test_mypage_edit_never_changes_employee_no(client):
    signup(client, {**ACCOUNT, "employee_no": "1251023"})
    login(client)
    assert client.patch("/api/me", json={"employee_no": "1231207"}).status_code == 409
    assert client.patch("/api/me", json={"employee_no": ""}).status_code == 409
    assert client.patch("/api/me", json={"employee_no": "1251023"}).status_code == 200  # 같은 값은 그대로
    assert client.get("/api/auth/me").json()["user"]["employee_no"] == "1251023"
    # 사번이 비어 있는 계정도 나중에 채울 수 없다
    client.post("/api/auth/logout")
    signup(client, {"login_id": "no.number", "password": "correct-horse-2", "name": "무사번"})
    login(client, "no.number", "correct-horse-2")
    assert client.patch("/api/me", json={"employee_no": "1999999"}).status_code == 409


def test_employee_no_format_is_checked(client):
    res = client.post("/api/auth/signup", json={**ACCOUNT, "employee_no": "12 34!"})
    assert res.status_code == 422


def test_numeric_login_id_is_the_employee_no(client):
    signup(client, {**ACCOUNT, "login_id": "1234567"})
    login(client, "1234567")
    assert client.get("/api/auth/me").json()["user"]["employee_no"] == "1234567"
    assert client.put("/api/profile", json={**PROFILE_BODY, "employee_no": "1234567"}).status_code == 200


def test_login_and_forgot_password_accept_employee_no_or_login_id(client):
    signup(client, {**ACCOUNT, "employee_no": "1251023"})
    client.post("/api/auth/logout")
    for who in ("minsu.kim", "1251023"):  # 아이디로도, 사번으로도 같은 계정에 들어간다
        res = login(client, who)
        assert res.status_code == 200 and res.json()["user"]["login_id"] == "minsu.kim"
    assert login(client, "1251023", "wrong-password").status_code == 401
    assert login(client, "9999999").status_code == 401

    # 사번이 겹쳐 하나로 정해지지 않으면(예전 데이터) 사번으로는 들어가지 못한다. 아이디는 그대로 된다
    with client.app.state.services.db() as db:
        from app.models import User

        db.add(User(login_id="dup.holder", password_hash="x", name="중복", employee_no="1251023"))
        db.commit()
    assert login(client, "1251023").status_code == 401
    assert login(client, "minsu.kim").status_code == 200

    body = {"login_id": "minsu.kim", "email": "Minsu.Kim@amigo.example"}
    assert client.post("/api/auth/password/forgot", json=body).status_code == 200
