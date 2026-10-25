"""팀장(관리자)도 자기 인수인계는 만들고 고치고 보낼 수 있다. 팀원의 인수인계는 읽기(검토)만 되고, 고치는 요청은 404 다."""

import pytest
from sqlalchemy import select

from app.models import HandoverSession, User

from test_handovers_admin import TEAM_IT, login, signup

DOC = "# 인수인계서\n\n## 개요\n내용"
FILE = [("files", ("memo.txt", "배포 절차 메모".encode(), "text/plain"))]


def member_write_calls(session_id: str, task_id: str, recipient_id: int) -> list[tuple[str, str, dict]]:
    """(메서드, 경로, 요청 인자): 팀원 인수인계를 고치는 API. 팀장이 보내면 모두 404(남의 세션·단위업무)."""
    s = f"/api/sessions/{session_id}"
    return [
        ("PATCH", f"/api/unit-tasks/{task_id}", {"json": {"description": "수정"}}),
        ("DELETE", f"/api/unit-tasks/{task_id}", {}),
        ("POST", f"/api/unit-tasks/{task_id}/files", {"files": FILE}),
        ("DELETE", s, {}),
        ("POST", f"{s}/upload", {"files": FILE}),
        ("POST", f"{s}/links", {"json": {"urls": ["https://example.com"]}}),
        ("POST", f"{s}/analyze", {}),
        ("POST", f"{s}/chat", {"json": {"text": "안녕"}}),
        ("PUT", f"{s}/document", {"json": {"markdown": DOC + "\n수정", "base_version": 1}}),
        ("POST", f"{s}/document/versions/1/restore", {}),
        ("POST", f"{s}/transfers", {"json": {"recipient_id": recipient_id}}),
    ]


@pytest.fixture()
def settings(settings):
    """자료를 올려도 자동 분석을 시작하지 않는다: 분석이 도는 동안에는 문서 저장이 409 라 테스트가 시점에 따라 흔들린다."""
    settings.auto_analyze = False
    return settings


@pytest.fixture()
def team(client):
    """IT 팀장 김미고(관리자) + 계장 김민준(demo), 최환(hwan). 김민준의 단위업무 1건(문서 v1)을 만든다."""
    signup(client, "amigoteamjang", "김미고", TEAM_IT, "팀장", role="admin")
    signup(client, "hwan", "최환", TEAM_IT, "계장")
    signup(client, "demo", "김민준", TEAM_IT, "계장")
    res = client.post("/api/unit-tasks", json={"tasks": [{"name": "AMIGO 서비스 운영", "description": "배포·장애 대응"}]})
    assert res.status_code == 201, res.text
    task = res.json()["tasks"][0]
    with client.app.state.services.db() as db:
        row = db.get(HandoverSession, task["session_id"])
        row.document_md, row.document_version = DOC, 1
        db.commit()
        hwan = db.scalar(select(User.id).where(User.login_id == "hwan"))
    return {"session_id": task["session_id"], "task_id": task["id"], "recipient_id": hwan}


def test_admin_cannot_write_member_handover(client, team):
    login(client, "amigoteamjang")
    for method, path, kwargs in member_write_calls(team["session_id"], team["task_id"], team["recipient_id"]):
        res = client.request(method, path, **kwargs)
        assert res.status_code == 404, f"{method} {path}: {res.status_code} {res.text}"

    # 팀원 인수인계는 아무것도 바뀌지 않았다
    with client.app.state.services.db() as db:
        row = db.get(HandoverSession, team["session_id"])
        assert row.document_md == DOC and row.document_version == 1 and row.sources == []


def test_admin_still_reads_member_handover(client, team):
    login(client, "amigoteamjang")
    s = f"/api/sessions/{team['session_id']}"
    for path in (f"{s}", f"{s}/state", f"{s}/messages", f"{s}/document", f"{s}/document/versions", f"{s}/sources", "/api/admin/handovers", "/api/admin/members"):
        assert client.get(path).status_code == 200, path
    assert client.get(f"{s}/download", params={"format": "md"}).status_code == 200
    assert client.get("/api/unit-tasks").json()["tasks"] == []  # 팀장 자신의 단위업무는 없다


