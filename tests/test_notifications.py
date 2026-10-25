"""알림: 받은 인수인계서 도착·수정 알림을 확인(읽음)·지우기·모두 확인한다. 상태는 계정별로 서버에 남는다."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def mock_client(tmp_path):
    settings = Settings(data_dir=tmp_path / "real-data", mock_mode=True, mock_delay=0, workers=4)
    with TestClient(create_app(settings)) as c:
        yield c


def login(client, login_id, password="demo1234"):
    assert client.post("/api/auth/login", json={"login_id": login_id, "password": password}).status_code == 200


def test_read_dismiss_and_new_version(mock_client):
    login(mock_client, "demo")  # 시드: 이서연이 '모바일 웹 운영' 인수인계서를 김민수에게 보내 두었다
    body = mock_client.get("/api/notifications").json()
    assert body["unread"] == 1 and len(body["notifications"]) == 1
    first = body["notifications"][0]
    assert first["kind"] == "received" and first["read"] is False and "모바일 웹 운영" in first["text"]

    body = mock_client.post("/api/notifications/read", json={"ids": [first["id"]]}).json()
    assert body["unread"] == 0 and body["notifications"][0]["read"] is True
    assert mock_client.post("/api/notifications/read", json={"ids": ["transfer:nope:v1"]}).status_code == 404
    mock_client.post("/api/auth/logout")

    # 인계자가 문서를 고치면 새 버전 알림이 다시 뜬다
    login(mock_client, "seoyeon.lee")
    sid = next(t for t in mock_client.get("/api/unit-tasks").json()["tasks"] if t["name"] == "모바일 웹 운영")["session_id"]
    doc = mock_client.get(f"/api/sessions/{sid}/document").json()
    assert mock_client.put(f"/api/sessions/{sid}/document", json={"markdown": doc["markdown"] + "\n보낸 뒤 고침", "base_version": doc["version"]}).status_code == 200
    mock_client.post("/api/auth/logout")

    login(mock_client, "demo")
    body = mock_client.get("/api/notifications").json()
    assert body["unread"] == 1 and body["notifications"][0]["kind"] == "updated" and body["notifications"][0]["id"] != first["id"]
    assert mock_client.post("/api/notifications/read", json={"ids": None}).json()["unread"] == 0  # 모두 확인

    body = mock_client.post("/api/notifications/dismiss", json={"ids": [body["notifications"][0]["id"]]}).json()
    assert body["notifications"] == [] and body["unread"] == 0
    assert len(mock_client.get("/api/handovers/received").json()["handovers"]) == 1  # 알림만 지우고 받은 인수인계서는 남는다


def test_notifications_need_login(mock_client):
    assert mock_client.get("/api/notifications").status_code == 401
