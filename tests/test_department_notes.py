"""부서・팀(반, 사무소)별 인수인계 목록: 소속 매칭, 분석 시작 때 AI 에 전달, 가상 응답 모드의 확인 질문."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.department_notes import DEFAULT_FILE, department_notes_for, office_dept_notes


def test_default_file_covers_every_team_in_mock_data_in_the_table_form():
    data = json.loads(DEFAULT_FILE.read_text(encoding="utf-8"))
    assert data["title"].startswith("부서・팀(반, 사무소)별 인수인계 목록")
    teams = {f"{d['부팀명']} {t['부팀명']}" for d in data["부서"] for t in d["팀"]}
    assert teams == {"총무부 법무팀", "경영전략부 기획조정팀", "IT서비스부 IT아미고팀", "디지털금융부 디지털사업팀", "결제사업부 페이먼트사업팀"}  # 팀 5개
    for d in data["부서"]:  # 형태: 부팀명 / 목록 / 비고 (부 아래에 팀)
        assert set(d) == {"부팀명", "목록", "비고", "팀"}
        for t in d["팀"]:
            assert set(t) == {"부팀명", "목록", "비고"} and t["목록"] and all(isinstance(v, str) and v and not v.startswith("가.") for v in t["목록"])


@pytest.mark.parametrize("organization", ["IT서비스부 IT아미고팀", "IT서비스부 · IT아미고팀", "IT서비스부  IT아미고팀"])
def test_organization_matching_ignores_spaces_and_middle_dots(organization):
    info = department_notes_for(organization)
    assert info and info["department"] == "IT서비스부 IT아미고팀"
    assert info["items"][0] == "시스템 구성도 및 형상 관리 대장" and info["note"].startswith("운영 서버·저장소 접근 권한 인계\n- ")
    assert "총무부 법무팀" in info["others"] and "IT서비스부 IT아미고팀" not in info["others"]
    assert "IT서비스부" not in info["others"]  # 목록이 빈 부는 참고 정보에서 뺀다


def test_unknown_or_empty_department_has_no_info(tmp_path):
    assert department_notes_for("") is None and department_notes_for("없는 부서") is None
    assert department_notes_for("IT서비스부") is None  # 부 자체에는 목록이 없다
    custom = tmp_path / "notes.json"  # AMIGO_DEPT_NOTES_FILE 로 실제 목록을 쓰는 경우
    custom.write_text(json.dumps({"부서": [{"부팀명": "가나부", "목록": ["부 공통 대장"], "비고": "", "팀": [
        {"부팀명": "다라팀", "목록": ["규정집", 3], "비고": "열쇠 인계\n- 열쇠구분, 수량"},
        {"부팀명": "마바사무소", "목록": [], "비고": ""}]}]}), encoding="utf-8")
    info = department_notes_for("가나부 · 다라팀", path=str(custom))
    assert info["items"] == ["규정집"] and info["note"] == "열쇠 인계\n- 열쇠구분, 수량" and info["others"] == {"가나부": {"items": ["부 공통 대장"], "note": ""}}
    assert department_notes_for("가나부", path=str(custom))["items"] == ["부 공통 대장"]
    assert department_notes_for("가나부 마바사무소", path=str(custom)) is None
    assert department_notes_for("IT서비스부 IT아미고팀", path=str(custom)) is None
    assert department_notes_for("가나부 다라팀", path=str(tmp_path / "missing.json")) is None


@pytest.fixture()
def mock_client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path / "d", mock_mode=True, mock_delay=0, workers=4))) as c:
        yield c


def test_team_member_sessions_are_not_asked_about_department_notes(mock_client):
    """사무관리규정 시행세칙의 부서별 추가 자료는 팀장(원내 양식) 인수인계서에만 필수라서, 팀원 세션에서는 묻지 않는다."""
    assert mock_client.post("/api/auth/login", json={"login_id": "demo", "password": "demo1234"}).status_code == 200
    task = next(t for t in mock_client.get("/api/unit-tasks").json()["tasks"] if t["name"] == "웹 접근성 관리")
    gaps = mock_client.get(f"/api/sessions/{task['session_id']}").json()["gaps"]
    assert [g["question"] for g in gaps if g["slot"] == "dept_notes"] == []


def test_office_dept_notes_only_for_the_office_form():
    from types import SimpleNamespace

    org = "IT서비스부 IT아미고팀"
    assert office_dept_notes(SimpleNamespace(form="standard", organization=org)) is None
    assert office_dept_notes(SimpleNamespace(form="", organization=org)) is None
    info = office_dept_notes(SimpleNamespace(form="office", organization=org))
    assert info and info["department"] == org and info["items"]


def test_start_analysis_does_not_pass_department_notes_for_team_members(mock_client):
    seen = {}
    services = mock_client.app.state.services
    real = services.agent.start
    services.agent.start = lambda sid, profile, kb, on_event=None: (seen.update(profile=profile), real(sid, profile, kb, on_event=on_event))[1]
    try:
        mock_client.post("/api/auth/login", json={"login_id": "demo", "password": "demo1234"})
        task = next(t for t in mock_client.get("/api/unit-tasks").json()["tasks"] if t["name"] == "유지보수 계약 관리")
        services.runner.start_analysis(task["session_id"], restart=True)
        for _ in range(100):  # 백그라운드 분석이 끝난 뒤에 임시 폴더를 지운다
            if mock_client.get(f"/api/sessions/{task['session_id']}").json()["status"] != "processing":
                break
            time.sleep(0.1)
    finally:
        services.agent.start = real
    assert "dept_notes" not in seen["profile"]  # 팀원(기본 양식)에게는 부서별 추가 자료를 넘기지 않는다
