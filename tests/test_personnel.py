"""정기인사 일정: 팀장이 등록(1년에 여러 번), 언제나 팀 단위라 구성원은 자기 팀 일정만 본다."""

from sqlalchemy import select

from app.models import User

PASSWORD = "correct-horse-1"
TEAM_IT = {"department": "디지털본부", "team": "IT아미고팀"}
TEAM_AI = {"department": "디지털본부", "team": "AI개발팀"}


def signup(client, login_id: str, name: str, team: dict | None, admin: bool = False) -> None:
    client.cookies.clear()
    assert client.post("/api/auth/signup", json={"login_id": login_id, "password": PASSWORD, "name": name}).status_code == 201
    login(client, login_id)
    if team:
        assert client.put("/api/profile", json={**team, "position": "팀장" if admin else "계장", "name": name}).status_code == 200
    if admin:
        with client.app.state.services.db() as db:
            db.scalar(select(User).where(User.login_id == login_id)).is_admin = True
            db.commit()


def login(client, login_id: str) -> None:
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"login_id": login_id, "password": PASSWORD}).status_code == 200


def test_admin_registers_several_and_only_own_team_sees_them(client):
    signup(client, "it.lead", "김미고", TEAM_IT, admin=True)
    assert client.get("/api/personnel-schedules").json()["schedules"] == []

    res = client.post("/api/admin/personnel-schedules", json={"name": "2027 상반기 정기인사", "announce_date": "2027-01-10", "appoint_date": "2027-01-15", "scope": "all"})
    assert res.status_code == 201 and res.json()["schedule"]["organization"] == "디지털본부 IT아미고팀"  # 예전 scope=all 을 보내도 팀 일정
    team = client.post("/api/admin/personnel-schedules", json={"name": "IT아미고팀 수시 인사", "appoint_date": "2026-11-20"}).json()["schedule"]
    assert team["organization"] == "디지털본부 IT아미고팀" and team["announce_date"] is None
    client.post("/api/admin/personnel-schedules", json={"name": "2027 하반기 정기인사", "announce_date": "2027-07-05", "appoint_date": "2027-07-12"})

    mine = client.get("/api/personnel-schedules").json()
    assert mine["editable"] is True
    assert [s["name"] for s in mine["schedules"]] == ["IT아미고팀 수시 인사", "2027 상반기 정기인사", "2027 하반기 정기인사"]  # 발령일 순

    # 같은 팀 구성원: 우리 팀 일정
    signup(client, "it.member", "최기아", TEAM_IT)
    member = client.get("/api/personnel-schedules").json()
    assert member["editable"] is False and len(member["schedules"]) == 3
    assert client.post("/api/admin/personnel-schedules", json={"name": "x", "appoint_date": "2027-01-01"}).status_code == 403

    # 다른 팀: 하나도 보이지 않는다
    signup(client, "ai.member", "임삼성", TEAM_AI)
    assert client.get("/api/personnel-schedules").json()["schedules"] == []


def test_update_delete_and_scope_rules(client):
    signup(client, "it.lead", "김미고", TEAM_IT, admin=True)
    sid = client.post("/api/admin/personnel-schedules", json={"name": "IT 수시 인사", "appoint_date": "2026-11-20"}).json()["schedule"]["id"]
    res = client.put(f"/api/admin/personnel-schedules/{sid}", json={"name": "IT 수시 인사(연기)", "announce_date": "2026-11-25", "appoint_date": "2026-12-01"})
    assert res.status_code == 200 and res.json()["schedule"]["appoint_date"] == "2026-12-01"

    # 다른 팀 팀장은 이 팀 일정을 보지도, 고치지도 못한다
    signup(client, "ai.lead", "최고미", TEAM_AI, admin=True)
    assert client.get("/api/personnel-schedules").json()["schedules"] == []
    assert client.put(f"/api/admin/personnel-schedules/{sid}", json={"name": "x", "appoint_date": "2027-01-01"}).status_code == 404
    assert client.delete(f"/api/admin/personnel-schedules/{sid}").status_code == 404

    login(client, "it.lead")
    assert client.delete(f"/api/admin/personnel-schedules/{sid}").json()["message"] == "'IT 수시 인사(연기)' 일정을 지웠습니다."
    assert client.get("/api/personnel-schedules").json()["schedules"] == []


def test_validation(client):
    signup(client, "it.lead", "김미고", TEAM_IT, admin=True)
    bad = [
        {"name": "", "appoint_date": "2027-01-15"},
        {"name": "날짜 없음", "appoint_date": ""},
        {"name": "형식 틀림", "appoint_date": "2027/01/15"},
        {"name": "순서 틀림", "announce_date": "2027-01-20", "appoint_date": "2027-01-15"},
    ]
    for body in bad:
        assert client.post("/api/admin/personnel-schedules", json=body).status_code == 422, body
    # 프로필(소속)이 없는 관리자는 일정을 만들 수 없다
    signup(client, "no.team", "무소속", None, admin=True)
    assert client.post("/api/admin/personnel-schedules", json={"name": "팀", "appoint_date": "2027-01-15"}).status_code == 400
    assert client.post("/api/admin/personnel-schedules", json={"name": "전사", "appoint_date": "2027-01-15", "scope": "all"}).status_code == 400
    assert client.get("/api/personnel-schedules").json()["schedules"] == []
    client.cookies.clear()
    assert client.get("/api/personnel-schedules").status_code == 401
