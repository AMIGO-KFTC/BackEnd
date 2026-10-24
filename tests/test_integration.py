"""RAG · AI 연동 통합 시나리오(실제 RAG 파싱·검색 + AI 에이전트 오프라인 엔진).

로그인 → 프로필 → 단위업무(인계 유형) → 업무파일 업로드 → 자동 분석 → 질의응답 → 문서 → 근거 원문 → 재적재 → 상태 점검.
"""

import re
import time

from conftest import idle, wait_for
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ACCOUNT = {"login_id": "minsu", "password": "pass-1234", "name": "김민수"}
PROFILE = {"department": "디지털전략부", "team": "웹서비스팀", "position": "과장", "name": "김민수"}
SAMPLES = ["업무정의서_웹서비스팀.pdf", "홈페이지_운영매뉴얼.hwpx", "주간회의록_2026-09-22.docx", "시스템_계정목록.xlsx", "재무팀_유지보수대금_요청.eml"]


def login(client) -> None:
    client.post("/api/auth/signup", json=ACCOUNT)
    assert client.post("/api/auth/login", json={"login_id": ACCOUNT["login_id"], "password": ACCOUNT["password"]}).status_code == 200
    client.put("/api/profile", json=PROFILE)


def upload(client, task_id, sample_dir, names=SAMPLES):
    files = [("files", (n, (sample_dir / n).read_bytes(), "application/octet-stream")) for n in names]
    res = client.post(f"/api/unit-tasks/{task_id}/files", files=files)
    assert res.status_code == 201, res.text
    return res.json()


