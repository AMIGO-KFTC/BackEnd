import io
import zipfile

from fastapi.testclient import TestClient
from pypdf import PdfReader

from app.main import create_app
from conftest import PROFILE, idle, upload, wait_for


def _messages(client, sid, after=0):
    return client.get(f"/api/sessions/{sid}/messages", params={"after": after}).json()


def test_full_flow_upload_analyze_chat_generate_download(client, sample_dir):
    created = client.post("/api/sessions", json=PROFILE, headers={"X-User-Id": "u1"})
    assert created.status_code == 201
    session = created.json()
    sid = session["id"]
    assert session["stage"] == "setup" and session["title"] == "김민수 · 홈페이지 운영 인수인계"
    assert [s["title"] for s in client.get("/api/sessions", headers={"X-User-Id": "u1"}).json()] == ["김민수 · 홈페이지 운영 인수인계"]
    assert client.get("/api/sessions", headers={"X-User-Id": "someone-else"}).json() == []

    # 분석 전 대화는 막는다
    assert client.post(f"/api/sessions/{sid}/chat", json={"text": "안녕"}).status_code == 400
    assert client.post(f"/api/sessions/{sid}/analyze").status_code == 400  # 자료 없음

    res = upload(client, sid, sorted(p for p in sample_dir.iterdir()))
    assert res.status_code == 201
    assert {s["status"] for s in res.json()} <= {"pending", "processing"}
    state = wait_for(client, sid, idle)
    assert all(s["status"] == "ready" and s["chunk_count"] > 0 for s in state["session"]["sources"])

    # STAGE 1 → 2 → 첫 질문
    assert client.post(f"/api/sessions/{sid}/analyze").status_code == 202
    state = wait_for(client, sid, lambda s: s["status"] == "waiting")
    session = state["session"]
    assert session["stage"] == "qna" and session["engine"] == "offline"
    assert set(session["slots"]) == {"overview", "stakeholders", "regular", "irregular", "systems", "dept_notes"}
    assert session["progress"]["current"] == session["progress"]["total"] == 6
    kinds = [m["kind"] for m in state["messages"]]
    assert kinds[0] == "info" and "summary" in kinds and kinds[-1] == "question"
    question = state["messages"][-1]
    assert question["meta"]["gap_id"] and question["meta"]["quick_replies"]
    cursor = question["id"]

    # STAGE 3: 답변 → 교차 확인 → 확인 → 다음 질문
    res = client.post(f"/api/sessions/{sid}/chat", json={"text": "재무팀 박지훈 차장 연락처는 내선 2345 입니다"})
    assert res.status_code == 202 and res.json()["message"]["role"] == "user"
    wait_for(client, sid, lambda s: s["status"] == "waiting")
    new = _messages(client, sid, cursor)
    assert [m["role"] for m in new] == ["user", "assistant"] and new[-1]["kind"] == "confirm"
    cursor = new[-1]["id"]
    client.post(f"/api/sessions/{sid}/chat", json={"text": "네"})
    wait_for(client, sid, lambda s: s["status"] == "waiting")
    new = _messages(client, sid, cursor)
    assert new[-1]["kind"] == "question" and new[-1]["content"].startswith("확인했습니다.")

    assert client.post(f"/api/sessions/{sid}/skip").status_code == 202
    wait_for(client, sid, lambda s: s["status"] == "waiting")

    # STAGE 4: 문서 생성 → 다운로드
    assert client.post(f"/api/sessions/{sid}/generate").status_code == 202
    state = wait_for(client, sid, lambda s: s["status"] == "waiting" and s["document_version"] >= 1)
    assert state["session"]["stage"] == "review"
    doc = client.get(f"/api/sessions/{sid}/document").json()
    assert doc["version"] == 1 and "# 업무 인수인계서" in doc["markdown"] and "내선 2345" in doc["markdown"]

    md = client.get(f"/api/sessions/{sid}/download", params={"format": "md"})
    assert md.headers["content-type"].startswith("text/markdown")
    assert "filename*=UTF-8''" in md.headers["content-disposition"]

    docx = client.get(f"/api/sessions/{sid}/download", params={"format": "docx"})
    assert docx.status_code == 200
    with zipfile.ZipFile(io.BytesIO(docx.content)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    assert "업무 인수인계서" in xml and "맑은 고딕" in xml and "내선 2345" in xml

    pdf = client.get(f"/api/sessions/{sid}/download", params={"format": "pdf"})
    assert pdf.content.startswith(b"%PDF")
    text = "".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "업무 인수인계서" in text.replace("\n", " ") or "인수인계서" in text

    # 검토 의견 → 문서 v2
    client.post(f"/api/sessions/{sid}/chat", json={"text": "Google Analytics 권한은 기존 담당자가 직접 이관해 줍니다"})
    state = wait_for(client, sid, lambda s: s["status"] == "waiting" and s["document_version"] >= 2)
    assert state["session"]["document_version"] == 2


def test_upload_validation_and_safe_names(client, settings, tmp_path):
    sid = client.post("/api/sessions", json=PROFILE).json()["id"]
    big = b"x" * (settings.max_upload_mb * 1024 * 1024 + 1)
    files = [
        ("files", ("악성.exe", b"MZ", "application/octet-stream")),
        ("files", ("큰파일.txt", big, "text/plain")),
        ("files", ("../../../etc/passwd.txt", "비밀 아님".encode(), "text/plain")),
        ("files", ("빈파일.txt", b"", "text/plain")),
    ]
    res = client.post(f"/api/sessions/{sid}/upload", files=files).json()
    by_name = {s["name"]: s for s in res}
    assert by_name["악성.exe"]["status"] == "failed" and "지원하지 않는" in by_name["악성.exe"]["error"]
    assert by_name["큰파일.txt"]["status"] == "failed" and "너무 큽니다" in by_name["큰파일.txt"]["error"]
    assert by_name["빈파일.txt"]["status"] == "failed"
    assert "passwd.txt" in by_name  # 경로는 버리고 파일명만 남는다
    wait_for(client, sid, idle)
    stored = list((settings.upload_dir / sid).iterdir())
    assert len(stored) == 1 and stored[0].suffix == ".txt" and stored[0].parent == settings.upload_dir / sid

    # 실패한 자료는 삭제할 수 있다
    failed = by_name["악성.exe"]["id"]
    assert client.delete(f"/api/sessions/{sid}/sources/{failed}").status_code == 204
    assert all(s["id"] != failed for s in client.get(f"/api/sessions/{sid}/sources").json())


def test_links_are_validated_and_blocked_hosts_fail(client):
    sid = client.post("/api/sessions", json=PROFILE).json()["id"]
    assert client.post(f"/api/sessions/{sid}/links", json={"urls": ["ftp://example.com/a"]}).status_code == 400
    res = client.post(f"/api/sessions/{sid}/links", json={"urls": ["http://127.0.0.1:9/secret"]})
    assert res.status_code == 201 and res.json()[0]["link_type"] == "web"
    state = wait_for(client, sid, idle)
    source = state["session"]["sources"][0]
    assert source["status"] == "failed" and "로컬 주소" in source["error"]
    confluence = client.post(
        f"/api/sessions/{sid}/links", json={"urls": ["https://acme.atlassian.net/wiki/spaces/A/pages/1/x"]}
    ).json()[0]
    assert confluence["link_type"] == "confluence"


def test_files_added_during_qna_trigger_reanalysis(client, sample_dir):
    sid = client.post("/api/sessions", json=PROFILE).json()["id"]
    upload(client, sid, [sample_dir / "업무정의서_웹서비스팀.pdf"])
    wait_for(client, sid, idle)
    client.post(f"/api/sessions/{sid}/analyze")
    state = wait_for(client, sid, lambda s: s["status"] == "waiting")
    before = len(state["session"]["slots"]["systems"]["items"])
    cursor = state["messages"][-1]["id"]

    upload(client, sid, [sample_dir / "홈페이지_운영매뉴얼.hwpx"])
    state = wait_for(client, sid, lambda s: idle(s) and s["status"] == "waiting" and len(_messages(client, sid, cursor)) > 0)
    new = _messages(client, sid, cursor)
    assert "새 자료(홈페이지_운영매뉴얼.hwpx)를 반영했어요" in new[-1]["content"]
    after = client.get(f"/api/sessions/{sid}").json()["slots"]["systems"]["items"]
    assert len(after) > before


def test_busy_session_rejects_parallel_requests(client, sample_dir):
    sid = client.post("/api/sessions", json=PROFILE).json()["id"]
    upload(client, sid, [sample_dir / "주간회의록_2026-09-22.docx"])
    wait_for(client, sid, idle)
    assert client.post(f"/api/sessions/{sid}/analyze").status_code == 202
    second = client.post(f"/api/sessions/{sid}/analyze")
    assert second.status_code in (202, 409)  # 첫 작업이 이미 끝났을 수도 있다
    wait_for(client, sid, lambda s: s["status"] == "waiting")


def test_delete_session_removes_everything(client, settings, sample_dir):
    sid = client.post("/api/sessions", json=PROFILE).json()["id"]
    upload(client, sid, [sample_dir / "재무팀_유지보수대금_요청.eml"])
    wait_for(client, sid, idle)
    assert (settings.upload_dir / sid).exists()
    assert client.delete(f"/api/sessions/{sid}").status_code == 204
    assert client.get(f"/api/sessions/{sid}").status_code == 404
    assert not (settings.upload_dir / sid).exists()
    assert client.get("/api/sessions/unknown/state").status_code == 404


def test_serves_built_frontend(tmp_path, settings):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>AMIGO</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
    settings.frontend_dist = str(dist)
    with TestClient(create_app(settings)) as c:
        assert "AMIGO" in c.get("/").text
        assert "AMIGO" in c.get("/s/abc").text  # SPA 경로는 index.html
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert c.get("/favicon.svg").text == "<svg/>"
        assert "AMIGO" in c.get("/..%2Fsecret.txt").text  # dist 밖 파일은 내주지 않음
        missing = c.get("/api/nope")
        assert missing.status_code == 404 and missing.json()["detail"]
        assert c.get("/api/health").json()["status"] == "ok"


def test_usage_is_recorded_and_budget_blocks_new_ai_work(client, settings):
    from app.services.runner import EventRecorder

    sid = client.post("/api/sessions", json=PROFILE).json()["id"]
    services = client.app.state.services
    record = EventRecorder(services, sid)
    record({"type": "usage", "requests": 1, "input_tokens": 100, "output_tokens": 2000, "cache_read_input_tokens": 5000,
            "cache_creation_input_tokens": 300, "cost_usd": 0.0425})
    record({"type": "usage", "requests": 1, "input_tokens": 10, "output_tokens": 500, "cost_usd": 0.01})

    state = client.get(f"/api/sessions/{sid}/state").json()
    usage = state["session"]["usage"]
    assert usage == {"requests": 2, "input_tokens": 110, "output_tokens": 2500, "cache_read_tokens": 5000,
                     "cache_write_tokens": 300, "cost_usd": 0.0525}
    total = client.get("/api/usage").json()
    assert total["cost_usd"] == 0.0525 and total["sessions"] == 1 and total["budget_usd"] == 0 and total["remaining_usd"] is None
    assert state["usage_total"]["cost_usd"] == 0.0525

    # 예산을 다 쓰면 Claude 엔진의 새 AI 작업을 막는다(오프라인 엔진은 비용이 없으므로 막지 않는다)
    services.settings.llm_budget_usd = 0.05
    assert client.get("/api/usage").json()["remaining_usd"] == 0
    services.agent.describe = lambda: {"engine": "claude", "model": "claude-opus-5-5"}
    blocked = client.post(f"/api/sessions/{sid}/analyze")
    assert blocked.status_code == 429 and "예산" in blocked.json()["detail"]


def test_leave_mode_session_is_light_and_config_lists_modes(client, sample_dir):
    config = client.get("/api/config").json()
    assert {m["key"] for m in config["modes"]} == {"reassignment", "leave"}
    by_key = {s["key"]: s for s in config["slots"]}
    assert by_key["dept_notes"]["leave"] is False and by_key["stakeholders"]["leave"] is True

    created = client.post("/api/sessions", json={**PROFILE, "mode": "leave", "task_name": "CD공동망 운영"})
    assert created.status_code == 201 and created.json()["mode"] == "leave" and created.json()["task_name"] == "CD공동망 운영"
    sid = created.json()["id"]
    assert client.post("/api/sessions", json={**PROFILE, "mode": "unknown"}).status_code == 422
    upload(client, sid, sorted(p for p in sample_dir.iterdir()))
    wait_for(client, sid, idle)
    assert client.post(f"/api/sessions/{sid}/analyze").status_code == 202
    state = wait_for(client, sid, lambda s: s["status"] == "waiting")
    assert set(state["session"]["slots"]) == {"stakeholders", "regular", "irregular", "systems"}
