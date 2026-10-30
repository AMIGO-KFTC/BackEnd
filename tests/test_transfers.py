"""인수인계서 전송: 인계자가 인수자 이름으로 보내고, 인수자는 마이페이지 '인수받은 내용'에서 받은 산출물을 본다."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import HandoverSession
from app.services.documents import add_version
from app.services.exporters import strip_citation_marks


@pytest.fixture()
def mock_client(tmp_path):
    settings = Settings(data_dir=tmp_path / "real-data", mock_mode=True, mock_delay=0, workers=4)
    with TestClient(create_app(settings)) as c:
        yield c


def login(client, login_id, password="demo1234"):
    res = client.post("/api/auth/login", json={"login_id": login_id, "password": password})
    assert res.status_code == 200, res.text


def task_session(client, name) -> str:
    return next(t for t in client.get("/api/unit-tasks").json()["tasks"] if t["name"] == name)["session_id"]


def test_send_by_name_and_recipient_sees_it_in_received(mock_client):
    login(mock_client, "demo")
    sid = task_session(mock_client, "기관 홈페이지 운영")
    doc = mock_client.get(f"/api/sessions/{sid}/document").json()

    # 인수자 찾기(이름 일부) — 본인은 빠진다
    found = mock_client.get("/api/users/recipients?q=서연").json()["candidates"]
    assert [(c["name"], c["team"], c["position"]) for c in found] == [("이서연", "웹서비스팀", "대리")]
    assert all(c["login_id"] != "demo" for c in mock_client.get("/api/users/recipients?q=김").json()["candidates"])

    res = mock_client.post(f"/api/sessions/{sid}/transfers", json={"recipient_name": "이서연", "message": "SSL 갱신 일정 꼭 확인해 주세요."})
    body = res.json()
    assert res.status_code == 201 and body["message"] == f"인수인계서 v{doc['version']}을(를) 이서연님에게 보냈습니다."
    sent = body["transfer"]
    assert sent["recipient"]["name"] == "이서연" and sent["sender"]["name"] == "김민수" and sent["document_version"] == doc["version"]
    assert sent["is_read"] is False and sent["task_name"] == "기관 홈페이지 운영"
    assert mock_client.get(f"/api/sessions/{sid}/messages").json()[-1]["content"] == body["message"]  # 대화 기록에도 남는다
    assert mock_client.get(f"/api/sessions/{sid}").json()["successor"] == "이서연"  # 비어 있던 인수자 칸을 채운다

    # 보낸 뒤 '직접 수정하기'로 고쳐 저장하면 인수자에게도 고친 최신 버전이 보인다
    mock_client.put(f"/api/sessions/{sid}/document", json={"markdown": doc["markdown"] + "\n보낸 뒤 고침", "base_version": doc["version"]})
    assert mock_client.get("/api/me").json()["stats"]["sent_transfers"] == 1
    mock_client.post("/api/auth/logout")

    # 인수자: 마이페이지 → 인수받은 내용(인수 현황)
    login(mock_client, "seoyeon.lee")
    me = mock_client.get("/api/me").json()["stats"]
    assert (me["received_transfers"], me["received_unread"]) == (1, 1)
    inbox = mock_client.get("/api/me/received").json()
    assert inbox["total"] == 1 and inbox["unread_count"] == 1 and inbox["message"] == "인수받은 인수인계서 1건(새로 받은 것 1건)"
    item = inbox["transfers"][0]
    assert (item["sent_version"], item["document_version"], item["updated_since_sent"]) == (doc["version"], doc["version"] + 1, True)
    assert item["document_origin"] == "user" and item["document_origin_label"] == "직접 수정" and item["document_updated_at"]
    assert (item["sender"]["name"], item["sender"]["position"], item["message"]) == ("김민수", "과장", "SSL 갱신 일정 꼭 확인해 주세요.")

    detail = mock_client.get(f"/api/me/received/{item['id']}").json()
    assert detail["markdown"].endswith("보낸 뒤 고침") and detail["is_read"] is True
    sent_copy = mock_client.get(f"/api/me/received/{item['id']}?sent=true").json()  # 보낸 시점 문서도 볼 수 있다
    assert sent_copy["markdown"] == doc["markdown"]
    assert mock_client.get("/api/me/received").json()["unread_count"] == 0
    assert mock_client.get("/api/me/received?unread_only=true").json()["total"] == 0
    assert mock_client.get("/api/me/received?q=홈페이지").json()["total"] == 1
    assert mock_client.get("/api/me/received?q=없는업무").json()["message"] == "아직 인수받은 인수인계서가 없습니다."
    md = mock_client.get(f"/api/me/received/{item['id']}/download?format=md")
    assert md.status_code == 200 and md.content.decode().endswith("보낸 뒤 고침") and "v2.md" in md.headers["content-disposition"]
    assert mock_client.get(f"/api/me/received/{item['id']}/download?format=md&sent=true").content.decode() == strip_citation_marks(doc["markdown"])
    assert mock_client.get(f"/api/me/received/{item['id']}/download?format=docx").status_code == 200

    # 보내면 인수자로 연결되어 원래 세션 문서를 읽을 수는 있지만(받은 인수인계) 고칠 수는 없다
    assert mock_client.get(f"/api/sessions/{detail['session_id']}/document").status_code == 200
    assert mock_client.put(f"/api/sessions/{detail['session_id']}/document", json={"markdown": "x", "base_version": 1}).status_code == 404
    mock_client.post("/api/auth/logout")

    # 인계자는 읽음 여부를 본다 / 받는 사람이 아니면 받은 문서를 볼 수 없다
    login(mock_client, "demo")
    history = mock_client.get(f"/api/sessions/{sid}/transfers").json()["transfers"]
    assert history[0]["recipient"]["name"] == "이서연" and history[0]["is_read"] is True
    assert mock_client.get("/api/me/sent").json()["total"] == 1
    assert mock_client.get(f"/api/me/received/{item['id']}").status_code == 404


def test_demo_account_already_has_a_received_handover(mock_client):
    login(mock_client, "demo")
    inbox = mock_client.get("/api/me/received").json()
    assert inbox["total"] == 1 and inbox["unread_count"] == 1
    assert inbox["transfers"][0]["task_name"] == "모바일 웹 운영" and inbox["transfers"][0]["sender"]["name"] == "이서연"


def test_send_errors(mock_client):
    login(mock_client, "demo")
    done = task_session(mock_client, "기관 홈페이지 운영")
    setup = task_session(mock_client, "유지보수 계약 관리")
    no_doc = mock_client.post(f"/api/sessions/{setup}/transfers", json={"recipient_name": "이서연"})
    assert no_doc.status_code == 400 and "인수인계서가 없습니다" in no_doc.json()["message"]
    missing = mock_client.post(f"/api/sessions/{done}/transfers", json={"recipient_name": "홍길동"})
    assert missing.status_code == 404 and "홍길동" in missing.json()["message"]
    assert mock_client.post(f"/api/sessions/{done}/transfers", json={"recipient_name": "김민수"}).status_code == 400  # 본인
    assert mock_client.post(f"/api/sessions/{done}/transfers", json={"recipient_name": ""}).status_code == 400
    assert mock_client.post(f"/api/sessions/{done}/transfers", json={"recipient_id": 9999}).status_code == 404
    mock_client.post("/api/auth/logout")

    # 다른 사람의 인수인계서는 보낼 수 없다
    login(mock_client, "junho.park")
    assert mock_client.post(f"/api/sessions/{done}/transfers", json={"recipient_name": "이서연"}).status_code == 404
    mock_client.post("/api/auth/logout")
    assert mock_client.get("/api/me/received").status_code == 401


def test_same_name_asks_to_choose_and_sender_session_deleted(client):
    """동명이인은 후보를 돌려주고 recipient_id 로 보낸다. 인계자가 업무를 지워도 받은 문서는 남는다(일반 DB·PostgreSQL)."""

    def account(login_id, name, team):
        client.post("/api/auth/signup", json={"login_id": login_id, "password": "password-123", "name": name})
        login(client, login_id, "password-123")
        client.patch("/api/me", json={"department": "기획부", "team": team, "position": "대리"})

    account("lee.one", "이서연", "전략팀")
    account("lee.two", "이서연", "예산팀")
    account("giver", "박인계", "전략팀")
    task = client.post("/api/unit-tasks", json={"tasks": [{"name": "예산 편성"}]}).json()["tasks"][0]
    services = client.app.state.services
    with services.db() as db:
        row = db.get(HandoverSession, task["session_id"])
        add_version(db, row, "# 예산 편성 인수인계서", origin="ai", ai_version=1)
        db.commit()

    dup = client.post(f"/api/sessions/{task['session_id']}/transfers", json={"recipient_name": "이서연"})
    assert dup.status_code == 409 and dup.json()["message"] == "'이서연' 이름의 회원이 2명입니다. 인수자를 골라 주세요."
    candidates = dup.json()["detail"]["candidates"]
    assert sorted(c["team"] for c in candidates) == ["예산팀", "전략팀"]
    chosen = next(c for c in candidates if c["team"] == "예산팀")
    ok = client.post(f"/api/sessions/{task['session_id']}/transfers", json={"recipient_id": chosen["user_id"]})
    assert ok.status_code == 201 and ok.json()["transfer"]["recipient"]["login_id"] == "lee.two"

    assert client.delete(f"/api/unit-tasks/{task['id']}").status_code == 200
    login(client, "lee.two", "password-123")
    inbox = client.get("/api/me/received").json()["transfers"]
    assert len(inbox) == 1
    kept = client.get(f"/api/me/received/{inbox[0]['id']}").json()
    assert kept["markdown"] == "# 예산 편성 인수인계서" and kept["session_id"] is None
    login(client, "lee.one", "password-123")
    assert client.get("/api/me/received").json()["total"] == 0


def test_recipient_sees_each_new_edit(mock_client):
    """보낸 뒤 고친 문서: 인수자가 열어 본 뒤 또 고치면 '새 버전' 표시. 받은 인수인계(/api/handovers/received)에도 최신 버전이 뜬다."""
    login(mock_client, "demo")
    sid = task_session(mock_client, "기관 홈페이지 운영")
    doc = mock_client.get(f"/api/sessions/{sid}/document").json()
    mock_client.post(f"/api/sessions/{sid}/transfers", json={"recipient_name": "박준호"})
    assert mock_client.get(f"/api/sessions/{sid}").json()["successor_login_id"] == "junho.park"  # 받은 인수인계에도 연결
    mock_client.post("/api/auth/logout")

    login(mock_client, "junho.park")
    tid = mock_client.get("/api/me/received").json()["transfers"][0]["id"]
    first = mock_client.get(f"/api/me/received/{tid}").json()
    assert first["updated_since_sent"] is False and first["markdown"] == doc["markdown"] and first["document_origin"] == "ai"
    received = mock_client.get("/api/handovers/received").json()["handovers"]
    assert [(h["id"], h["document_version"], h["document_origin"]) for h in received] == [(sid, doc["version"], "ai")]
    # 인수자는 읽기만 할 수 있다
    assert mock_client.put(f"/api/sessions/{sid}/document", json={"markdown": "x", "base_version": doc["version"]}).status_code == 404
    mock_client.post("/api/auth/logout")

    login(mock_client, "demo")  # 인계자가 직접 고쳐 저장
    saved = mock_client.put(f"/api/sessions/{sid}/document", json={"markdown": doc["markdown"] + "\n담당자 연락처 추가", "base_version": doc["version"]}).json()
    assert saved["saved"] is True
    mock_client.post("/api/auth/logout")

    login(mock_client, "junho.park")
    item = mock_client.get("/api/me/received").json()["transfers"][0]
    assert item["updated_since_read"] is True and item["updated_since_sent"] is True and item["document_version"] == saved["document"]["version"]
    assert mock_client.get(f"/api/me/received/{tid}").json()["markdown"].endswith("담당자 연락처 추가")
    assert mock_client.get("/api/me/received").json()["transfers"][0]["updated_since_read"] is False  # 열어 보면 표시가 꺼진다
    latest = mock_client.get("/api/handovers/received").json()["handovers"][0]
    assert latest["document_version"] == saved["document"]["version"] and latest["document_origin"] == "user" and latest["document_origin_label"] == "직접 수정"
    assert mock_client.get(f"/api/sessions/{sid}/document").json()["markdown"].endswith("담당자 연락처 추가")  # 받은 인수인계 화면이 읽는 문서


def test_received_list_shows_the_message_the_sender_left(mock_client):
    """전송할 때 남긴 한마디가 받은 인수인계 목록(/api/handovers/received)에 내려온다. 남기지 않았으면 빈 값."""
    login(mock_client, "demo")
    sid = task_session(mock_client, "기관 홈페이지 운영")
    mock_client.post(f"/api/sessions/{sid}/transfers", json={"recipient_name": "박준호", "message": "  SSL 갱신 일정 꼭 확인해 주세요.  "})
    other = task_session(mock_client, "정보보호 점검") if any(t["name"] == "정보보호 점검" for t in mock_client.get("/api/unit-tasks").json()["tasks"]) else None
    if other:
        mock_client.post(f"/api/sessions/{other}/transfers", json={"recipient_name": "박준호"})
    mock_client.post("/api/auth/logout")

    login(mock_client, "junho.park")
    received = {h["id"]: h for h in mock_client.get("/api/handovers/received").json()["handovers"]}
    assert received[sid]["message"] == "SSL 갱신 일정 꼭 확인해 주세요." and received[sid]["sent_at"]
    if other:
        assert received[other]["message"] == ""
