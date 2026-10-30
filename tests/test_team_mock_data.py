"""팀별 가상 메일·지라·컨플루언스: 사용자의 팀(profile.team)에 따라 다른 데이터가 보이고, 모르는 팀은 기본 데이터를 본다."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.org_duties import has_team, team_options

TEAMS = {
    "디지털사업팀": ("디지털금융부", "신규 서비스", "DBZ", "2101"),
    "법무팀": ("총무부", "계약", "LGL", "2201"),
    "페이먼트사업팀": ("결제사업부", "카드", "CRD", "2301"),
    "기획조정팀": ("경영전략부", "경영", "PLN", "2401"),
}


@pytest.fixture()
def client(tmp_path):
    settings = Settings(data_dir=tmp_path / "real-data", mock_mode=True, mock_delay=0, workers=4)
    with TestClient(create_app(settings)) as c:
        yield c


def signup_login(client, login_id: str, department: str = "", team: str = ""):
    body = {"login_id": login_id, "password": "pass1234!", "name": "테스터", "email": f"{login_id}@amigo.example", "employee_no": "", "department": department, "team": team}
    res = client.post("/api/auth/signup", json=body)
    assert res.status_code == 201, res.text
    assert client.post("/api/auth/login", json={"login_id": login_id, "password": "pass1234!"}).status_code == 200


def test_all_five_teams_are_selectable_at_signup():
    teams = {**TEAMS, "IT아미고팀": ("IT서비스부",)}
    for team, (division, *_rest) in teams.items():
        assert has_team(division, team)
    names = [t for row in team_options() for t in row["teams"]]
    assert all(names.count(team) == 1 for team in teams) and len(names) == 5  # 법무팀 등 중복 없이, 팀은 5개뿐


def test_it_operations_team_sees_the_default_data(client):
    """기본 메일·지라·컨플루언스 데이터는 IT아미고팀의 것이다."""
    signup_login(client, "itops", "IT서비스부", "IT아미고팀")
    mails = client.get("/api/mail/messages", params={"size": 50}).json()["messages"]
    assert mails and all(m["id"].startswith("m-") for m in mails)
    assert "WEB" in {p["key"] for p in client.get("/api/integrations/jira/projects").json()["projects"]}


@pytest.mark.parametrize("team", list(TEAMS))
def test_team_user_sees_own_team_data(client, team):
    division, word, key, page_id = TEAMS[team]
    signup_login(client, "u" + key.lower(), division, team)
    mails = client.get("/api/mail/messages", params={"size": 50}).json()["messages"]
    assert len(mails) >= 4 and {m["id"].split("-")[0] for m in mails} == {key.lower()}
    detail = client.get(f"/api/mail/messages/{mails[0]['id']}")
    assert detail.status_code == 200

    projects = client.get("/api/integrations/jira/projects").json()["projects"]
    assert [p["key"] for p in projects] == [key] and projects[0]["epics"] and projects[0]["sprint"]
    assert client.get("/api/integrations/jira/search").json()["total"] >= 6

    found = client.get("/api/integrations/confluence/search").json()
    assert found["total"] >= 3 and {r["space_key"] for r in found["results"]} == {key} and page_id in {r["id"] for r in found["results"]}


def test_card_team_sees_card_mails_and_default_team_does_not(client):
    signup_login(client, "cardu", "결제사업부", "페이먼트사업팀")
    mails = client.get("/api/mail/messages", params={"q": "정산", "size": 50}).json()["messages"]
    assert mails and all(m["id"].startswith("crd-") for m in mails)
    client.post("/api/auth/logout")
    signup_login(client, "nobody")  # 팀 없음 → 기본 메일함·기본 지라·기본 위키
    default = client.get("/api/mail/messages", params={"size": 50}).json()["messages"]
    assert default and all(m["id"].startswith("m-") for m in default)
    assert {p["key"] for p in client.get("/api/integrations/jira/projects").json()["projects"]} == {"AMG", "INFRA", "AIDEV", "WEB"}
    spaces = {r["space_key"] for r in client.get("/api/integrations/confluence/search", params={"limit": 50}).json()["results"]}
    assert spaces <= {"WEB", "DSD", "SEC", "FIN", "HR"}


def test_unknown_team_falls_back_to_default(client):
    signup_login(client, "other")
    assert client.put("/api/profile", json={"department": "없는본부", "position": "과장", "team": "없는팀", "name": "테스터"}).status_code == 200  # 목록에 없는 임의의 팀
    mails = client.get("/api/mail/messages", params={"size": 50}).json()["messages"]
    assert all(m["id"].startswith("m-") for m in mails)
    assert "WEB" in {p["key"] for p in client.get("/api/integrations/jira/projects").json()["projects"]}
