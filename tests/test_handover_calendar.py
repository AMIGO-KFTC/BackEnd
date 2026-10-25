"""받은 인수인계서 적용하기: 날짜가 정해진 것은 캘린더 일정으로, 반복하는 일은 '정기 업무' 칸으로(app/services/handover_calendar.py)."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.handover_calendar import parse_schedule, routine_months

TODAY = date(2026, 10, 25)  # 일요일


def parse(text, recurring=True):
    p = parse_schedule(text, TODAY, recurring=recurring)
    return [(d.isoformat(), label) for d, label in p.dates], p.cadence


def test_recurring_goes_to_routines_not_calendar():
    assert parse("매월 5영업일 이내") == ([], "monthly")
    assert parse("매월 말일까지 보고") == ([], "monthly")
    assert parse("매주 월요일 오전") == ([], "weekly")
    assert parse("격주 금요일") == ([], "weekly")
    assert parse("매일 오전 9시") == ([], "daily")
    assert parse("분기별(1·4·7·10월)") == ([], "quarterly")
    assert parse("상반기") == ([], "quarterly")  # 분기·반기는 한 칸
    assert parse("매년 3월 31일") == ([("2027-03-31", "")], "yearly")  # 매년이어도 날짜가 있으면 캘린더에도
    assert parse("연 1회 정기 점검") == ([], "yearly")
    assert parse("필요 시 처리") == ([], "other")
    assert parse("담당자와 협의해서") == ([], "other")
    assert parse("3월 중") == ([], "yearly")  # 정기업무 장에서 'N월 중'은 매년


def test_routine_months():
    assert routine_months("quarterly", "분기 말") == [3, 6, 9, 12]
    assert routine_months("quarterly", "분기별(1·4·7·10월)") == [1, 4, 7, 10]
    assert routine_months("quarterly", "매 분기 종료 후 15일 이내") == [1, 4, 7, 10]
    assert routine_months("quarterly", "반기") == [6, 12]
    assert routine_months("quarterly", "상반기") == [6]
    assert routine_months("yearly", "매년 3·9월") == [3, 9]
    assert routine_months("yearly", "매년 12월 초(현재 인증서 만료 2026-12-15)") == [12]
    assert routine_months("yearly", "연 1회") == []
    assert routine_months("monthly", "매월 10일") == []


def test_dated_items_go_to_calendar():
    assert parse("11월 14일까지", recurring=False) == ([("2026-11-14", "마감")], None)
    assert parse("11/14까지 검수 완료", recurring=False) == ([("2026-11-14", "마감")], None)
    assert parse("2027년 1월 5일 마감", recurring=False) == ([("2027-01-05", "마감")], None)
    assert parse("2026.11.01~2027.02.28", recurring=False) == ([("2026-11-01", "시작"), ("2027-02-28", "종료")], None)
    assert parse("2026.03~2026.12", recurring=False) == ([("2026-12-31", "종료")], None)  # 지난 시작일은 넣지 않는다
    assert parse("2025-03-01 완료", recurring=False) == ([], "other")  # 지난 날짜뿐이면 수시·기타로 남긴다
    # 반복 + 날짜가 같이 있으면 둘 다
    assert parse("매년 12월 초(현재 인증서 만료 2026-12-15)") == ([("2026-12-15", "마감")], "yearly")
    assert parse("해당 없음") == ([], None)


@pytest.fixture()
def mock_client(tmp_path):
    settings = Settings(data_dir=tmp_path / "real-data", mock_mode=True, mock_delay=0, workers=4)
    with TestClient(create_app(settings)) as c:
        yield c


def login(client, login_id, password="demo1234"):
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"login_id": login_id, "password": password}).status_code == 200


def task_session(client, name) -> str:
    return next(t for t in client.get("/api/unit-tasks").json()["tasks"] if t["name"] == name)["session_id"]


def test_transfer_fills_recipient_calendar_and_routines_once(mock_client):
    login(mock_client, "demo")
    sid = task_session(mock_client, "기관 홈페이지 운영")
    body = mock_client.post(f"/api/sessions/{sid}/transfers", json={"recipient_name": "이서연"}).json()
    assert body["calendar_routines"] > 0 and body["calendar_message"].startswith("이서연님 캘린더에")
    assert mock_client.get("/api/planner").json()["routines"] == []  # 보낸 사람 캘린더는 그대로

    login(mock_client, "seoyeon.lee")
    planner = mock_client.get("/api/planner").json()
    routines = {(r["cadence"], r["name"]): r for r in planner["routines"]}
    assert any(c == "weekly" and r["timing"] == "매주 월요일 오전" for (c, _), r in routines.items())
    assert any(c == "monthly" and r["timing"] == "매월 5영업일 이내" for (c, _), r in routines.items())
    assert all(r["source"] == "기관 홈페이지 운영" for r in planner["routines"])
    assert planner["todos"] == []  # 할 일은 건드리지 않는다
    assert all(e["text"].startswith("[인수] ") for e in planner["events"])
    assert len(planner["events"]) == body["calendar_events"] and len(planner["routines"]) == body["calendar_routines"]
    assert any(r["cadence"] == "quarterly" and r["months"] == [1, 4, 7, 10] for r in planner["routines"])  # '분기별(1·4·7·10월)'
    assert any(r["cadence"] == "yearly" and r["months"] == [12] for r in planner["routines"])  # '매년 12월 초'

    # 받은 인수인계서를 다시 적용해도 두 번 쌓이지 않는다
    again = mock_client.post(f"/api/handovers/received/{sid}/calendar").json()
    assert (again["events"], again["routines"]) == (0, 0) and again["message"] == "이 인수인계서의 일정은 이미 캘린더에 있습니다."

    # 지운 정기 업무는 다시 적용하면 돌아온다
    first = planner["routines"][0]["id"]
    assert mock_client.delete(f"/api/planner/routines/{first}").status_code == 200
    assert mock_client.delete(f"/api/planner/routines/{first}").status_code == 404
    assert mock_client.post(f"/api/handovers/received/{sid}/calendar").json()["routines"] == 1


def test_only_recipient_can_apply(mock_client):
    login(mock_client, "demo")
    sid = task_session(mock_client, "기관 홈페이지 운영")
    assert mock_client.post(f"/api/handovers/received/{sid}/calendar").status_code == 404  # 인계자 본인은 받은 사람이 아니다
    assert mock_client.post("/api/handovers/received/nope/calendar").status_code == 404