def wait_question(client, sid, timeout=60.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        p = client.get(f"/api/conversations/{sid}").json()["progress"]
        if p["status"] == "waiting" and p["next_action"] in ("answer_question", "confirm_answer", "review_document"):
            return p
        assert p["status"] != "error", p
        time.sleep(0.1)
    raise AssertionError("timeout waiting for AI")


def test_unit_task_flow_with_real_rag_and_agent(client, sample_dir):
    login(client)
    created = client.post("/api/unit-tasks", json={"tasks": [
        {"name": "기관 홈페이지 운영", "description": "콘텐츠 게시·승인, 유지보수 업체 관리", "successor": "이서연 대리", "handover_date": "2026-10-30"},
        {"name": "웹 접근성 관리", "mode": "leave"},
    ]}).json()["tasks"]
    full, light = created
    assert (full["mode"], full["successor"], full["handover_date"]) == ("reassignment", "이서연 대리", "2026-10-30")
    assert light["mode"] == "leave"
    bad = client.post("/api/unit-tasks", json={"tasks": [{"name": "x", "mode": "vacation"}]})
    assert bad.status_code == 422
    assert client.post("/api/unit-tasks", json={"tasks": [{"name": "y", "handover_date": "10월 30일"}]}).status_code == 422

    # 인사발령: 6개 장 전체 분석 → 질문
    sid = full["session_id"]
    upload(client, full["id"], sample_dir)
    assert wait_question(client, sid)["next_action"] == "answer_question"
    session = client.get(f"/api/sessions/{sid}").json()
    assert set(session["slots"]) == {"overview", "regular", "partners", "irregular", "stakeholders", "systems", "dept_notes"}
    assert all(s["status"] == "ready" and s["chunk_count"] > 0 for s in session["sources"])

    # 장기휴가: 3개 장만(세부 업무·시스템 접근 권한·주요 연락망)
    upload(client, light["id"], sample_dir, SAMPLES[:2])
    wait_question(client, light["session_id"])
    assert set(client.get(f"/api/sessions/{light['session_id']}").json()["slots"]) == {"leave_tasks", "systems", "contacts"}

    # 질의응답 → 문서
    # 질문 순서는 자료에 따라 달라지므로, 앞의 질문들에 차례로 답한다(협업관계처럼 근거 번호가 없는 장과 시스템·정기업무 장이 섞여 있다)
    for _ in range(6):
        if wait_question(client, sid)["next_action"] != "answer_question":
            break
        client.post(f"/api/sessions/{sid}/chat", json={"text": "홈페이지 리뉴얼 건이고 신청은 내선 2345 로 연락합니다"})
        wait_question(client, sid)
        client.post(f"/api/sessions/{sid}/chat", json={"text": "네, 맞아요"})
        wait_question(client, sid)
    client.post(f"/api/sessions/{sid}/generate")
    wait_for(client, sid, lambda s: s["status"] == "waiting" and s["document_version"] >= 1)
    markdown = client.get(f"/api/sessions/{sid}/document").json()["markdown"]
    for row in ("| 인수자 | 이서연 대리 |", "| 인계 일자 | 2026-10-30 |", "| 인계 유형 | 인사발령 |", "| 단위 업무명 | 기관 홈페이지 운영 |"):
        assert row in markdown

    # 월별 일정표 막대를 고치면 실제 에이전트 상태(계획기간)에 확정되고, 검토 의견으로 다시 만들어도 유지된다
    import re

    name = re.search(r"<tr><td>([^<]+)</td>(?:<td[^>]*>[^<]*</td>)+<td>[^<]*</td></tr>", markdown.split("## 1.")[1]).group(1)
    edited = client.put(f"/api/sessions/{sid}/document/gantt", json={"name": name, "months": [4, 5]})
    assert edited.status_code == 200, (name, edited.text)
    row = re.search(rf"<tr><td>{re.escape(name)}</td>(.*?)<td>[^<]*</td></tr>", edited.json()["markdown"]).group(1)
    assert row == '<td></td><td></td><td></td><td class="bar" bgcolor="#7055C8" colspan="2">&nbsp;</td>' + "<td></td>" * 7
    assert client.get(f"/api/sessions/{sid}/document").json()["markdown"] == edited.json()["markdown"]
    client.post(f"/api/sessions/{sid}/chat", json={"text": "외주 업체 계약은 매년 12월에 갱신합니다"})
    wait_for(client, sid, lambda s: s["status"] == "waiting" and s["document_version"] >= 2)
    assert row in client.get(f"/api/sessions/{sid}/document").json()["markdown"]
    markdown = client.get(f"/api/sessions/{sid}/document").json()["markdown"]

    # 근거 원문(화면의 '출처' 팝업): 문서 본문에는 근거 장·번호가 없고, 이 API 가 번호별 출처와 RAG 조각 원문을 돌려준다
    cites = client.get(f"/api/sessions/{sid}/citations").json()
    assert cites["document_version"] == 2
    numbered = [(c["number"], c["label"]) for c in cites["citations"]]
    assert [n for n, _ in numbered] == list(range(1, len(numbered) + 1)) and "근거 자료" not in markdown
    marks = {int(n) for n in re.findall(r" \[(\d+)\]", markdown)}
    assert marks and marks <= {n for n, _ in numbered}  # 본문 글 뒤의 번호는 모두 이 목록에 있다
    from_docs = [c for c in cites["citations"] if c["kind"] == "document"]
    answers = [c for c in cites["citations"] if c["kind"] == "answer"]
    assert from_docs and all(c["source_name"] in SAMPLES and c["chunk_id"] and len(c["text"]) > len(c["quote"]) for c in from_docs)
    assert answers and any("내선 2345" in c["text"] for c in answers)

    client.patch(f"/api/unit-tasks/{full['id']}", json={"mode": "leave"})  # 문서는 6개 장으로 작성된 상태
    assert [(c["number"], c["label"]) for c in client.get(f"/api/sessions/{sid}/citations").json()["citations"]] == numbered
    client.patch(f"/api/unit-tasks/{full['id']}", json={"mode": "reassignment"})

    pdf = next(s for s in session["sources"] if s["name"].endswith(".pdf"))
    chunks = client.get(f"/api/sessions/{sid}/sources/{pdf['id']}/chunks", params={"limit": 2}).json()
    assert chunks["total"] == pdf["chunk_count"] and len(chunks["chunks"]) == 2
    assert chunks["chunks"][0]["citation"].startswith("업무정의서_웹서비스팀.pdf") and chunks["chunks"][0]["page"] == 1
    assert client.get(f"/api/sessions/{sid}/sources/nope/chunks").status_code == 404

    # 재적재: 지식베이스가 비어도(서버 이전·임베딩 변경) 원본으로 다시 만들고, 대화에는 새 메시지를 남기지 않는다
    kb = client.app.state.services.kb(sid)
    before_messages = len(client.get(f"/api/sessions/{sid}/messages").json())
    for s in session["sources"]:
        kb.delete_source(s["id"])
    assert kb.get_chunks() == [] or all(c.metadata.get("source_type") == "chat" for c in kb.get_chunks())
    res = client.post(f"/api/sessions/{sid}/reindex")
    assert res.status_code == 202 and res.json()["source_count"] == len(SAMPLES)
    wait_for(client, sid, idle)
    assert {c.metadata["source_id"] for c in kb.get_chunks()} >= {s["id"] for s in session["sources"]}
    assert len(client.get(f"/api/sessions/{sid}/messages").json()) == before_messages
    assert client.post(f"/api/sessions/{light['session_id']}/reindex").status_code == 202

    # 다른 계정은 근거·재적재에 접근할 수 없다
    client.cookies.clear()
    client.post("/api/auth/signup", json={"login_id": "other", "password": "other-pass-12", "name": "이서연"})
    client.post("/api/auth/login", json={"login_id": "other", "password": "other-pass-12"})
    assert client.get(f"/api/sessions/{sid}/citations").status_code == 404
    assert client.post(f"/api/sessions/{sid}/reindex").status_code == 404


def test_mode_change_and_reindex_guards(client, sample_dir):
    login(client)
    task = client.post("/api/unit-tasks", json={"tasks": [{"name": "보도자료 게시"}]}).json()["tasks"][0]
    sid = task["session_id"]
    assert client.post(f"/api/sessions/{sid}/reindex").status_code == 400  # 다시 적재할 자료 없음
    patched = client.patch(f"/api/unit-tasks/{task['id']}", json={"mode": "leave", "successor": "박지훈"}).json()
    assert patched["task"]["mode"] == "leave" and patched["task"]["successor"] == "박지훈" and "다시 분석" not in patched["message"]

    upload(client, task["id"], sample_dir, SAMPLES[:1])
    wait_question(client, sid)
    patched = client.patch(f"/api/unit-tasks/{task['id']}", json={"mode": "reassignment"}).json()
    assert "다시 분석" in patched["message"]  # 분석 뒤 유형을 바꾸면 안내
    assert client.get(f"/api/sessions/{sid}").json()["mode"] == "reassignment"


def test_startup_reindexes_missing_knowledge_base(settings, sample_dir):
    with TestClient(create_app(settings)) as client:
        login(client)
        task = client.post("/api/unit-tasks", json={"tasks": [{"name": "홈페이지 운영"}]}).json()["tasks"][0]
        sid = task["session_id"]
        upload(client, task["id"], sample_dir, SAMPLES[:2])
        wait_question(client, sid)
        client.app.state.services.kb(sid).clear()  # 서버를 옮기며 data/rag 가 비었다고 가정

    with TestClient(create_app(settings)) as client:
        login(client)
        kb = client.app.state.services.kb(sid)
        deadline = time.time() + 30
        while time.time() < deadline and len({c.metadata.get("source_id") for c in kb.get_chunks()}) < 2:
            time.sleep(0.1)
        assert len({c.metadata.get("source_id") for c in kb.get_chunks()}) == 2
        wait_for(client, sid, idle)
        assert all(s["status"] == "ready" for s in client.get(f"/api/sessions/{sid}").json()["sources"])
        col = kb.store.collection(sid, create=False)  # 임베딩 설정이 바뀐 지식베이스로 만든다
        col.modify(metadata={**(col.metadata or {}), "embedder": "old-embedder-v0"})

    with TestClient(create_app(settings)) as client:  # 다음 시작 때 처음부터 다시 만든다
        login(client)
        services = client.app.state.services
        deadline = time.time() + 30
        while time.time() < deadline and not (services.kb_state(sid)["state"] == "ok" and len(services.kb_state(sid)["source_ids"]) == 2):
            time.sleep(0.1)
        assert services.kb_state(sid)["state"] == "ok" and len(services.kb_state(sid)["source_ids"]) == 2
        wait_for(client, sid, idle)


def test_health_reports_integration_status(client, tmp_path):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["engine"] == "offline" and body["mock"] is False
    checks = body["checks"]
    assert checks == {
        "db": {"ok": True},
        "rag": {"ok": True, "mock": False, "embedding": "hash"},
        "ai": {"ok": True, "engine": "offline", "model": None, "budget_exhausted": False},
    }
    assert not list(client.app.state.services.settings.rag_dir.glob(".probe-*"))  # 점검하면서 파일을 쓰지 않는다

    # 지식베이스 폴더에 쓸 수 없으면 degraded
    blocked = tmp_path / "blocked"
    blocked.write_text("파일이라 폴더를 만들 수 없음")
    services = client.app.state.services
    original = services.settings
    services.settings = original.model_copy(update={"data_dir": blocked})
    try:
        body = client.get("/api/health").json()
        assert body["status"] == "degraded" and body["checks"]["rag"]["ok"] is False
    finally:
        services.settings = original


def test_citations_work_in_mock_mode(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path / "d", mock_mode=True, mock_delay=0))) as c:
        c.post("/api/auth/login", json={"login_id": "demo", "password": "demo1234"})
        done = next(t for t in c.get("/api/unit-tasks").json()["tasks"] if t["document_version"] == 1)
        sid = done["session_id"]
        cites = c.get(f"/api/sessions/{sid}/citations").json()["citations"]
        markdown = c.get(f"/api/sessions/{sid}/document").json()["markdown"]
        assert [x["number"] for x in cites] == list(range(1, len(cites) + 1)) and "근거 자료" not in markdown
        docs = [x for x in cites if x["kind"] == "document"]
        assert docs and all(x["text"].startswith("(가상 응답") for x in docs)
        source = c.get(f"/api/unit-tasks/{done['id']}/files").json()[0]
        assert c.get(f"/api/sessions/{sid}/sources/{source['id']}/chunks").json()["total"] > 0

        # 올린 자료를 지워도 그 근거는 '인계자 답변'으로 바뀌지 않는다(자료 이름만 비고 인용문을 돌려줌)
        used = next(x for x in docs)
        assert c.delete(f"/api/sessions/{sid}/sources/{used['source_id']}").status_code == 204
        after = {x["number"]: x for x in c.get(f"/api/sessions/{sid}/citations").json()["citations"]}
        assert after[used["number"]]["kind"] == "document" and after[used["number"]]["source_name"] == ""
        assert after[used["number"]]["text"] == used["quote"]
        health = c.get("/api/health").json()
        assert health["checks"]["rag"]["mock"] is True and health["checks"]["ai"]["engine"] == "mock"


