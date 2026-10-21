"""부서별 특이사항: 소속 매칭, 분석 시작 때 AI 에 전달, 가상 응답 모드의 확인 질문."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.department_notes import DEFAULT_FILE, department_notes_for


def test_default_file_covers_every_department_in_mock_data_in_the_defined_form():
    rows = json.loads(DEFAULT_FILE.read_text(encoding="utf-8"))["departments"]
    assert {r["부서"] for r in rows} == {"디지털전략부 웹서비스팀", "디지털전략부 IT운영팀", "디지털본부 IT아미고팀", "디지털본부 AI개발팀",
                                         "재무팀", "정보보호팀", "IT지원팀", "법무팀", "총무팀", "홍보팀", "인사팀"}
    for r in rows:  # 형태: 부서 / 중요문서 위치 / 관리 가맹점 / 라이센스 업체 (정보만)
        assert set(r) == {"부서", "중요문서 위치", "관리 가맹점", "라이센스 업체"}
        assert r["중요문서 위치"] and all(d["문서"] and d["위치"] for d in r["중요문서 위치"])
        assert all(l["업체"] and l["라이센스"] for l in r["라이센스 업체"])


@pytest.mark.parametrize("organization", ["디지털전략부 웹서비스팀", "디지털전략부 · 웹서비스팀", "디지털전략부  웹서비스팀"])
def test_organization_matching_ignores_spaces_and_middle_dots(organization):
    info = department_notes_for(organization)
    assert info and info["department"] == "디지털전략부 웹서비스팀"
    assert info["franchises"] == ["한울 제휴몰", "새봄 온라인서점", "푸른길 여행"]
    assert {"vendor": "한빛웹솔루션", "product": "홈페이지 CMS"} in info["licenses"]
    assert info["documents"][0]["name"] == "웹 접근성 인증 신청서류" and "재무팀" in info["others"] and "디지털전략부 웹서비스팀" not in info["others"]


def test_unknown_or_empty_department_has_no_info(tmp_path):
    assert department_notes_for("") is None and department_notes_for("없는 부서") is None
    custom = tmp_path / "notes.json"  # AMIGO_DEPT_NOTES_FILE 로 실제 요약본을 쓰는 경우
    custom.write_text(json.dumps({"departments": [{"부서": "가나부 다라팀", "중요문서 위치": [{"문서": "규정집", "위치": "서가 3"}], "관리 가맹점": [], "라이센스 업체": []}]}), encoding="utf-8")
    info = department_notes_for("가나부 · 다라팀", path=str(custom))
    assert info["documents"] == [{"name": "규정집", "location": "서가 3"}] and info["licenses"] == [] and info["others"] == {}
    assert department_notes_for("디지털전략부 웹서비스팀", path=str(custom)) is None
    assert department_notes_for("가나부 다라팀", path=str(tmp_path / "missing.json")) is None


@pytest.fixture()
def mock_client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path / "d", mock_mode=True, mock_delay=0, workers=4))) as c:
        yield c


def test_mock_mode_asks_how_department_targets_are_managed(mock_client):
    assert mock_client.post("/api/auth/login", json={"login_id": "demo", "password": "demo1234"}).status_code == 200
    task = next(t for t in mock_client.get("/api/unit-tasks").json()["tasks"] if t["name"] == "웹 접근성 관리")
    gaps = mock_client.get(f"/api/sessions/{task['session_id']}").json()["gaps"]
    asked = [g["question"] for g in gaps if g["slot"] == "dept_notes"]
    assert len(asked) == 3
    assert "한빛웹솔루션" in asked[0] and "한울 제휴몰" in asked[1] and "웹 접근성 인증 신청서류" in asked[2]  # 라이센스 → 가맹점 → 문서
    assert not any("공유드라이브" in q for q in asked)  # 이미 아는 위치는 묻지 않는다


def test_start_analysis_passes_department_notes_to_agent(mock_client):
    from app.services import runner as runner_module

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
    notes = seen["profile"]["dept_notes"]
    assert runner_module and notes["department"] == "디지털전략부 웹서비스팀" and notes["franchises"] and "재무팀" in notes["others"]
