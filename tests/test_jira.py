"""가상 Jira: 프로젝트 진행 현황·이슈 검색·상세, 고른 이슈·에픽·진행 현황을 인수인계 자료로 등록."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def mock_client(tmp_path):
    settings = Settings(data_dir=tmp_path / "real-data", mock_mode=True, mock_delay=0, workers=4)
    with TestClient(create_app(settings)) as c:
        yield c


def test_projects_search_and_detail(mock_client):
    projects = mock_client.get("/api/integrations/jira/projects").json()["projects"]
    amg = next(p for p in projects if p["key"] == "AMG")
    assert amg["sprint"]["name"] == "AMG 스프린트 14" and 0 < amg["sprint"]["percent"] < 100
    assert any(e["key"] == "AMG-3" and e["percent"] == 100 for e in amg["epics"])
    assert amg["blocked"] and amg["open_by_assignee"]

    found = mock_client.get("/api/integrations/jira/search", params={"q": "지라", "project": "AMG"}).json()
    assert [i["key"] for i in found["issues"]][:1] == ["AMG-13"]
    assert mock_client.get("/api/integrations/jira/search", params={"status": "완료", "assignee": "최환"}).json()["total"] >= 2

    epic = mock_client.get("/api/integrations/jira/issues/AMG-1").json()
    assert epic["type"] == "에픽" and len(epic["children"]) >= 5 and epic["progress"]["percent"] > 0
    assert mock_client.get("/api/integrations/jira/issues/NOPE-1").status_code == 404


def test_import_into_session(mock_client):
    assert mock_client.post("/api/auth/login", json={"login_id": "demo", "password": "demo1234"}).status_code == 200
    sid = next(t for t in mock_client.get("/api/unit-tasks").json()["tasks"] if t["name"] == "유지보수 계약 관리")["session_id"]
    res = mock_client.post(f"/api/sessions/{sid}/integrations/jira", json={"item_ids": ["project:AMG", "AMG-1", "AMG-13"]})
    assert res.status_code == 201, res.text
    names = [s["name"] for s in res.json()["sources"]]
    assert names[0] == "지라 · AMIGO 인수인계 서비스 진행 현황" and all(s["link_type"] == "jira" for s in res.json()["sources"])
    again = mock_client.post(f"/api/sessions/{sid}/integrations/jira", json={"item_ids": ["AMG-1"]})
    assert again.status_code == 409
    assert mock_client.post(f"/api/sessions/{sid}/integrations/jira", json={"item_ids": ["XX-9"]}).status_code == 404


def test_mock_site_pages(mock_client):
    assert "AMIGO 인수인계 서비스" in mock_client.get("/jira").text
    assert "보드" in mock_client.get("/jira/projects/AMG").text
    assert "하위 이슈" in mock_client.get("/jira/browse/AMG-1").text


def test_move_issue_updates_progress(mock_client):
    before = next(p for p in mock_client.get("/api/integrations/jira/projects").json()["projects"] if p["key"] == "AMG")["sprint"]["done_points"]
    res = mock_client.patch("/api/integrations/jira/issues/AMG-13", json={"status": "완료"})
    assert res.status_code == 200 and res.json()["status"] == "완료"
    after = next(p for p in mock_client.get("/api/integrations/jira/projects").json()["projects"] if p["key"] == "AMG")["sprint"]["done_points"]
    assert after == before + 8
    assert mock_client.patch("/api/integrations/jira/issues/AMG-13", json={"status": "보류"}).status_code == 422
    mock_client.patch("/api/integrations/jira/issues/AMG-13", json={"status": "진행 중"})  # 다른 테스트를 위해 되돌림

