"""관리자 페이지: 팀 인수인계 현황(팀원별 작성 수행 기록과 성과)."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def mock_client(tmp_path):
    settings = Settings(data_dir=tmp_path / "real-data", mock_mode=True, mock_delay=0, workers=4)
    with TestClient(create_app(settings)) as c:
        yield c


def login(client, login_id, password):
    assert client.post("/api/auth/login", json={"login_id": login_id, "password": password}).status_code == 200


WEB = {"department": "디지털전략부", "team": "웹서비스팀"}


def test_team_report_shows_each_member_progress_and_performance(mock_client):
    login(mock_client, "admin", "admin1234")
    teams = {t["label"]: t for t in mock_client.get("/api/admin/teams").json()["teams"]}
    web = teams["디지털전략부 웹서비스팀"]
    assert (web["member_count"], web["task_count"], web["document_count"]) == (3, 7, 2) and web["completion_rate"] == round(2 / 7, 3)
    assert teams["디지털전략부 IT운영팀"]["task_count"] == 0

    report = mock_client.get("/api/admin/teams/report", params=WEB).json()
    assert report["label"] == "디지털전략부 웹서비스팀" and report["message"] == "디지털전략부 웹서비스팀 팀원 3명의 인수인계 현황입니다."
    summary = report["summary"]
    assert (summary["member_count"], summary["active_member_count"], summary["task_count"], summary["document_count"]) == (3, 3, 7, 2)
    members = {m["login_id"]: m for m in report["members"]}
    assert [m["name"] for m in report["members"]] == ["김민수", "박준호", "이서연"]  # 이름순

    minsu = members["demo"]
    assert (minsu["position"], minsu["task_count"], minsu["document_count"]) == ("과장", 3, 1)
    assert minsu["completion_rate"] == round(1 / 3, 3)
    assert minsu["stages"] == {"preparing": 1, "analyzing": 0, "answering": 1, "completed": 1}
    assert minsu["source_count"] == 6 + 3 and minsu["ai_versions"] == 1 and minsu["user_versions"] == 0
    assert minsu["questions_answered"] > 0 and minsu["answer_rate"] == 1.0
    assert 0 < minsu["coverage_rate"] <= 1 and minsu["avg_hours_to_first_document"] is not None

    seoyeon = members["seoyeon.lee"]  # 하나는 문서 완성, 하나는 1건 답하고 1건 건너뜀
    assert (seoyeon["task_count"], seoyeon["document_count"], seoyeon["completion_rate"]) == (2, 1, 0.5)
    assert seoyeon["questions_skipped"] == 1 and 0 < seoyeon["answer_rate"] < 1
    junho = members["junho.park"]
    assert (junho["document_count"], junho["completion_rate"], junho["stages"]["preparing"]) == (0, 0.0, 1)
    assert junho["questions_answered"] == 0 and junho["answer_rate"] is None and junho["last_activity_at"]

    # 기간: 미래 날짜부터면 해당 단위업무가 없다
    empty = mock_client.get("/api/admin/teams/report", params={**WEB, "date_from": "2099-01-01"}).json()
    assert empty["summary"]["task_count"] == 0 and empty["members"][0]["completion_rate"] is None
    assert mock_client.get("/api/admin/teams/report", params={**WEB, "date_from": "2026-12-01", "date_to": "2026-01-01"}).status_code == 400
    assert mock_client.get("/api/admin/teams/report", params={"department": "없는 부서"}).status_code == 404


def test_member_report_lists_tasks_and_activity(mock_client):
    login(mock_client, "demo", "demo1234")  # 팀원이 문서를 직접 고치면 성과에 직접 수정으로 잡힌다
    task = next(t for t in mock_client.get("/api/unit-tasks").json()["tasks"] if t["name"] == "기관 홈페이지 운영")
    doc = mock_client.get(f"/api/sessions/{task['session_id']}/document").json()
    mock_client.put(f"/api/sessions/{task['session_id']}/document", json={"markdown": doc["markdown"] + "\n메모", "base_version": doc["version"]})
    mock_client.post("/api/auth/logout")

    login(mock_client, "admin", "admin1234")
    minsu = next(m for m in mock_client.get("/api/admin/teams/report", params=WEB).json()["members"] if m["login_id"] == "demo")
    assert minsu["user_versions"] == 1

    detail = mock_client.get(f"/api/admin/members/{minsu['user_id']}/report").json()
    assert detail["department"] == "디지털전략부" and detail["team"] == "웹서비스팀" and detail["member"]["user_versions"] == 1
    tasks = {t["name"]: t for t in detail["tasks"]}
    done = tasks["기관 홈페이지 운영"]
    assert done["stage_group"] == "completed" and done["has_document"] and done["document_version"] == 2
    assert (done["ai_versions"], done["user_versions"], done["source_count"]) == (1, 1, 6)
    assert tasks["유지보수 계약 관리"]["stage_group"] == "preparing" and tasks["유지보수 계약 관리"]["coverage_rate"] is None
    assert tasks["웹 접근성 관리"]["questions_open"] > 0

    activity = detail["activity"]
    kinds = {a["kind"] for a in activity}
    assert {"task", "source", "answer", "document_ai", "document_user"} <= kinds
    assert activity[0]["kind"] == "document_user" and activity[0]["task_name"] == "기관 홈페이지 운영" and activity[0]["detail"].startswith("v2")
    assert [a["at"] for a in activity] == sorted((a["at"] for a in activity), reverse=True)
    assert all("내선" not in a["detail"] for a in activity)  # 답변 내용은 담지 않는다
    assert mock_client.get("/api/admin/members/999/report").status_code == 404


def test_team_report_is_admin_only(mock_client):
    login(mock_client, "demo", "demo1234")
    assert mock_client.get("/api/admin/teams").status_code == 403
    assert mock_client.get("/api/admin/teams/report", params=WEB).status_code == 403
    assert mock_client.get("/api/admin/members/1/report").status_code == 403
    mock_client.post("/api/auth/logout")
    assert mock_client.get("/api/admin/teams").status_code == 401


def test_team_report_on_regular_database(client):
    """가상 응답 모드가 아닌 DB(PostgreSQL 포함)에서도 집계 쿼리가 동작하는지."""
    from app.models import HandoverSession, User
    from app.services.documents import add_version

    def member(login_id, name, position):
        client.post("/api/auth/signup", json={"login_id": login_id, "password": "password-123", "name": name})
        login(client, login_id, "password-123")
        client.patch("/api/me", json={"department": "기획부", "team": "전략팀", "position": position})
        return client.post("/api/unit-tasks", json={"tasks": [{"name": f"{name} 업무 A"}, {"name": f"{name} 업무 B"}]}).json()["tasks"]

    a_tasks = member("kim.a", "김가", "과장")
    member("lee.b", "이나", "대리")
    services = client.app.state.services
    with services.db() as db:
        row = db.get(HandoverSession, a_tasks[0]["session_id"])
        row.stage = "review"
        add_version(db, row, "# 인수인계서", origin="ai", ai_version=1)
        db.query(User).filter(User.login_id == "lee.b").update({"is_admin": True})
        db.commit()

    login(client, "lee.b", "password-123")
    teams = client.get("/api/admin/teams").json()["teams"]
    assert [(t["label"], t["member_count"], t["task_count"], t["document_count"]) for t in teams] == [("기획부 전략팀", 2, 4, 1)]
    report = client.get("/api/admin/teams/report", params={"department": "기획부", "team": "전략팀"}).json()
    assert report["summary"]["completion_rate"] == 0.25
    kim = next(m for m in report["members"] if m["login_id"] == "kim.a")
    assert (kim["task_count"], kim["document_count"], kim["stages"]["completed"], kim["ai_versions"]) == (2, 1, 1, 1)
    detail = client.get(f"/api/admin/members/{kim['user_id']}/report", params={"date_from": "2020-01-01"}).json()
    assert len(detail["tasks"]) == 2 and detail["activity"][0]["kind"] == "document_ai"
