"""대화 진행 상태·기록을 DB 에 저장하고 불러오기(서버 재시작 후 이어서 진행 포함)."""

import json
import sqlite3
import time
from pathlib import Path

from conftest import idle, wait_for
from fastapi.testclient import TestClient
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy import func, select

from app.main import create_app
from app.models import AgentCheckpoint, HandoverSession, Source
from app.services.checkpointer import DBCheckpointSaver, import_sqlite_checkpoints

ACCOUNT = {"login_id": "minsu", "password": "pass-1234", "name": "김민수"}
PROFILE = {"department": "디지털전략부", "team": "웹서비스팀", "position": "과장", "name": "김민수"}


def login(client, account=ACCOUNT) -> dict:
    client.post("/api/auth/signup", json=account)
    res = client.post("/api/auth/login", json={"login_id": account["login_id"], "password": account["password"]})
    return {"Authorization": f"Bearer {res.json()['token']}"}


def start_task(client, sample_dir, name="홈페이지 운영") -> dict:
    client.put("/api/profile", json=PROFILE)
    task = client.post("/api/unit-tasks", json={"tasks": [{"name": name}]}).json()["tasks"][0]
    pdf = sample_dir / "업무정의서_웹서비스팀.pdf"
    client.post(f"/api/unit-tasks/{task['id']}/files", files=[("files", (pdf.name, pdf.read_bytes(), "application/pdf"))])
    return task


def progress(client, sid) -> dict:
    return client.get(f"/api/conversations/{sid}").json()["progress"]


