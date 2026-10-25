"""팀 담당 업무 목록: 팀장이 추가·수정·삭제하고, 같은 소속 구성원은 읽어서 인수인계 1단계에서 고른다."""

from sqlalchemy import select

from app.models import User

PASSWORD = "correct-horse-1"
TEAM = {"department": "디지털본부", "team": "IT아미고팀"}
OTHER = {"department": "디지털본부", "team": "AI개발팀"}
URL = "/api/team-duties/me"


def signup(client, login_id: str, team: dict | None, admin: bool = False) -> None:
    client.cookies.clear()
    assert client.post("/api/auth/signup", json={"login_id": login_id, "password": PASSWORD, "name": login_id}).status_code == 201
    login(client, login_id)
    if team:
        assert client.put("/api/profile", json={**team, "position": "과장", "name": login_id}).status_code == 200
    if admin:
        with client.app.state.services.db() as db:
            db.scalar(select(User).where(User.login_id == login_id)).is_admin = True
            db.commit()


def login(client, login_id: str) -> None:
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"login_id": login_id, "password": PASSWORD}).status_code == 200


def test_login_and_profile_are_required(client):
    assert client.get(URL).status_code == 401
    signup(client, "noprofile", None)
    res = client.get(URL)
    assert res.status_code == 400 and "프로필" in res.json()["message"]


def test_admin_crud_and_member_reads(client):
    signup(client, "boss", TEAM, admin=True)
    empty = client.get(URL).json()
    assert empty["organization"] == "디지털본부 IT아미고팀" and empty["duties"] == [] and empty["editable"] is True

    res = client.post(URL, json={"name": " 홈페이지  운영 ", "description": "콘텐츠 게시·점검"})
    assert res.status_code == 201 and [d["name"] for d in res.json()["duties"]] == ["홈페이지 운영"]
    client.post(URL, json={"name": "보안 점검"})
    dup = client.post(URL, json={"name": "홈페이지 운영"})
    assert dup.status_code == 409
    assert client.post(URL, json={"name": "  "}).status_code == 422

    duties = client.get(URL).json()["duties"]
    assert [d["name"] for d in duties] == ["홈페이지 운영", "보안 점검"]
    first, second = duties
    renamed = client.patch(f"{URL}/{second['id']}", json={"name": "보안 취약점 점검", "sort_order": 0}).json()["duties"]
    assert [d["name"] for d in renamed] == ["보안 취약점 점검", "홈페이지 운영"]
    assert client.patch(f"{URL}/{second['id']}", json={"name": "홈페이지 운영"}).status_code == 409
    assert client.delete(f"{URL}/{first['id']}").json()["duties"][0]["name"] == "보안 취약점 점검"
    assert client.delete(f"{URL}/{first['id']}").status_code == 404

    signup(client, "member", TEAM)
    view = client.get(URL).json()
    assert view["editable"] is False and [d["name"] for d in view["duties"]] == ["보안 취약점 점검"]
    assert client.post(URL, json={"name": "새 업무"}).status_code == 403
    assert client.patch(f"{URL}/{second['id']}", json={"name": "x"}).status_code == 403
    assert client.delete(f"{URL}/{second['id']}").status_code == 403


def test_duties_are_per_team(client):
    signup(client, "boss", TEAM, admin=True)
    duty = client.post(URL, json={"name": "홈페이지 운영"}).json()["duties"][0]
    signup(client, "other.boss", OTHER, admin=True)
    assert client.get(URL).json()["duties"] == []
    assert client.post(URL, json={"name": "홈페이지 운영"}).status_code == 201  # 다른 팀은 같은 이름을 따로 둔다
    assert client.delete(f"{URL}/{duty['id']}").status_code == 404  # 다른 팀 업무는 못 지운다