def test_reindex_rebuilds_when_embedding_changed_and_blocks_ai_meanwhile(client, sample_dir):
    login(client)
    task = client.post("/api/unit-tasks", json={"tasks": [{"name": "홈페이지 운영"}]}).json()["tasks"][0]
    sid = task["session_id"]
    upload(client, task["id"], sample_dir, SAMPLES[:2])
    wait_question(client, sid)
    services = client.app.state.services
    assert services.kb_state(sid)["state"] == "ok"

    # 다른 임베딩으로 만든 지식베이스처럼 표시 → 조각을 더 넣을 수 없는 상태
    col = services.kb(sid).store.collection(sid, create=False)
    col.modify(metadata={**(col.metadata or {}), "embedder": "old-embedder-v0"})
    assert services.kb_state(sid)["state"] == "mismatch"

    res = client.post(f"/api/sessions/{sid}/reindex")
    assert res.status_code == 202 and res.json()["source_count"] == 2
    wait_for(client, sid, idle)
    state = services.kb_state(sid)
    assert state["state"] == "ok" and len(state["source_ids"]) == 2  # 새 임베딩으로 다시 만들어짐
    assert all(s["status"] == "ready" for s in client.get(f"/api/sessions/{sid}").json()["sources"])

    # 다시 적재하는 동안에는 AI 작업 요청을 받지 않는다
    services.runner._reindexing.add(sid)
    try:
        busy = client.post(f"/api/sessions/{sid}/chat", json={"text": "내선 2345"})
        assert busy.status_code == 409
        assert client.post(f"/api/sessions/{sid}/reindex").status_code == 409
    finally:
        services.runner._reindexing.discard(sid)
    assert client.post(f"/api/sessions/{sid}/chat", json={"text": "내선 2345"}).status_code == 202


def test_mock_reanalysis_does_not_duplicate_chunks(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path / "d", mock_mode=True, mock_delay=0))) as c:
        c.post("/api/auth/login", json={"login_id": "demo", "password": "demo1234"})
        task = next(t for t in c.get("/api/unit-tasks").json()["tasks"] if t["document_version"] == 1)
        sid = task["session_id"]
        source = c.get(f"/api/unit-tasks/{task['id']}/files").json()[0]
        before = c.get(f"/api/sessions/{sid}/sources/{source['id']}/chunks").json()["total"]
        for _ in range(3):
            assert c.post(f"/api/sessions/{sid}/analyze").status_code == 202
            for _ in range(100):
                if c.get(f"/api/sessions/{sid}").json()["status"] == "waiting":
                    break
                time.sleep(0.05)
        assert c.get(f"/api/sessions/{sid}/sources/{source['id']}/chunks").json()["total"] == before
