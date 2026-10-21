"""받은 인수인계(인수자)와 관리자(팀장) API: 역할, 소속 범위, 인수자의 읽기 전용 접근."""

from sqlalchemy import select

from app.models import User

PASSWORD = "correct-horse-1"
TEAM_IT = {"department": "디지털본부", "team": "IT아미고팀"}
TEAM_AI = {"department": "디지털본부", "team": "AI개발팀"}


def signup(client, login_id: str, name: str, team: dict, position: str, role: str = "member") -> None:
    client.cookies.clear()
    assert client.post("/api/auth/signup", json={"login_id": login_id, "password": PASSWORD, "name": name}).status_code == 201
    login(client, login_id)
    assert client.put("/api/profile", json={**team, "position": position, "name": name}).status_code == 200
    if role == "admin":  # 관리자 권한은 회원가입으로 정할 수 없고 DB(또는 관리자 API)에서 지정한다
        services = client.app.state.services
        with services.db() as db:
            user = db.scalar(select(User).where(User.login_id == login_id))
            user.is_admin = True
            db.commit()


def login(client, login_id: str) -> None:
    client.cookies.clear()
    res = client.post("/api/auth/login", json={"login_id": login_id, "password": PASSWORD})
    assert res.status_code == 200


def setup_team(client) -> str:
    """IT 팀장 + 계장 2명, AI 팀장 + 계장 1명. 김민준 → 임윤희 인수인계 1건을 만들고 그 세션 ID 를 돌려준다."""
    signup(client, "amigoteamjang", "김미고", TEAM_IT, "팀장", role="admin")
    signup(client, "aiteamjang", "최고미", TEAM_AI, "팀장", role="admin")
    signup(client, "hwan", "최환", TEAM_IT, "계장")
    signup(client, "yunhui", "임윤희", TEAM_AI, "계장")
    signup(client, "demo", "김민준", TEAM_IT, "계장")
    res = client.post(
        "/api/unit-tasks",
        json={"tasks": [{"name": "AMIGO 서비스 운영", "description": "배포·장애 대응", "successor": "임윤희 계장", "successor_login_id": "YUNHUI", "handover_date": "2026-11-14"}]},
    )
    assert res.status_code == 201, res.text
    task = res.json()["tasks"][0]
    assert task["successor_login_id"] == "yunhui"
    return task["session_id"]


def test_role_in_login_and_me(client):
    signup(client, "amigoteamjang", "김미고", TEAM_IT, "팀장", role="admin")
    signup(client, "hwan", "최환", TEAM_IT, "계장")
    assert client.get("/api/auth/me").json()["user"]["is_admin"] is False
    login(client, "amigoteamjang")
    body = client.post("/api/auth/login", json={"login_id": "amigoteamjang", "password": PASSWORD}).json()
    assert body["user"]["is_admin"] is True


def test_members_list_requires_login_and_profile(client):
    assert client.get("/api/members").status_code == 401
    signup(client, "hwan", "최환", TEAM_IT, "계장")
    client.cookies.clear()
    assert client.post("/api/auth/signup", json={"login_id": "noprofile", "password": PASSWORD, "name": "무소속"}).status_code == 201
    login(client, "hwan")
    members = client.get("/api/members").json()["members"]
    assert [m["login_id"] for m in members] == ["hwan"]  # 프로필이 없는 계정은 목록에 없다
    assert members[0]["organization"] == "디지털본부 IT아미고팀" and members[0]["is_admin"] is False


def test_received_handovers_and_read_only_access(client):
    session_id = setup_team(client)

    login(client, "yunhui")
    received = client.get("/api/handovers/received").json()["handovers"]
    assert len(received) == 1 and received[0]["id"] == session_id
    assert received[0]["owner_name"] == "김민준" and received[0]["owner_login_id"] == "demo" and received[0]["task_name"] == "AMIGO 서비스 운영"
    assert received[0]["stage"] == "setup" and received[0]["document_version"] == 0

    # 인수자는 읽기만: 상태·문서 조회는 되고, 대화·삭제는 남의 세션처럼 404
    state = client.get(f"/api/sessions/{session_id}/state")
    assert state.status_code == 200 and state.json()["session"]["owner_login_id"] == "demo"  # 화면이 읽기 전용으로 그릴 근거
    assert client.post(f"/api/sessions/{session_id}/chat", json={"text": "안녕"}).status_code == 404
    assert client.delete(f"/api/sessions/{session_id}").status_code == 404

    # 인수자가 아닌 같은 팀 동료는 읽지도 못한다
    login(client, "hwan")
    assert client.get("/api/handovers/received").json()["handovers"] == []
    assert client.get(f"/api/sessions/{session_id}/state").status_code == 404


def test_admin_scope_is_own_team(client):
    session_id = setup_team(client)

    login(client, "hwan")
    assert client.get("/api/admin/members").status_code == 403
    assert client.get("/api/admin/handovers").status_code == 403

    login(client, "amigoteamjang")
    members = client.get("/api/admin/members").json()
    assert members["organization"] == "디지털본부 IT아미고팀"
    assert [m["login_id"] for m in members["members"]] == ["amigoteamjang", "demo", "hwan"]  # 관리자 먼저, 그다음 이름순
    demo = next(m for m in members["members"] if m["login_id"] == "demo")
    assert demo["handover_count"] == 1 and demo["completed_count"] == 0 and demo["last_login_at"]

    handovers = client.get("/api/admin/handovers").json()["handovers"]
    assert [h["id"] for h in handovers] == [session_id]
    assert handovers[0]["owner_login_id"] == "demo" and handovers[0]["successor"] == "임윤희 계장" and handovers[0]["successor_login_id"] == "yunhui"
    assert client.get(f"/api/sessions/{session_id}/state").status_code == 200  # 같은 팀 구성원의 세션은 읽을 수 있다
    assert client.post(f"/api/sessions/{session_id}/chat", json={"text": "안녕"}).status_code == 404

    login(client, "aiteamjang")  # 다른 팀 관리자에게는 보이지 않는다
    assert [m["login_id"] for m in client.get("/api/admin/members").json()["members"]] == ["aiteamjang", "yunhui"]
    assert client.get("/api/admin/handovers").json()["handovers"] == []
    assert client.get(f"/api/sessions/{session_id}/state").status_code == 404


def test_admin_deletes_member_with_handovers(client):
    session_id = setup_team(client)
    login(client, "amigoteamjang")
    me = client.get("/api/auth/me").json()["user"]["id"]
    assert client.delete(f"/api/admin/members/{me}").status_code == 400

    ai_member = next(m for m in client.get("/api/members").json()["members"] if m["login_id"] == "yunhui")
    assert client.delete(f"/api/admin/members/{ai_member['id']}").status_code == 404  # 다른 팀

    demo = next(m for m in client.get("/api/admin/members").json()["members"] if m["login_id"] == "demo")
    res = client.delete(f"/api/admin/members/{demo['id']}")
    assert res.status_code == 200 and "김민준" in res.json()["message"]
    assert [m["login_id"] for m in client.get("/api/admin/members").json()["members"]] == ["amigoteamjang", "hwan"]
    assert client.get("/api/admin/handovers").json()["handovers"] == []
    assert client.post("/api/auth/login", json={"login_id": "demo", "password": PASSWORD}).status_code == 401
    login(client, "yunhui")
    assert client.get("/api/handovers/received").json()["handovers"] == []
    assert client.get(f"/api/sessions/{session_id}/state").status_code == 404
