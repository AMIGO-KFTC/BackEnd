"""가상 응답 모드(AMIGO_MOCK_MODE=true): 실제 분석·AI 없이 예시 데이터로 같은 API 계약을 지키는지."""

import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def mock_client(tmp_path):
    settings = Settings(data_dir=tmp_path / "real-data", mock_mode=True, mock_delay=0, workers=4)
    with TestClient(create_app(settings)) as c:
        yield c


def login_demo(client) -> None:
    res = client.post("/api/auth/login", json={"login_id": "demo", "password": "demo1234"})
    assert res.status_code == 200 and res.json()["name"] == "김민수"


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


def upload(client, task_id, names):
    files = [("files", (n, f"가상 자료 {n}".encode(), "application/octet-stream")) for n in names]
    return client.post(f"/api/unit-tasks/{task_id}/files", files=files)


def test_marks_responses_as_mock(mock_client, client):
    health = mock_client.get("/api/health")
    assert health.json()["mock"] is True and health.json()["engine"] == "mock"
    assert health.headers["x-amigo-mock"] == "true"
    assert mock_client.get("/api/config").json()["mock"] is True
    real = client.get("/api/health")  # 실제 모드에는 표시가 없다
    assert real.json()["mock"] is False and "x-amigo-mock" not in real.headers


def test_demo_account_has_tasks_in_each_state(mock_client):
    login_demo(mock_client)
    assert mock_client.get("/api/profile").json()["profile"] == {"department": "디지털전략부", "position": "과장", "team": "웹서비스팀", "name": "김민수", "employee_no": ""}
    tasks = mock_client.get("/api/unit-tasks").json()["tasks"]
    assert [t["name"] for t in tasks] == ["기관 홈페이지 운영", "웹 접근성 관리", "유지보수 계약 관리"]

    by_title = {c["title"]: c for c in mock_client.get("/api/conversations").json()["conversations"]}
    done = by_title["기관 홈페이지 운영 인수인계"]
    assert done["next_action"] == "review_document" and done["document_version"] == 1 and done["unread_count"] == 0
    asking = by_title["웹 접근성 관리 인수인계"]
    assert asking["next_action"] == "answer_question" and asking["current_question"]["number"] == 1 and asking["unread_count"] > 0
    empty = by_title["유지보수 계약 관리 인수인계"]
    assert empty["next_action"] == "upload_files" and empty["file_count"] == 0

    doc = mock_client.get(f"/api/sessions/{done['session_id']}/document").json()["markdown"]
    assert "# 업무 인수인계서" in doc and "| 단위 업무명 | 기관 홈페이지 운영 |" in doc and "내선 2345" in doc and "```mermaid" not in doc  # 관계도 그림은 없다
    assert mock_client.get(f"/api/unit-tasks/{tasks[0]['id']}/files").json()[0]["status"] == "ready"
    assert mock_client.get(f"/api/sessions/{done['session_id']}/download", params={"format": "pdf"}).content.startswith(b"%PDF")


