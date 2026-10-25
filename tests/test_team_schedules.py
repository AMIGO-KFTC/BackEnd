"""팀 공유 일정: 팀장이 등록, 언제나 팀 단위라 구성원은 자기 팀 일정만 본다(정기인사 일정과 같은 규칙)."""

from tests.test_personnel import TEAM_AI, TEAM_IT, login, signup


def test_admin_shares_and_only_own_team_sees_them(client):
    signup(client, "it.lead", "김미고", TEAM_IT, admin=True)
    assert client.get("/api/team-schedules").json()["schedules"] == []

    res = client.post("/api/admin/team-schedules", json={"title": "하반기 팀 워크숍", "start_date": "2026-11-12", "end_date": "2026-11-13", "memo": "대전 연수원"})
    assert res.status_code == 201
    workshop = res.json()["schedule"]
    assert workshop["organization"] == "디지털본부 IT아미고팀" and workshop["end_date"] == "2026-11-13"
    same_day = client.post("/api/admin/team-schedules", json={"title": "주간 회의", "start_date": "2026-11-02", "end_date": "2026-11-02"}).json()["schedule"]
    assert same_day["end_date"] is None and same_day["memo"] == ""  # 시작일과 같은 끝나는 날은 하루짜리로 저장
    client.post("/api/admin/team-schedules", json={"title": "보안 점검", "start_date": "2026-12-01", "scope": "all"})  # 예전 scope=all 도 팀 일정

    mine = client.get("/api/team-schedules").json()
    assert mine["editable"] is True
    assert [s["title"] for s in mine["schedules"]] == ["주간 회의", "하반기 팀 워크숍", "보안 점검"]  # 시작일 순

    signup(client, "it.member", "최환", TEAM_IT)
    member = client.get("/api/team-schedules").json()
    assert member["editable"] is False and len(member["schedules"]) == 3
    assert client.post("/api/admin/team-schedules", json={"title": "x", "start_date": "2027-01-01"}).status_code == 403

    signup(client, "ai.member", "임윤희", TEAM_AI)
    assert client.get("/api/team-schedules").json()["schedules"] == []


def test_update_delete_and_validation(client):
    signup(client, "it.lead", "김미고", TEAM_IT, admin=True)
    sid = client.post("/api/admin/team-schedules", json={"title": "팀 회식", "start_date": "2026-11-20"}).json()["schedule"]["id"]
    res = client.put(f"/api/admin/team-schedules/{sid}", json={"title": "팀 회식(연기)", "start_date": "2026-11-27", "memo": "저녁 6시"})
    assert res.status_code == 200 and res.json()["schedule"]["start_date"] == "2026-11-27" and res.json()["schedule"]["memo"] == "저녁 6시"

    for body in [{"title": "", "start_date": "2027-01-15"}, {"title": "날짜 없음", "start_date": ""}, {"title": "순서", "start_date": "2027-01-15", "end_date": "2027-01-10"}]:
        assert client.post("/api/admin/team-schedules", json=body).status_code == 422, body

    signup(client, "ai.lead", "최고미", TEAM_AI, admin=True)
    assert client.put(f"/api/admin/team-schedules/{sid}", json={"title": "x", "start_date": "2027-01-01"}).status_code == 404
    assert client.delete(f"/api/admin/team-schedules/{sid}").status_code == 404

    login(client, "it.lead")
    assert client.delete(f"/api/admin/team-schedules/{sid}").json()["message"] == "'팀 회식(연기)' 일정을 지웠습니다."
    client.cookies.clear()
    assert client.get("/api/team-schedules").status_code == 401
