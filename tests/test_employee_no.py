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


def test_profile_edit_changes_employee_no(client):
    signup(client)
    login(client)
    res = client.put("/api/profile", json={**PROFILE_BODY, "employee_no": "1251023"})
    assert res.status_code == 200 and res.json()["profile"]["employee_no"] == "1251023"
    assert client.get("/api/auth/me").json()["user"]["employee_no"] == "1251023"
    # 보내지 않으면 그대로, 빈 문자열이면 비운다
    assert client.put("/api/profile", json=PROFILE_BODY).json()["profile"]["employee_no"] == "1251023"
    assert client.put("/api/profile", json={**PROFILE_BODY, "employee_no": ""}).json()["profile"]["employee_no"] == ""


def test_profile_edit_rejects_someone_elses_employee_no(client):
    signup(client, {**ACCOUNT, "employee_no": "1111111"})
    client.post("/api/auth/logout")
    signup(client, {"login_id": "other", "password": "correct-horse-2", "name": "이서연"})
    login(client, "other", "correct-horse-2")
    res = client.put("/api/profile", json={**PROFILE_BODY, "name": "이서연", "employee_no": "1111111"})
    assert res.status_code == 409
    assert client.get("/api/auth/me").json()["user"]["employee_no"] == ""


def test_mypage_edit_changes_employee_no(client):
    signup(client)
    login(client)
    res = client.patch("/api/me", json={"employee_no": "1231207"})
    assert res.status_code == 200 and res.json()["user"]["employee_no"] == "1231207"


def test_employee_no_format_is_checked(client):
    res = client.post("/api/auth/signup", json={**ACCOUNT, "employee_no": "12 34!"})
    assert res.status_code == 422
