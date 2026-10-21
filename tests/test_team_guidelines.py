"""팀 인수인계서 지침: 관리자가 적은 항목·특징을 저장하고, 그 소속의 AI 분석에 전달한다."""

from conftest import idle, wait_for

ORG = "디지털전략부 웹서비스팀"
PROFILE = {"department": "디지털전략부", "team": "웹서비스팀", "position": "과장", "name": "김민수"}
GUIDE = {
    "items": [
        {"name": "외주 관리", "instruction": "업체명, 계약 기간, 월 금액"},
        {"name": "라이선스", "instruction": ""},
    ],
    "notes": "우리 팀은 매주 월요일 보도자료를 낸다.",
}


def login(client, login_id="minsu.kim", profile=PROFILE, name="김민수"):
    client.cookies.clear()
    assert client.post("/api/auth/signup", json={"login_id": login_id, "password": "correct-horse-1", "name": name}).status_code == 201
    assert client.post("/api/auth/login", json={"login_id": login_id, "password": "correct-horse-1"}).status_code == 200
    if profile:
        assert client.put("/api/profile", json={**profile, "name": name}).status_code == 200


def test_login_and_profile_are_required(client):
    assert client.get("/api/team-guidelines/me").status_code == 401
    assert client.put("/api/team-guidelines/me", json=GUIDE).status_code == 401
    login(client, profile=None)
    res = client.get("/api/team-guidelines/me")
    assert res.status_code == 400 and "프로필" in res.json()["message"]
    assert client.put("/api/team-guidelines/me", json=GUIDE).status_code == 400


def test_empty_then_save_then_read(client):
    login(client)
    empty = client.get("/api/team-guidelines/me").json()
    assert empty["organization"] == ORG and empty["items"] == [] and empty["notes"] == ""
    assert empty["editable"] is True and empty["prompt_preview"] == "" and empty["updated_at"] is None

    res = client.put("/api/team-guidelines/me", json=GUIDE)
    saved = res.json()
    assert res.status_code == 200 and saved["success"] is True and "저장" in saved["message"]
    assert [i["name"] for i in saved["items"]] == ["외주 관리", "라이선스"]
    assert all(i["id"] for i in saved["items"]) and "required" not in saved["items"][0]
    assert saved["notes"] == GUIDE["notes"] and saved["updated_by"] == "김민수" and saved["updated_at"]
    # AI 프롬프트에 들어가는 문구를 화면이 미리 보여 줄 수 있다
    assert "- 외주 관리 — 업체명, 계약 기간, 월 금액" in saved["prompt_preview"] and "매주 월요일 보도자료" in saved["prompt_preview"]

    again = client.get("/api/team-guidelines/me").json()
    assert again["items"] == saved["items"] and again["notes"] == saved["notes"] and again["prompt_preview"] == saved["prompt_preview"]

    # 항목 id 는 그대로 보내면 유지된다
    kept = client.put("/api/team-guidelines/me", json={"items": saved["items"], "notes": ""}).json()
    assert [i["id"] for i in kept["items"]] == [i["id"] for i in saved["items"]] and kept["notes"] == ""

    cleared = client.put("/api/team-guidelines/me", json={"items": [], "notes": ""}).json()
    assert cleared["items"] == [] and cleared["prompt_preview"] == ""


def test_validation(client):
    login(client)
    url = "/api/team-guidelines/me"
    dup = client.put(url, json={"items": [{"name": "외주 관리"}, {"name": " 외주  관리 "}]})
    assert dup.status_code == 422 and "같은 이름" in dup.json()["message"]
    assert client.put(url, json={"items": [{"name": "   "}]}).status_code == 422  # 이름이 비면 안 된다
    assert client.put(url, json={"items": [{"name": "가" * 101}]}).status_code == 422
    assert client.put(url, json={"items": [{"name": "가", "instruction": "나" * 501}]}).status_code == 422
    assert client.put(url, json={"items": [{"name": f"항목{i}"} for i in range(31)]}).status_code == 422
    assert client.put(url, json={"notes": "다" * 2001}).status_code == 422
    assert client.put(url, json={"items": [{"name": f"항목{i}"} for i in range(30)], "notes": "다" * 2000}).status_code == 200
    assert client.get(url).json()["items"][0]["name"] == "항목0"  # 실패한 요청은 저장된 값을 바꾸지 않는다