def test_full_flow_with_virtual_ai(mock_client):
    login_demo(mock_client)
    task = mock_client.post("/api/unit-tasks", json={"tasks": [{"name": "보도자료 게시"}]}).json()["tasks"][0]
    sid = task["session_id"]

    res = upload(mock_client, task["id"], ["업무정의서.pdf", "운영매뉴얼.hwpx", "스캔본_실패.pdf", "설치.exe"]).json()
    assert res["success"] and res["analysis_scheduled"] and "파일 3개를 저장했습니다. 1개는 저장하지 못했습니다(설치.exe)." in res["message"]
    p = wait_progress(mock_client, sid, lambda p: p["next_action"] == "answer_question")
    files = {f["name"]: f for f in mock_client.get(f"/api/unit-tasks/{task['id']}/files").json()}
    assert files["업무정의서.pdf"]["status"] == "ready" and files["업무정의서.pdf"]["chunk_count"] > 0
    assert files["스캔본_실패.pdf"]["status"] == "failed" and "가상 응답" in files["스캔본_실패.pdf"]["error"]
    assert files["설치.exe"]["status"] == "failed"  # 확장자 검사는 실제와 같다

    session = mock_client.get(f"/api/sessions/{sid}").json()
    assert set(session["slots"]) == {"overview", "stakeholders", "regular", "irregular", "systems", "dept_notes"}
    labels = {c["label"] for slot in session["slots"].values() for item in slot["items"] for c in item["citations"]}
    assert labels and all(label.split(" ")[0] in ("업무정의서.pdf", "운영매뉴얼.hwpx") for label in labels)  # 실제로 읽힌 자료만 근거로
    assert "업무정의서.pdf p.1" in labels
    assert p["questions"]["total"] == 8 and "정보보호팀" in p["current_question"]["content"]

    # 답변 → 확인 요청 → 고쳐 말하기 → 다시 확인 → 네
    mock_client.post(f"/api/sessions/{sid}/chat", json={"text": "내선 2345 입니다"})
    p = wait_progress(mock_client, sid, lambda p: p["next_action"] == "confirm_answer")
    assert "내선 2345" in p["current_question"]["content"]
    mock_client.post(f"/api/sessions/{sid}/chat", json={"text": "아니요, 내선 2346 입니다"})
    p = wait_progress(mock_client, sid, lambda p: p["next_action"] == "confirm_answer" and "2346" in p["current_question"]["content"])
    assert p["current_question"]["content"].startswith("수정한 내용으로")
    mock_client.post(f"/api/sessions/{sid}/chat", json={"text": "네, 맞아요"})
    p = wait_progress(mock_client, sid, lambda p: p["next_action"] == "answer_question" and p["current_question"]["number"] == 2)
    assert not p["current_question"]["content"].startswith(("확인했습니다.", "**[")) and p["questions"]["answered"] == 1  # 질문만 나온다

    # 건너뛰기 → 다음 질문, 지금 문서 생성 → 문서 v1
    assert mock_client.post(f"/api/sessions/{sid}/skip").status_code == 202
    p = wait_progress(mock_client, sid, lambda p: p["status"] == "waiting" and p["current_question"] and p["current_question"]["number"] == 3)
    assert p["questions"]["skipped"] == 1
    assert mock_client.post(f"/api/sessions/{sid}/generate").status_code == 202
    p = wait_progress(mock_client, sid, lambda p: p["next_action"] == "review_document")
    doc = mock_client.get(f"/api/sessions/{sid}/document").json()
    assert doc["version"] == 1 and "내선 2346" in doc["markdown"] and "## 6. 확인 필요 사항" in doc["markdown"]

    # 검토 의견 → 문서 v2
    mock_client.post(f"/api/sessions/{sid}/chat", json={"text": "외주 업체 계약은 매년 12월에 갱신합니다"})
    wait_progress(mock_client, sid, lambda p: p["document_version"] == 2 and p["status"] == "waiting")
    assert "외주 업체 계약은 매년 12월에 갱신합니다" in mock_client.get(f"/api/sessions/{sid}/document").json()["markdown"]

    # 화면에서 고친 월별 막대는 계획기간으로 확정되어 조회·다운로드·재생성에 그대로 반영된다
    before = mock_client.get(f"/api/sessions/{sid}/document").json()
    name = "개인정보처리방침 개정 반영"
    edited = mock_client.put(f"/api/sessions/{sid}/document/gantt", json={"name": name, "nth": 0, "months": [2, 3]})
    assert edited.status_code == 200 and edited.json()["version"] == before["version"]
    md = edited.json()["markdown"]
    assert md != before["markdown"] and mock_client.get(f"/api/sessions/{sid}/document").json()["markdown"] == md
    assert mock_client.get(f"/api/sessions/{sid}/download?format=md").content.decode("utf-8") == md
    assert mock_client.put(f"/api/sessions/{sid}/document/gantt", json={"name": "없는 업무", "months": [1]}).status_code == 409
    assert mock_client.put(f"/api/sessions/{sid}/document/gantt", json={"name": name, "months": [13]}).status_code == 422

    # 처리 중 중복 요청 거절 등 실제 규칙은 그대로
    assert mock_client.post(f"/api/sessions/{sid}/chat", json={"text": ""}).status_code == 422


def test_leave_mode_uses_light_template(mock_client):
    login_demo(mock_client)
    sid = mock_client.post("/api/sessions", json={"owner_name": "김민수", "mode": "leave", "task_name": "보도자료 게시"}).json()["id"]
    mock_client.post(f"/api/sessions/{sid}/upload", files=[("files", ("자료.pdf", b"x", "application/pdf"))])
    for _ in range(100):
        s = mock_client.get(f"/api/sessions/{sid}").json()
        if s["sources"][0]["status"] == "ready":
            break
        time.sleep(0.05)
    assert mock_client.post(f"/api/sessions/{sid}/analyze").status_code == 202
    for _ in range(100):
        s = mock_client.get(f"/api/sessions/{sid}").json()
        if s["status"] == "waiting":
            break
        time.sleep(0.05)
    assert set(s["slots"]) == {"leave_units", "leave_tasks", "systems"}  # 장기휴가 양식


def test_progress_is_visible_with_delay(tmp_path):
    settings = Settings(data_dir=tmp_path / "d", mock_mode=True, mock_delay=0.05, mock_seed=False)
    with TestClient(create_app(settings)) as c:
        c.post("/api/auth/signup", json={"login_id": "fe-dev", "password": "pass-1234", "name": "이서연"})
        c.post("/api/auth/login", json={"login_id": "fe-dev", "password": "pass-1234"})
        assert c.get("/api/unit-tasks").json()["tasks"] == []  # 시드를 끄면 빈 상태
        c.put("/api/profile", json={"department": "디지털전략부", "team": "", "position": "대리", "name": "이서연"})
        task = c.post("/api/unit-tasks", json={"tasks": [{"name": "홈페이지 운영"}]}).json()["tasks"][0]
        upload(c, task["id"], ["업무정의서.pdf"])
        seen = set()
        deadline = time.time() + 20
        while time.time() < deadline:
            s = c.get(f"/api/sessions/{task['session_id']}/state").json()["session"]
            seen.add((s["status"], s["progress"]["current"]))
            if s["status"] == "waiting":
                break
            time.sleep(0.02)
        running_steps = {cur for status, cur in seen if status == "running"}
        assert len(running_steps) >= 3  # 장별 진행률이 차례로 올라가는 것이 보인다


def test_mock_mode_never_touches_real_data(tmp_path):
    real_dir = tmp_path / "real-data"
    settings = Settings(data_dir=real_dir, database_url=f"sqlite:///{tmp_path / 'real.db'}", mock_mode=True, mock_delay=0)
    with TestClient(create_app(settings)) as c:
        mock_dir = c.app.state.services.mock_dir
        assert mock_dir.exists() and c.app.state.services.settings.data_dir == mock_dir
        login_demo(c)
    assert not real_dir.exists() and not (tmp_path / "real.db").exists()
    assert not mock_dir.exists()  # 종료하면 임시 폴더를 지운다
