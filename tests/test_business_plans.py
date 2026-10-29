"""팀장(원내 양식) 인사발령: 주요 사업계획(가상 예시) 조회, 세션의 사업계획·현안 저장, 부서·팀별 추가자료(16장) 파일 등록."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from app.config import Settings
from app.main import create_app
from app.models import Base, HandoverSession
from app.services.business_plans import DEFAULT_FILE, business_plans, team_plans
from app.services.container import _add_missing_columns
from app.services.documents import DEPT_FILES_HEADING, replace_section
from app.services.org_duties import org_duties
from conftest import PROFILE
from test_handovers_admin import signup

TEAM_IT = {"department": "IT서비스부", "team": "IT아미고팀"}

# ---------------------------------------------------------------- 주요 사업계획 목록


def test_default_file_lists_plans_for_every_org_team():
    data = json.loads(DEFAULT_FILE.read_text(encoding="utf-8"))
    assert "가상 예시" in data["title"] and "가상 예시" in data["note"]
    plans = {(d["name"], t["name"]): t["plans"] for d in data["divisions"] for t in d["teams"]}
    org_teams = {(d["name"], t["name"]) for d in org_duties()["divisions"] for t in d["teams"]}
    assert org_teams <= set(plans)  # 업무분장의 모든 팀에 사업계획이 있다
    assert all(4 <= len(p) <= 7 and len(set(p)) == len(p) for p in plans.values())
    assert "CI/CD 파이프라인 고도화" in team_plans("IT서비스부", "IT아미고팀")
    assert team_plans("없는 본부", "IT아미고팀") == [] and team_plans("IT서비스부", "없는 팀") == []


def test_custom_file_is_cleaned_and_missing_file_is_empty(tmp_path):
    custom = tmp_path / "plans.json"  # AMIGO_BUSINESS_PLANS_FILE 로 실제 업무보고를 쓰는 경우
    custom.write_text(json.dumps({"title": "업무보고", "divisions": [
        {"name": " 가나본부 ", "teams": [{"name": "다라팀", "plans": [" 사업1 ", "", "사업1", 3, "사업2"]}, {"name": ""}]},
    ]}), encoding="utf-8")
    assert business_plans(str(custom)) == {"title": "업무보고", "note": "", "divisions": [{"name": "가나본부", "teams": [{"name": "다라팀", "plans": ["사업1", "사업2"]}]}]}
    assert business_plans(str(tmp_path / "missing.json"))["divisions"] == []


def test_env_override(tmp_path, monkeypatch):
    custom = tmp_path / "env-plans.json"
    custom.write_text(json.dumps({"divisions": [{"name": "IT서비스부", "teams": [{"name": "IT아미고팀", "plans": ["실제 사업"]}]}]}), encoding="utf-8")
    monkeypatch.setenv("AMIGO_BUSINESS_PLANS_FILE", str(custom))
    assert team_plans("IT서비스부", "IT아미고팀") == ["실제 사업"]


def test_my_business_plans_api(client):
    assert client.get("/api/business-plans/me").status_code == 401
    client.post("/api/auth/signup", json={"login_id": "noprofile", "password": "Abcd1234!x", "name": "무소속"})
    client.post("/api/auth/login", json={"login_id": "noprofile", "password": "Abcd1234!x"})
    assert client.get("/api/business-plans/me").json() == {"division": "", "team": "", "plans": []}

    signup(client, "amigoteamjang", "김미고", TEAM_IT, "팀장", role="admin")
    body = client.get("/api/business-plans/me").json()
    assert body["division"] == "IT서비스부" and body["team"] == "IT아미고팀"
    assert body["plans"] == team_plans("IT서비스부", "IT아미고팀")


# ---------------------------------------------------------------- 세션의 사업계획·현안


def test_session_saves_plans_and_issues(client):
    plans = [" 은행 공동 본인확인 플랫폼 구축 추진 ", "", "직접 추가한 사업", "은행 공동 본인확인 플랫폼 구축 추진"]
    issues = ["은행 공동 본인확인 플랫폼 구축 추진", " 직접 추가한 현안 "]
    res = client.post("/api/sessions", json={**PROFILE, "plans": plans, "issues": issues})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["plans"] == ["은행 공동 본인확인 플랫폼 구축 추진", "직접 추가한 사업"]
    assert body["issues"] == ["은행 공동 본인확인 플랫폼 구축 추진", "직접 추가한 현안"]
    assert body["dept_uploads"] == {}
    with client.app.state.services.db() as db:
        profile = db.get(HandoverSession, body["id"]).profile()
        assert profile["plans"] == body["plans"] and profile["issues"] == body["issues"] and profile["dept_uploads"] == {}

    # 예전 세션(기본값)은 빈 목록
    assert client.post("/api/sessions", json=PROFILE).json()["plans"] == []


@pytest.mark.parametrize("field", ["plans", "issues"])
def test_plans_and_issues_limits(client, field):
    assert client.post("/api/sessions", json={**PROFILE, field: [f"사업 {i}" for i in range(31)]}).status_code == 422
    assert client.post("/api/sessions", json={**PROFILE, field: ["가" * 201]}).status_code == 422
    # 중복·빈 값을 버린 뒤 30개면 된다
    assert client.post("/api/sessions", json={**PROFILE, field: [f"사업 {i}" for i in range(30)] + ["사업 0", " "]}).status_code == 201


def test_unit_tasks_accept_plans_and_issues(client):
    signup(client, "amigoteamjang", "김미고", TEAM_IT, "팀장", role="admin")
    task = {"name": "주요 사업계획 인계", "plans": ["CI/CD 파이프라인 고도화", "새 사업"], "issues": ["새 사업"]}
    res = client.post("/api/unit-tasks", json={"tasks": [task]})
    assert res.status_code == 201, res.text
    detail = client.get(f"/api/sessions/{res.json()['tasks'][0]['session_id']}").json()
    assert detail["form"] == "office" and detail["plans"] == task["plans"] and detail["issues"] == ["새 사업"]
    assert client.post("/api/unit-tasks", json={"tasks": [{"name": "x", "plans": ["가" * 201]}]}).status_code == 422


def test_old_database_gets_new_columns(tmp_path):
    """예전 DB(새 컬럼 없음)에 사업계획·현안·추가자료 컬럼을 기본값과 함께 붙인다."""
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for column in ("plans_json", "issues_json", "dept_uploads_json"):
            conn.execute(text(f"ALTER TABLE sessions DROP COLUMN {column}"))
    _add_missing_columns(engine)
    columns = {c["name"]: c for c in inspect(engine).get_columns("sessions")}
    assert {"plans_json", "issues_json", "dept_uploads_json"} <= set(columns)
    assert columns["plans_json"]["default"] == "'[]'" and columns["dept_uploads_json"]["default"] == "'{}'"
    engine.dispose()


# ---------------------------------------------------------------- 부서·팀별 추가자료(16장)


def test_replace_section_keeps_other_edits():
    edited = "# 인수인계서\n\n## 15. 기타\n직접 쓴 메모\n\n## 16. 부서·팀(반, 사무소)별 추가자료\n- 예전 내용\n\n## 17. 확인\n서명\n"
    rendered = "# 인수인계서\n\n## 15. 기타\nAI 글\n\n## 16. 부서·팀(반, 사무소)별 추가자료\n- 새 내용: a.pdf\n\n## 17. 확인\nAI 서명\n"
    merged = replace_section(edited, rendered)
    assert "직접 쓴 메모" in merged and "서명\n" in merged and "AI 글" not in merged and "AI 서명" not in merged
    assert "- 새 내용: a.pdf" in merged and "예전 내용" not in merged
    assert merged.index("새 내용") < merged.index("## 17. 확인")
    # 마지막 장이어도, 직접 고친 문서에서 지웠어도 된다
    assert replace_section("# 문서\n\n## 16. 이름 바꿈\n옛 글", "## 16. 부서·팀(반, 사무소)별 추가자료\n새 글").endswith(f"{DEPT_FILES_HEADING}\n새 글")
    assert replace_section("# 문서\n본문\n", "## 16. 부서·팀(반, 사무소)별 추가자료\n새 글").endswith("본문\n\n## 16. 부서·팀(반, 사무소)별 추가자료\n새 글\n")
    assert replace_section("# 문서\n", "# 다른 문서\n") == "# 문서\n"  # 다시 그린 문서에 16장이 없으면 그대로


@pytest.fixture()
def mock_client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path / "d", mock_mode=True, mock_delay=0, workers=4))) as c:
        c.post("/api/auth/login", json={"login_id": "demo", "password": "demo1234"})  # 김민수(디지털전략부 웹서비스팀)
        yield c


def _document_session(client) -> str:
    sid = next(t for t in client.get("/api/unit-tasks").json()["tasks"] if t["name"] == "기관 홈페이지 운영")["session_id"]
    with client.app.state.services.db() as db:  # 데모 세션의 소속(웹서비스팀)은 목록에서 없어졌으니 남은 팀(IT아미고팀)으로 바꾼다
        db.get(HandoverSession, sid).organization = "IT서비스부 · IT아미고팀"
        db.commit()
    return sid


def _files(*names: str) -> list:
    return [("files", (n, "가상 자료".encode(), "application/octet-stream")) for n in names]


ITEM = "시스템 구성도 및 형상 관리 대장"  # department_notes.json 의 IT아미고팀 항목


def test_dept_files_validation(mock_client):
    sid = _document_session(mock_client)
    url = f"/api/sessions/{sid}/dept-files"
    assert mock_client.post(url, files=_files("a.pdf")).status_code == 422  # 항목 없음
    assert mock_client.post(url, data={"item": ITEM}).status_code == 422  # 파일 없음
    bad = mock_client.post(url, data={"item": "목록에 없는 항목"}, files=_files("a.pdf"))
    assert bad.status_code == 400 and "목록에 없는 항목" in bad.json()["message"]
    assert mock_client.post(url, data={"item": "  "}, files=_files("a.pdf")).status_code == 400

    # 문서가 아직 없는 세션
    fresh = mock_client.post("/api/sessions", json=PROFILE).json()["id"]
    assert mock_client.post(f"/api/sessions/{fresh}/dept-files", data={"item": ITEM}, files=_files("a.pdf")).status_code == 404

    # 다른 계정은 남의 세션에 올릴 수 없다
    mock_client.cookies.clear()
    mock_client.post("/api/auth/signup", json={"login_id": "stranger", "password": "Abcd1234!x", "name": "남"})
    mock_client.post("/api/auth/login", json={"login_id": "stranger", "password": "Abcd1234!x"})
    assert mock_client.post(url, data={"item": ITEM}, files=_files("a.pdf")).status_code == 404


def test_dept_files_with_mock_agent_rerender(mock_client):
    """가상 응답 모드 에이전트의 rerender 로도 오류 없이 등록된다."""
    sid = _document_session(mock_client)
    res = mock_client.post(f"/api/sessions/{sid}/dept-files", data={"item": ITEM}, files=_files("계약명세.pdf"))
    assert res.status_code == 200, res.text
    assert res.json()["version"] == 1
    detail = mock_client.get(f"/api/sessions/{sid}").json()
    assert detail["dept_uploads"] == {ITEM: ["계약명세.pdf"]}
    assert any(s["name"] == "계약명세.pdf" for s in detail["sources"])
    assert mock_client.app.state.services.agent._states[sid]["profile"]["dept_uploads"] == {ITEM: ["계약명세.pdf"]}


def test_dept_files_update_ai_version_in_place_or_add_user_version(mock_client, monkeypatch):
    sid = _document_session(mock_client)
    agent = mock_client.app.state.services.agent
    doc = mock_client.get(f"/api/sessions/{sid}/document").json()
    calls: list[dict] = []

    def fake_rerender(thread_id, patch):
        calls.append(patch)
        files = "\n".join(f"- {k}: {', '.join(v)}" for k, v in patch["dept_uploads"].items())
        markdown = f"{doc['markdown'].rstrip()}\n\n{DEPT_FILES_HEADING}\n{files}\n"
        agent._states[thread_id]["document"] = markdown  # 실제 에이전트처럼 상태의 문서도 바꾼다
        return {"markdown": markdown, "version": 1}

    monkeypatch.setattr(agent, "rerender", fake_rerender)
    url = f"/api/sessions/{sid}/dept-files"

    # AI 초안이 현재 문서면 그 버전을 그 자리에서 고친다
    first = mock_client.post(url, data={"item": ITEM}, files=_files("a.pdf", "b.pdf")).json()
    assert calls[-1] == {"dept_uploads": {ITEM: ["a.pdf", "b.pdf"]}}
    assert first["version"] == doc["version"] == 1 and first["origin"] == "ai" and f"- {ITEM}: a.pdf, b.pdf" in first["markdown"]

    # 직접 고친 문서면 16장만 바꾼 새 버전이 생기고, 직접 쓴 글은 남는다
    edited = first["markdown"].replace(DEPT_FILES_HEADING, f"> 인계자 메모: 직접 쓴 글\n\n{DEPT_FILES_HEADING}")
    assert mock_client.put(f"/api/sessions/{sid}/document", json={"markdown": edited, "base_version": 1}).status_code == 200
    second = mock_client.post(url, data={"item": ITEM}, files=_files("c.pdf", "a.pdf")).json()
    assert calls[-1] == {"dept_uploads": {ITEM: ["a.pdf", "b.pdf", "c.pdf"]}}  # 같은 이름은 한 번만
    assert second["version"] == 3 and second["origin"] == "user"
    assert "인계자 메모: 직접 쓴 글" in second["markdown"] and f"- {ITEM}: a.pdf, b.pdf, c.pdf" in second["markdown"]
    assert mock_client.get(f"/api/sessions/{sid}/document/versions").json()["versions"][0]["note"] == "부서별 추가자료 등록"

    # 스냅숏 동기화가 직접 고친 문서를 덮어쓰지 않는다
    mock_client.app.state.services.runner._sync_snapshot(sid)
    assert mock_client.get(f"/api/sessions/{sid}/document").json()["version"] == 3


def test_replace_dept_table_swaps_only_the_upload_table_in_staff_documents():
    """팀원 문서(기타 자료 > 부서별 추가 자료)도 업로드 표만 바꾸고, 직접 고친 다른 글은 그대로 둔다."""
    from app.services.documents import replace_dept_table

    edited = "## 3. 기타 자료\n\n### 부서별 추가 자료\n\n직접 쓴 안내\n\n| 추가자료 | 등록 여부 |\n|---|---|\n| 계약 명세표 | 자료 등록 필요 |\n\n끝 문장\n"
    rendered = "## 3. 기타 자료\n\n### 부서별 추가 자료\n\nAI 안내\n\n| 추가자료 | 등록 여부 |\n|---|---|\n| 계약 명세표 | 등록됨: a.pdf |\n"
    merged = replace_dept_table(edited, rendered)
    assert "| 계약 명세표 | 등록됨: a.pdf |" in merged and "직접 쓴 안내" in merged and "끝 문장" in merged and "AI 안내" not in merged


def test_dept_file_items_only_for_team_lead_form():
    from types import SimpleNamespace

    from app.services.department_notes import dept_file_items

    assert dept_file_items(SimpleNamespace(organization="IT서비스부 · IT아미고팀", form="office"))[0] == "시스템 구성도 및 형상 관리 대장"
    assert dept_file_items(SimpleNamespace(organization="IT서비스부 · IT아미고팀", form="standard")) == []  # 팀원 문서에는 싣지 않는다
    assert dept_file_items(SimpleNamespace(organization="없는 팀", form="standard")) == []