def wait_progress(client, sid, predicate, timeout: float = 60.0) -> dict:
    """진행 상태가 조건을 만족할 때까지 기다린다(백그라운드 작업이 이어서 시작될 수 있는 경우)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        p = progress(client, sid)
        if predicate(p):
            return p
        time.sleep(0.1)
    raise AssertionError(f"timeout: {p['status']} / {p['next_action']}")


def test_progress_and_history_follow_the_conversation(client, sample_dir):
    login(client)
    client.put("/api/profile", json=PROFILE)
    task = client.post("/api/unit-tasks", json={"tasks": [{"name": "홈페이지 운영"}]}).json()["tasks"][0]
    sid = task["session_id"]

    p = progress(client, sid)
    assert p["task_id"] == task["id"] and p["stage"] == "setup" and p["stage_number"] == 0
    assert p["next_action"] == "upload_files" and p["next_action_label"] == "업무 자료를 올려 주세요."
    assert p["message_count"] == 1 and p["unread_count"] == 1 and p["current_question"] is None

    pdf = sample_dir / "업무정의서_웹서비스팀.pdf"
    client.post(f"/api/unit-tasks/{task['id']}/files", files=[("files", (pdf.name, pdf.read_bytes(), "application/pdf"))])
    wait_for(client, sid, lambda s: idle(s) and s["status"] == "waiting")

    # AI 가 첫 질문을 던지고 답을 기다리는 상태
    body = client.get(f"/api/conversations/{sid}").json()
    p = body["progress"]
    assert p["stage"] == "qna" and p["stage_number"] == 3 and p["stage_label"] == "질의응답"
    assert p["next_action"] == "answer_question"
    question = p["current_question"]
    assert question["kind"] == "question" and question["number"] == 1 and question["gap_id"] and question["quick_replies"]
    assert p["questions"]["asked"] == 1 and p["questions"]["total"] >= p["questions"]["remaining"] + 1
    assert p["file_count"] == 1 and p["unread_count"] == p["message_count"]
    history = body["history"]["messages"]
    assert history[-1]["id"] == question["message_id"] == p["last_message"]["id"]
    assert [m["id"] for m in history] == sorted(m["id"] for m in history)

    # 읽음 위치 저장: 뒤로 돌아가지 않고, 없는 ID 는 마지막 메시지로 맞춘다
    read = client.put(f"/api/conversations/{sid}/read", json={"message_id": question["message_id"]}).json()
    assert read["success"] and read["progress"]["unread_count"] == 0 and read["progress"]["last_read_message_id"] == question["message_id"]
    assert client.put(f"/api/conversations/{sid}/read", json={"message_id": 1}).json()["progress"]["last_read_message_id"] == question["message_id"]
    assert client.put(f"/api/conversations/{sid}/read", json={"message_id": 10**9}).json()["progress"]["last_read_message_id"] == question["message_id"]

    # 답변 → AI 가 정리한 내용 확인 요청
    client.post(f"/api/sessions/{sid}/chat", json={"text": "재무팀 박지훈 차장, 내선 2345"})
    wait_for(client, sid, lambda s: s["status"] == "waiting")
    p = progress(client, sid)
    assert p["next_action"] == "confirm_answer" and p["current_question"]["kind"] == "confirm"
    assert p["unread_count"] == 1  # 내 답변은 안 읽은 메시지로 세지 않는다
    assert p["questions"]["answered"] >= 1

    client.post(f"/api/sessions/{sid}/chat", json={"text": "네"})
    wait_for(client, sid, lambda s: s["status"] == "waiting")
    assert progress(client, sid)["current_question"]["number"] == 2

    # 기록 페이지: 최근 3개 → 그 이전 → ... 처음까지
    page = client.get(f"/api/conversations/{sid}/messages", params={"limit": 3}).json()
    assert len(page["messages"]) == 3 and page["has_more_before"] is True
    seen = page["messages"]
    while page["has_more_before"]:
        page = client.get(f"/api/conversations/{sid}/messages", params={"limit": 3, "before": seen[0]["id"]}).json()
        seen = page["messages"] + seen
    all_messages = client.get(f"/api/sessions/{sid}/messages").json()
    assert [m["id"] for m in seen] == [m["id"] for m in all_messages]

    # 문서 생성 뒤에는 검토 단계
    client.post(f"/api/sessions/{sid}/generate")
    wait_for(client, sid, lambda s: s["status"] == "waiting" and s["document_version"] >= 1)
    p = progress(client, sid)
    assert p["next_action"] == "review_document" and p["stage_number"] == 4 and p["document_version"] == 1

    listed = client.get("/api/conversations").json()["conversations"]
    assert [c["session_id"] for c in listed] == [sid]


def test_conversations_are_listed_by_recent_activity_and_private(client, sample_dir):
    login(client)
    client.put("/api/profile", json=PROFILE)
    tasks = client.post("/api/unit-tasks", json={"tasks": [{"name": "A 업무"}, {"name": "B 업무"}]}).json()["tasks"]
    pdf = sample_dir / "업무정의서_웹서비스팀.pdf"
    client.post(f"/api/unit-tasks/{tasks[0]['id']}/files", files=[("files", (pdf.name, pdf.read_bytes(), "application/pdf"))])
    wait_for(client, tasks[0]["session_id"], lambda s: idle(s) and s["status"] == "waiting")
    listed = client.get("/api/conversations").json()["conversations"]
    assert [c["title"] for c in listed] == ["A 업무 인수인계", "B 업무 인수인계"]
    assert [c["next_action"] for c in listed] == ["answer_question", "upload_files"]

    client.cookies.clear()
    assert client.get("/api/conversations").status_code == 401
    other = login(client, {"login_id": "other", "password": "other-pass", "name": "이서연"})
    assert client.get("/api/conversations", headers=other).json()["conversations"] == []
    for path in ("", "/messages"):
        assert client.get(f"/api/conversations/{tasks[0]['session_id']}{path}", headers=other).status_code == 404
    assert client.put(f"/api/conversations/{tasks[0]['session_id']}/read", json={"message_id": 1}, headers=other).status_code == 404


def test_conversation_resumes_after_server_restart(settings, sample_dir):
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        task = start_task(client, sample_dir)
        sid = task["session_id"]
        wait_for(client, sid, lambda s: idle(s) and s["status"] == "waiting")
        before = progress(client, sid)
        assert before["current_question"]["number"] == 1

    # 에이전트 상태가 별도 파일이 아니라 같은 DB 에 저장돼 있다
    assert not settings.checkpoint_path.exists()

    with TestClient(create_app(settings), headers=headers) as client:  # 새 서버 프로세스, 로그인 세션도 DB 에 있어 그대로 유효
        services = client.app.state.services
        with services.db() as db:
            assert db.scalar(select(func.count()).select_from(AgentCheckpoint).where(AgentCheckpoint.thread_id == sid)) > 0
        after = client.get(f"/api/conversations/{sid}").json()
        assert after["progress"]["current_question"] == before["current_question"]
        assert after["progress"]["message_count"] == before["message_count"]

        # 멈춘 질문에 답하면 그 자리부터 이어서 진행된다
        res = client.post(f"/api/sessions/{sid}/chat", json={"text": "재무팀 박지훈 차장, 내선 2345"})
        assert res.status_code == 202
        wait_for(client, sid, lambda s: s["status"] == "waiting")
        p = client.get(f"/api/conversations/{sid}").json()["progress"]
        assert p["next_action"] == "confirm_answer" and "내선 2345" in p["current_question"]["content"]


def test_restart_recovers_interrupted_work(settings, sample_dir):
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        task = start_task(client, sample_dir)
        sid = task["session_id"]
        wait_for(client, sid, lambda s: idle(s) and s["status"] == "waiting")
        services = client.app.state.services
        # 서버가 AI 작업·자료 적재 도중 꺼진 상황을 흉내 낸다(디스크의 파싱용 사본도 사라짐)
        with services.db() as db:
            row = db.get(HandoverSession, sid)
            row.status = "running"
            source = db.scalars(select(Source).where(Source.session_id == sid)).one()
            source.status = "processing"
            stored = source.stored_path
            db.commit()
    Path(stored).unlink()

    with TestClient(create_app(settings), headers=headers) as client:
        state = wait_for(client, sid, idle)
        assert state["session"]["status"] == "error" and state["session"]["sources"][0]["status"] == "ready"
        assert Path(stored).read_bytes() == (sample_dir / "업무정의서_웹서비스팀.pdf").read_bytes()  # DB 원본으로 복원
        p = client.get(f"/api/conversations/{sid}").json()["progress"]
        assert p["next_action"] == "retry" and "다시 시도" in p["next_action_label"]

        assert client.post(f"/api/sessions/{sid}/retry").status_code == 202
        # 재시도가 끝나면 다시 적재된 자료가 새 자료로 반영되고(AI 작업이 한 번 더 돈다), 대화는 질문을 기다리는 상태로 돌아온다
        p = wait_progress(
            client,
            sid,
            lambda p: p["status"] == "waiting" and p["next_action"] == "answer_question" and "새 자료" in p["last_message"]["content"],
        )
        assert p["current_question"] is not None and p["questions"]["asked"] >= 1


def test_db_checkpointer_roundtrip_and_legacy_import(client, tmp_path):
    services = client.app.state.services
    saver = DBCheckpointSaver(services.SessionLocal)

    # 예전 버전처럼 SQLite 파일에 저장된 체크포인트
    legacy = tmp_path / "agent_checkpoints.sqlite"
    conn = sqlite3.connect(str(legacy), check_same_thread=False)
    old = SqliteSaver(conn)
    config = {"configurable": {"thread_id": "t-legacy", "checkpoint_ns": ""}}
    first = old.put(config, empty_checkpoint(), {"source": "input", "step": -1}, {})
    cp = empty_checkpoint()
    cp["channel_values"] = {"stage": "qna", "question_count": 3}
    second = old.put(first, cp, {"source": "loop", "step": 2, "질문": "재무팀 연락처"}, {})
    old.put_writes(second, [("stage", "review"), ("__interrupt__", {"value": "대기"})], task_id="task-1")
    conn.close()

    assert import_sqlite_checkpoints(legacy, services.SessionLocal) == 2
    assert not legacy.exists() and (tmp_path / "agent_checkpoints.sqlite.imported").exists()
    assert import_sqlite_checkpoints(legacy, services.SessionLocal) == 0  # 한 번만

    latest = saver.get_tuple({"configurable": {"thread_id": "t-legacy"}})
    assert latest.checkpoint["channel_values"] == {"stage": "qna", "question_count": 3}
    assert latest.metadata["질문"] == "재무팀 연락처" and latest.parent_config["configurable"]["checkpoint_id"] == first["configurable"]["checkpoint_id"]
    assert {(c, v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)) for _, c, v in latest.pending_writes} == {
        ("stage", "review"),
        ("__interrupt__", '{"value": "대기"}'),
    }

    # 새 체크포인트 저장·목록·필터·삭제
    cp3 = empty_checkpoint()
    third = saver.put(second, cp3, {"source": "loop", "step": 3}, {})
    assert saver.get_tuple({"configurable": {"thread_id": "t-legacy"}}).config == third
    listed = list(saver.list({"configurable": {"thread_id": "t-legacy"}}))
    assert [t.metadata["step"] for t in listed] == [3, 2, -1]
    assert [t.metadata["step"] for t in saver.list({"configurable": {"thread_id": "t-legacy"}}, filter={"source": "loop"}, limit=1)] == [3]
    assert [t.metadata["step"] for t in saver.list({"configurable": {"thread_id": "t-legacy"}}, before=third)] == [2, -1]

    saver.put_writes(third, [("stage", "first")], task_id="t")
    saver.put_writes(third, [("stage", "second")], task_id="t")  # 일반 채널은 처음 값 유지
    assert [v for _, c, v in saver.get_tuple(third).pending_writes] == ["first"]

    saver.delete_thread("t-legacy")
    assert saver.get_tuple({"configurable": {"thread_id": "t-legacy"}}) is None
