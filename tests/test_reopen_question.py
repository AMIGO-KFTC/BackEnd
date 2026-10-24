"""이전 질문으로 돌아가 다시 답하기: POST /api/sessions/{id}/reopen (가상 응답 모드의 에이전트로 흐름을 확인한다)."""

from test_answers import asking_session, mock_client, progress, wait_progress  # noqa: F401  (mock_client 는 fixture)


def answer_first_question(client, sid):
    first = progress(client, sid)["current_question"]
    client.post(f"/api/sessions/{sid}/chat", json={"text": "정보보호팀 이수진 대리, 내선 3120", "reply_to": first["message_id"]})
    confirm = wait_progress(client, sid, lambda p: p["next_action"] == "confirm_answer")["current_question"]
    client.post(f"/api/sessions/{sid}/chat", json={"text": "네, 맞아요", "reply_to": confirm["message_id"]})
    second = wait_progress(client, sid, lambda p: p["next_action"] == "answer_question" and p["current_question"]["number"] == 2)["current_question"]
    return first, second


def test_reopen_returns_to_the_earlier_question_and_overwrites_the_answer(mock_client):
    sid = asking_session(mock_client)
    first, second = answer_first_question(mock_client, sid)

    res = mock_client.post(f"/api/sessions/{sid}/reopen", json={"gap_id": first["gap_id"]})
    assert res.status_code == 202 and res.json()["message"]["role"] == "user"

    again = wait_progress(mock_client, sid, lambda p: p["next_action"] == "answer_question" and p["current_question"]["gap_id"] == first["gap_id"])["current_question"]
    assert again["message_id"] > second["message_id"] and again["number"] == 1
    assert "고르신 질문으로 이동했어요" in again["content"]

    # 새로 답하면 앞의 답을 대신하고, 확인하면 원래 이어지던 질문이 다시 나온다
    mock_client.post(f"/api/sessions/{sid}/chat", json={"text": "정보보호팀 박지훈 과장, 내선 4455", "reply_to": again["message_id"]})
    confirm = wait_progress(mock_client, sid, lambda p: p["next_action"] == "confirm_answer")["current_question"]
    assert "내선 4455" in confirm["content"]
    mock_client.post(f"/api/sessions/{sid}/chat", json={"text": "네, 맞아요", "reply_to": confirm["message_id"]})
    nxt = wait_progress(mock_client, sid, lambda p: p["next_action"] == "answer_question" and p["current_question"]["gap_id"] == second["gap_id"])
    assert nxt["current_question"]["number"] == 2


def test_reopen_rejects_questions_that_cannot_be_reopened(mock_client):
    sid = asking_session(mock_client)
    first, second = answer_first_question(mock_client, sid)

    now_asking = mock_client.post(f"/api/sessions/{sid}/reopen", json={"gap_id": second["gap_id"]})  # 지금 묻고 있는 질문
    assert now_asking.status_code == 409 and "지금 묻고 있는" in now_asking.json()["message"]
    assert mock_client.post(f"/api/sessions/{sid}/reopen", json={"gap_id": "no-such-gap"}).status_code == 404
    assert mock_client.post(f"/api/sessions/{sid}/reopen", json={"gap_id": ""}).status_code == 422
    assert progress(mock_client, sid)["current_question"]["gap_id"] == second["gap_id"]  # 거절된 요청은 아무것도 바꾸지 않는다


def test_reopen_is_refused_before_analysis(mock_client):
    setup = next(t for t in mock_client.get("/api/unit-tasks").json()["tasks"] if t["name"] == "유지보수 계약 관리")["session_id"]
    assert mock_client.post(f"/api/sessions/{setup}/reopen", json={"gap_id": "x"}).status_code == 400
