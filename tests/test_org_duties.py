"""직제규정 시행세칙(가상 예시) 조회와 인계자 정보의 단위업무·세부업무(정기·비정기) 저장."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.org_duties import DEFAULT_FILE, org_duties, team_duties
from conftest import PROFILE


def test_default_file_lists_units_for_every_mock_team():
    data = json.loads(DEFAULT_FILE.read_text(encoding="utf-8"))
    assert "가상 예시" in data["title"] and "가상 예시" in data["note"]
    teams = {(d["name"], t["name"]): t["duties"] for d in data["divisions"] for t in d["teams"]}
    assert {("디지털전략부", "웹서비스팀"), ("디지털전략부", "IT운영팀"), ("디지털본부", "IT아미고팀"), ("디지털본부", "AI개발팀")} <= set(teams)
    for team in ("재무팀", "정보보호팀", "IT지원팀", "법무팀", "총무팀", "홍보팀", "인사팀"):
        assert any(name == team for _, name in teams)
    assert all(5 <= len(duties) <= 8 and len(set(duties)) == len(duties) for duties in teams.values())
    # 목업 시나리오(웹서비스팀 단위업무)와 이름이 맞는다
    assert {"기관 홈페이지 운영", "웹 접근성 관리", "유지보수 계약 관리", "모바일 웹 운영"} <= set(teams[("디지털전략부", "웹서비스팀")])
    assert "규제심사위원회 운영" in team_duties("경영지원본부", "법무팀")
    assert team_duties("없는 본부", "웹서비스팀") == [] and team_duties("디지털전략부", "없는 팀") == []


def test_custom_file_is_cleaned_and_missing_file_is_empty(tmp_path):
    custom = tmp_path / "duties.json"  # AMIGO_ORG_DUTIES_FILE 로 실제 업무분장을 쓰는 경우
    custom.write_text(json.dumps({"title": "시행세칙", "divisions": [
        {"name": " 가나본부 ", "teams": [{"name": "다라팀", "duties": [" 업무1 ", "", "업무1", 3, "업무2"]}, {"name": ""}]},
        {"teams": []},
    ]}), encoding="utf-8")
    assert org_duties(str(custom)) == {"title": "시행세칙", "note": "", "divisions": [{"name": "가나본부", "teams": [{"name": "다라팀", "duties": ["업무1", "업무2"]}]}]}
    assert org_duties(str(tmp_path / "missing.json"))["divisions"] == []


def test_org_duties_api_requires_login(client):
    assert client.get("/api/org-duties").status_code == 401
    client.post("/api/auth/signup", json={"login_id": "orguser", "password": "Abcd1234!x", "name": "조직"})
    assert client.post("/api/auth/login", json={"login_id": "orguser", "password": "Abcd1234!x"}).status_code == 200
    body = client.get("/api/org-duties").json()
    assert body["success"] and "가상 예시" in body["title"]
    web = next(t for d in body["divisions"] if d["name"] == "디지털전략부" for t in d["teams"] if t["name"] == "웹서비스팀")
    assert web["duties"][0] == "기관 홈페이지 운영"


def test_session_saves_units_and_passes_them_to_the_agent_profile(client):
    units = [
        {"name": " 규제심사위원회 운영 ", "regular": ["월간 심사 안건 정리", " "], "irregular": ["위원 위촉 갱신"]},
        {"name": "법령검토", "regular": [], "irregular": []},
        {"name": "", "regular": [], "irregular": []},  # 이름도 세부업무도 없으면 버린다
    ]
    created = client.post("/api/sessions", json={**PROFILE, "task_name": "규제심사위원회 운영, 법령검토", "units": units})
    assert created.status_code == 201
    expected = [
        {"name": "규제심사위원회 운영", "regular": ["월간 심사 안건 정리"], "irregular": ["위원 위촉 갱신"]},
        {"name": "법령검토", "regular": [], "irregular": []},
    ]
    assert created.json()["units"] == expected
    sid = created.json()["id"]
    assert client.get(f"/api/sessions/{sid}").json()["units"] == expected

    from app.models import HandoverSession

    with client.app.state.services.db() as db:
        assert db.get(HandoverSession, sid).profile()["units"] == expected

    # 예전 화면처럼 units 없이 만들면 빈 목록
    assert client.post("/api/sessions", json=PROFILE).json()["units"] == []


@pytest.mark.parametrize(
    "units",
    [
        [{"name": "가" * 201}],
        [{"name": "업무", "regular": ["가" * 201]}],
        [{"name": "업무", "irregular": ["x"] * 51}],
        [{"name": f"업무{i}"} for i in range(31)],
    ],
)
def test_units_are_limited(client, units):
    assert client.post("/api/sessions", json={**PROFILE, "units": units}).status_code == 422


@pytest.fixture()
def mock_client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path / "d", mock_mode=True, mock_delay=0, workers=4))) as c:
        yield c


def test_mock_agent_names_overview_items_after_selected_units(mock_client):
    assert mock_client.post("/api/auth/login", json={"login_id": "demo", "password": "demo1234"}).status_code == 200
    units = [{"name": "기관 홈페이지 운영", "regular": ["공지 게시 승인"]}, {"name": "웹 접근성 관리", "irregular": ["인증 갱신"]}, {"name": "SNS 채널 관리"}]
    sid = mock_client.post("/api/sessions", json={**PROFILE, "task_name": "기관 홈페이지 운영, 웹 접근성 관리, SNS 채널 관리", "units": units}).json()["id"]
    files = [("files", ("업무정의서.pdf", "가상 자료".encode(), "application/octet-stream"))]
    assert mock_client.post(f"/api/sessions/{sid}/upload", files=files).status_code in (200, 201)
    deadline = time.time() + 20
    while time.time() < deadline:
        session = mock_client.get(f"/api/sessions/{sid}").json()
        if session["sources"] and all(s["status"] in ("ready", "failed") for s in session["sources"]):
            break
        time.sleep(0.05)
    assert mock_client.post(f"/api/sessions/{sid}/analyze").status_code == 202
    while time.time() < deadline:
        session = mock_client.get(f"/api/sessions/{sid}").json()
        if session["status"] == "waiting":
            break
        time.sleep(0.05)
    names = [item["fields"]["name"] for item in session["slots"]["overview"]["items"]]
    assert names == ["기관 홈페이지 운영", "웹 접근성 관리", "SNS 채널 관리"]