def test_admin_writes_and_sends_own_handover(client, team):
    """팀장도 자기 인수인계를 만들고, 자료를 올리고, 문서를 고쳐 인수자에게 보낼 수 있다."""
    login(client, "amigoteamjang")
    res = client.post("/api/unit-tasks", json={"tasks": [{"name": "IT아미고팀 운영 총괄", "description": "팀 업무 분장·본부 보고"}]})
    assert res.status_code == 201, res.text
    task = res.json()["tasks"][0]
    s = f"/api/sessions/{task['session_id']}"
    assert client.patch(f"/api/unit-tasks/{task['id']}", json={"description": "수정"}).status_code == 200
    assert client.post(f"/api/unit-tasks/{task['id']}/files", files=FILE).status_code == 201
    with client.app.state.services.db() as db:
        row = db.get(HandoverSession, task["session_id"])
        row.document_md, row.document_version = DOC, 1
        db.commit()
    res = client.put(f"{s}/document", json={"markdown": DOC + "\n팀장 수정", "base_version": 1})
    assert res.status_code == 200 and res.json()["saved"] is True, res.text
    assert client.post(f"{s}/transfers", json={"recipient_id": team["recipient_id"]}).status_code == 201
    assert [t["name"] for t in client.get("/api/unit-tasks").json()["tasks"]] == ["IT아미고팀 운영 총괄"]
    client.post("/api/auth/logout")

    login(client, "hwan")  # 받은 사람은 받은 인수인계에서 본다
    assert [h["owner_name"] for h in client.get("/api/handovers/received").json()["handovers"]] == ["김미고"]


def test_member_still_writes(client, team):
    login(client, "demo")
    s = f"/api/sessions/{team['session_id']}"
    task_id = team["task_id"]
    assert client.post("/api/sessions", json={"owner_name": "김민준"}).status_code == 201
    assert client.post("/api/unit-tasks", json={"tasks": [{"name": "협력사 계약 관리"}]}).status_code == 201
    assert client.patch(f"/api/unit-tasks/{task_id}", json={"description": "수정"}).status_code == 200
    assert client.post(f"/api/unit-tasks/{task_id}/files", files=FILE).status_code == 201
    assert client.post(f"{s}/links", json={"urls": ["https://example.com/wiki"]}).status_code == 201
    res = client.put(f"{s}/document", json={"markdown": DOC + "\n수정", "base_version": 1})
    assert res.status_code == 200 and res.json()["saved"] is True, res.text
    assert client.post(f"{s}/transfers", json={"recipient_id": team["recipient_id"]}).status_code == 201
    assert client.delete(f"/api/unit-tasks/{task_id}").status_code == 200


def test_admin_sees_team_handovers_in_chat_and_detail(client, team):
    """팀장은 챗봇 질문 범위에서 팀원 인수인계(team)를 보고, 세션 상세의 viewer 로 팀원 문서인지 안다."""
    login(client, "amigoteamjang")
    scopes = client.get("/api/handover-chat/scopes").json()["scopes"]
    assert [(s["id"], s["kind"], s["owner_name"]) for s in scopes] == [(team["session_id"], "team", "김민준")]
    assert client.get(f"/api/sessions/{team['session_id']}").json()["viewer"] == "team"
    res = client.post("/api/handover-chat", json={"question": "개요 알려 줘", "scope_ids": [team["session_id"]]})
    assert res.status_code == 200, res.text
    client.post("/api/auth/logout")

    login(client, "demo")  # 팀원에게는 팀 범위가 없고, 자기 세션은 owner
    assert all(s["kind"] != "team" for s in client.get("/api/handover-chat/scopes").json()["scopes"])
    assert client.get(f"/api/sessions/{team['session_id']}").json()["viewer"] == "owner"
    assert client.post(f"/api/sessions/{team['session_id']}/transfers", json={"recipient_id": team["recipient_id"]}).status_code == 201
    client.post("/api/auth/logout")

    login(client, "hwan")  # 전송받은 사람은 recipient
    assert client.get(f"/api/sessions/{team['session_id']}").json()["viewer"] == "recipient"