def test_guidelines_are_per_team(client):
    login(client)
    client.put("/api/team-guidelines/me", json=GUIDE)
    login(client, "other.user", {"department": "재무부", "team": "회계팀", "position": "대리"}, "박지훈")
    other = client.get("/api/team-guidelines/me").json()
    assert other["organization"] == "재무부 회계팀" and other["items"] == [] and other["notes"] == ""
    client.put("/api/team-guidelines/me", json={"items": [{"name": "예산 집행"}], "notes": ""})
    login(client, "same.team", PROFILE, "이서연")  # 같은 소속이면 같은 지침을 본다
    mine = client.get("/api/team-guidelines/me").json()
    assert [i["name"] for i in mine["items"]] == ["외주 관리", "라이선스"]


def test_only_listed_editors_can_save_when_configured(settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    settings.team_guideline_editors = " Boss ,other"
    with TestClient(create_app(settings)) as client:
        login(client, "member")
        view = client.get("/api/team-guidelines/me").json()
        assert view["editable"] is False
        denied = client.put("/api/team-guidelines/me", json=GUIDE)
        assert denied.status_code == 403 and "권한" in denied.json()["message"]
        login(client, "boss")  # 아이디는 대소문자를 구분하지 않는다
        assert client.get("/api/team-guidelines/me").json()["editable"] is True
        assert client.put("/api/team-guidelines/me", json=GUIDE).status_code == 200
        login(client, "member2")
        assert [i["name"] for i in client.get("/api/team-guidelines/me").json()["items"]] == ["외주 관리", "라이선스"]  # 읽기는 모두 가능


def test_agent_receives_the_teams_guidelines_when_analysis_starts(client, sample_dir):
    services = client.app.state.services
    started: dict[str, dict] = {}
    original = services.agent.start

    def spy(thread_id, profile, kb, on_event=None):
        started[thread_id] = dict(profile)
        return original(thread_id, profile, kb, on_event=on_event)

    services.agent.start = spy

    def run_analysis():
        task = client.post("/api/unit-tasks", json={"tasks": [{"name": "홈페이지 운영"}]}).json()["tasks"][0]
        pdf = sample_dir / "업무정의서_웹서비스팀.pdf"
        res = client.post(f"/api/unit-tasks/{task['id']}/files", files=[("files", (pdf.name, pdf.read_bytes(), "application/octet-stream"))])
        assert res.status_code == 201 and res.json()["analysis_scheduled"] is True
        wait_for(client, task["session_id"], lambda s: idle(s) and s["status"] == "waiting")
        return task["session_id"]

    login(client)
    client.put("/api/team-guidelines/me", json={**GUIDE, "prompt": "담당자 연락처를 가장 먼저 확인한다."})
    mine = run_analysis()
    guide = started[mine]["team_guidelines"]
    assert [i["name"] for i in guide["items"]] == ["외주 관리", "라이선스"]
    assert guide["notes"] == GUIDE["notes"]
    assert guide["prompt"] == "담당자 연락처를 가장 먼저 확인한다."
    assert "id" not in guide["items"][0]  # 에이전트에는 프롬프트에 필요한 값만 넘긴다

    login(client, "other.user", {"department": "재무부", "team": "회계팀", "position": "대리"}, "박지훈")
    theirs = run_analysis()
    assert "team_guidelines" not in started[theirs]  # 다른 팀의 지침은 섞이지 않고, 지침이 없으면 키 자체가 없다


def test_organization_key_ignores_separators():
    from app.services.guidelines import organization_key

    assert organization_key("디지털전략부 웹서비스팀") == organization_key("디지털전략부 · 웹서비스팀") == organization_key("  디지털전략부  ·  웹서비스팀 ") == "디지털전략부 웹서비스팀"
    assert organization_key("") == organization_key(None) == ""


def test_session_started_from_the_screen_gets_the_teams_guidelines(client, sample_dir):
    """화면의 '새 인수인계 시작' 은 소속을 '부서 · 팀' 으로 보내 세션을 만든다. 프로필 소속('부서 팀')으로 저장한 지침이 그 세션에도 가야 한다."""
    from conftest import PROFILE as SESSION_PROFILE
    from conftest import upload

    services = client.app.state.services
    started: dict[str, dict] = {}
    original = services.agent.start

    def spy(thread_id, profile, kb, on_event=None):
        started[thread_id] = dict(profile)
        return original(thread_id, profile, kb, on_event=on_event)

    services.agent.start = spy
    login(client)
    client.put("/api/team-guidelines/me", json=GUIDE)
    created = client.post("/api/sessions", json={**SESSION_PROFILE, "organization": "디지털전략부 · 웹서비스팀"})
    assert created.status_code == 201
    sid = created.json()["id"]
    assert upload(client, sid, [sample_dir / "업무정의서_웹서비스팀.pdf"]).status_code == 201
    wait_for(client, sid, lambda s: all(src["status"] in ("ready", "failed") for src in s["sources"]))
    assert client.post(f"/api/sessions/{sid}/analyze").status_code == 202
    wait_for(client, sid, lambda s: idle(s) and s["status"] == "waiting")
    assert [i["name"] for i in started[sid]["team_guidelines"]["items"]] == ["외주 관리", "라이선스"]


def test_team_prompt_is_saved_alone_without_touching_items_and_notes(client):
    login(client)
    url = "/api/team-guidelines/me"
    assert client.get(url).json()["prompt"] == ""
    client.put(url, json=GUIDE)  # 항목과 팀 특징을 먼저 저장

    saved = client.put(url, json={"prompt": "  모든 항목 제목은 [웹팀] 으로 시작하세요.  "}).json()  # AI 맞춤 설정 화면은 프롬프트만 보낸다
    assert saved["prompt"] == "모든 항목 제목은 [웹팀] 으로 시작하세요."  # 앞뒤 공백은 지운다
    assert [i["name"] for i in saved["items"]] == ["외주 관리", "라이선스"] and saved["notes"] == GUIDE["notes"]  # 보내지 않은 필드는 그대로
    assert "[웹팀] 으로 시작" in saved["prompt_preview"] and "### 팀 전용 프롬프트" in saved["prompt_preview"]

    client.put(url, json={"items": [], "notes": ""})  # 항목 화면은 프롬프트를 보내지 않으므로 프롬프트는 남는다
    kept = client.get(url).json()
    assert kept["items"] == [] and kept["notes"] == "" and kept["prompt"] == "모든 항목 제목은 [웹팀] 으로 시작하세요."
    assert "[웹팀] 으로 시작" in kept["prompt_preview"]

    cleared = client.put(url, json={"prompt": ""}).json()  # 빈 글을 보내면 지운다
    assert cleared["prompt"] == "" and cleared["prompt_preview"] == ""
    assert client.put(url, json={"prompt": "가" * 2001}).status_code == 422
    assert client.put(url, json={"prompt": "가" * 2000}).status_code == 200


def test_prompt_only_team_gets_the_prompt_in_the_agent_profile(client):
    from app.models import TeamGuideline
    from app.services.guidelines import guidelines_for

    login(client)
    client.put("/api/team-guidelines/me", json={"prompt": "항상 존댓말로 쓰세요."})  # 항목 없이 프롬프트만
    services = client.app.state.services
    with services.db() as db:
        assert db.get(TeamGuideline, ORG).items_json == "[]"
        guide = guidelines_for(db, "디지털전략부 · 웹서비스팀")
    assert guide == {"items": [], "notes": "", "prompt": "항상 존댓말로 쓰세요."}


def test_prompt_saved_after_analysis_reaches_the_agent_on_the_next_turn(tmp_path):
    """팀장이 분석이 끝난 뒤 프롬프트를 저장해도, 팀원 세션의 다음 답변 처리부터 에이전트가 새 프롬프트를 쓴다(다시 분석하지 않아도)."""
    import time

    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    with TestClient(create_app(Settings(data_dir=tmp_path / "d", mock_mode=True, mock_delay=0, workers=4))) as c:
        c.post("/api/auth/login", json={"login_id": "demo", "password": "demo1234"})
        task = next(t for t in c.get("/api/unit-tasks").json()["tasks"] if t["name"] == "웹 접근성 관리")
        sid = task["session_id"]
        agent = c.app.state.services.agent
        assert "team_guidelines" not in agent._states[sid]["profile"]
        assert c.put("/api/team-guidelines/me", json={"prompt": "금액과 기한은 반드시 확인한다."}).status_code == 200
        assert c.post(f"/api/sessions/{sid}/skip").status_code == 202
        for _ in range(100):
            if c.get(f"/api/sessions/{sid}").json()["status"] != "running":
                break
            time.sleep(0.1)
        assert agent._states[sid]["profile"]["team_guidelines"]["prompt"] == "금액과 기한은 반드시 확인한다."
        c.put("/api/team-guidelines/me", json={"prompt": ""})  # 프롬프트를 지우면 다음 턴에 빠진다
        c.post(f"/api/sessions/{sid}/skip")
        for _ in range(100):
            if c.get(f"/api/sessions/{sid}").json()["status"] != "running":
                break
            time.sleep(0.1)
        assert "team_guidelines" not in agent._states[sid]["profile"]
