"""화면에서 입력한 답변을 받아 AI 에 넘기는 과정: 저장 → 질문과 짝짓기 → 에이전트 전달 → 정리·다음 질문."""

import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def mock_client(tmp_path):
    settings = Settings(data_dir=tmp_path / "real-data", mock_mode=True, mock_delay=0, workers=4)
    with TestClient(create_app(settings)) as c:
        c.post("/api/auth/login", json={"login_id": "demo", "password": "demo1234"})
        yield c


def asking_session(client) -> str:
    return next(t for t in client.get("/api/unit-tasks").json()["tasks"] if t["name"] == "웹 접근성 관리")["session_id"]


def progress(client, sid) -> dict:
    return client.get(f"/api/conversations/{sid}").json()["progress"]


def wait_progress(client, sid, predicate, timeout=20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        p = progress(client, sid)
        if predicate(p):
            return p
        time.sleep(0.05)
    raise AssertionError(f"timeout: {p['status']} / {p['next_action']}")


def test_answer_is_saved_paired_with_question_and_passed_to_ai(mock_client):
    sid = asking_session(mock_client)
    question = progress(mock_client, sid)["current_question"]
    assert question["kind"] == "question" and question["gap_id"]

    # 화면에 띄운 질문(reply_to)에 대한 답변을 저장하고 202 로 바로 돌려준다
    res = mock_client.post(f"/api/sessions/{sid}/chat", json={"text": "정보보호팀 이수진 대리, 내선 3120", "reply_to": question["message_id"]})
    assert res.status_code == 202
    saved = res.json()["message"]
    assert saved["role"] == "user" and saved["kind"] == "answer" and saved["content"] == "정보보호팀 이수진 대리, 내선 3120"
    assert saved["meta"] == {"reply_to": question["message_id"], "question_kind": "question", "gap_id": question["gap_id"], "slot": question["slot"]}

    # AI 가 답변을 받아 정리하고 '이렇게 정리했는데 맞나요?' 로 확인한다
    confirm = wait_progress(mock_client, sid, lambda p: p["next_action"] == "confirm_answer")["current_question"]
    assert confirm["message_id"] > saved["id"] and "내선 3120" in confirm["content"]

    # 이미 답한 질문에 다시 답하면(다른 창, 늦게 누른 버튼) 엉뚱한 질문에 붙지 않게 막는다
    stale = mock_client.post(f"/api/sessions/{sid}/chat", json={"text": "늦은 답변", "reply_to": question["message_id"]})
    assert stale.status_code == 409 and stale.json()["message"] == "그사이 질문이 바뀌었어요. 새 질문을 확인한 뒤 다시 답해 주세요."
    assert mock_client.post(f"/api/sessions/{sid}/skip?reply_to={question['message_id']}").status_code == 409
    history = mock_client.get(f"/api/sessions/{sid}/messages").json()
    assert all(m["content"] != "늦은 답변" for m in history) and history[-1]["id"] == confirm["message_id"]

    # 확인 요청에 답하면 다음 질문으로 넘어간다. reply_to 없이 보내도(예전 화면) 지금 질문에 짝지어 저장된다
    ok = mock_client.post(f"/api/sessions/{sid}/chat", json={"text": "네, 맞아요"}).json()["message"]
    assert ok["meta"]["reply_to"] == confirm["message_id"] and ok["meta"]["question_kind"] == "confirm"
    nxt = wait_progress(mock_client, sid, lambda p: p["next_action"] == "answer_question" and p["current_question"]["number"] == 2)
    assert nxt["questions"]["answered"] == 1

    # 건너뛰기도 어떤 질문을 건너뛰었는지 남긴다
    skipped = mock_client.post(f"/api/sessions/{sid}/skip?reply_to={nxt['current_question']['message_id']}")
    assert skipped.status_code == 202 and skipped.json()["message"]["meta"]["reply_to"] == nxt["current_question"]["message_id"]


def test_answer_is_refused_when_ai_is_not_waiting(mock_client):
    sid = asking_session(mock_client)
    services = mock_client.app.state.services
    agent = services.agent
    original = agent.is_waiting
    agent.is_waiting = lambda _sid: False
    try:
        res = mock_client.post(f"/api/sessions/{sid}/chat", json={"text": "답변"})
        assert res.status_code == 409 and "준비" in res.json()["message"]
        assert mock_client.post(f"/api/sessions/{sid}/generate").status_code == 409
        with services.db() as db:  # 오류로 멈춘 세션이면 '다시 시도' 안내
            from app.models import HandoverSession

            db.get(HandoverSession, sid).status = "error"
            db.commit()
        assert "다시 시도" in mock_client.post(f"/api/sessions/{sid}/chat", json={"text": "답변"}).json()["message"]
    finally:
        agent.is_waiting = original
    assert all(m["content"] != "답변" for m in mock_client.get(f"/api/sessions/{sid}/messages").json())

    # 자료 등록 전(분석 전) 세션은 답변을 받지 않는다 / 빈 답변은 422
    setup = next(t for t in mock_client.get("/api/unit-tasks").json()["tasks"] if t["name"] == "유지보수 계약 관리")["session_id"]
    assert mock_client.post(f"/api/sessions/{setup}/chat", json={"text": "답변"}).status_code == 400
    assert mock_client.post(f"/api/sessions/{sid}/chat", json={"text": ""}).status_code == 422
