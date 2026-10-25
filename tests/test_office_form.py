"""팀장(관리자)이 만든 인수인계 세션은 원내 양식(form=office)으로 만든다. 팀원·로그인 없이 만든 세션은 기본 양식(standard)."""

import time

from sqlalchemy import select

from app.models import HandoverSession, User

from conftest import PROFILE
from test_handovers_admin import TEAM_IT, signup


def create_task(client, name: str = "AMIGO 서비스 운영") -> str:
    res = client.post("/api/unit-tasks", json={"tasks": [{"name": name, "description": "배포·장애 대응"}]})
    assert res.status_code == 201, res.text
    return res.json()["tasks"][0]["session_id"]


def test_admin_session_uses_office_form(client):
    signup(client, "amigoteamjang", "김미고", TEAM_IT, "팀장", role="admin")
    session_id = create_task(client)

    assert client.get(f"/api/sessions/{session_id}").json()["form"] == "office"
    with client.app.state.services.db() as db:
        row = db.get(HandoverSession, session_id)
        assert row.form == "office" and row.profile()["form"] == "office"

        # 나중에 관리자 권한이 바뀌어도 이미 만든 세션의 양식은 그대로다
        db.scalar(select(User).where(User.login_id == "amigoteamjang")).is_admin = False
        db.commit()
        assert db.get(HandoverSession, session_id).profile()["form"] == "office"


def test_member_session_uses_standard_form(client):
    signup(client, "hwan", "최환", TEAM_IT, "계장")
    session_id = create_task(client)

    assert client.get(f"/api/sessions/{session_id}").json()["form"] == "standard"
    with client.app.state.services.db() as db:
        assert db.get(HandoverSession, session_id).profile()["form"] != "office"


def test_anonymous_session_uses_standard_form(client):
    res = client.post("/api/sessions", json=PROFILE, headers={"X-User-Id": "browser-1"})
    assert res.status_code == 201, res.text
    assert res.json()["form"] == "standard"


def test_form_reaches_agent_profile(client, monkeypatch):
    """분석 시작 때 넘기는 인계자 정보와 매 턴 갱신(update_profile)에 양식이 들어간다."""
    signup(client, "amigoteamjang", "김미고", TEAM_IT, "팀장", role="admin")
    session_id = create_task(client)
    services = client.app.state.services
    agent = services.agent
    started: list[dict] = []
    patches: list[dict] = []
    original_start = agent.start

    def recording_start(thread_id, profile, kb, **kwargs):
        started.append(dict(profile))
        return original_start(thread_id, profile, kb, **kwargs)

    monkeypatch.setattr(agent, "start", recording_start)
    monkeypatch.setattr(agent, "update_profile", lambda thread_id, patch: patches.append(dict(patch)) or True)

    services.runner.start_analysis(session_id)
    deadline = time.time() + 30
    while not started and time.time() < deadline:
        time.sleep(0.05)
    assert started and started[0]["form"] == "office"

    services.runner._refresh_agent_profile(session_id)
    assert patches and patches[-1]["form"] == "office"
