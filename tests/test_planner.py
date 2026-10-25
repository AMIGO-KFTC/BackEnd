"""캘린더 일정·할 일: 로그인 계정마다 서버에 저장해 다른 브라우저에서도 같은 값을 본다."""

from fastapi.testclient import TestClient

PASSWORD = "correct-horse-1!"


def login(client, login_id="minsu.kim", signup=True):
    client.cookies.clear()
    if signup:
        assert client.post("/api/auth/signup", json={"login_id": login_id, "password": PASSWORD, "name": "김민수"}).status_code == 201
    assert client.post("/api/auth/login", json={"login_id": login_id, "password": PASSWORD}).status_code == 200


def test_login_required(client):
    assert client.get("/api/planner").status_code == 401
    assert client.post("/api/planner/events", json={"date": "2026-10-26", "text": "회의"}).status_code == 401
    assert client.post("/api/planner/todos", json={"text": "보고서"}).status_code == 401
    assert client.patch("/api/planner/todos/x", json={"done": True}).status_code == 401
    assert client.post("/api/planner/import", json={}).status_code == 401


def test_events_and_todos_round_trip(client):
    login(client)
    empty = client.get("/api/planner").json()
    assert empty["success"] is True and empty["events"] == [] and empty["todos"] == []

    res = client.post("/api/planner/events", json={"date": "2026-10-26", "text": "  주간 회의  "})
    assert res.status_code == 201
    event = res.json()["event"]
    assert event["id"] and event["date"] == "2026-10-26" and event["text"] == "주간 회의"
    client.post("/api/planner/events", json={"id": "ev-2", "date": "2026-10-26", "text": "보고"})

    todo = client.post("/api/planner/todos", json={"id": "td-1", "text": "인수인계서 검토", "date": "2026-10-27"}).json()["todo"]
    assert todo == {"id": "td-1", "text": "인수인계서 검토", "done": False, "date": "2026-10-27"}
    client.post("/api/planner/todos", json={"id": "td-2", "text": "날짜 없는 일"})

    planner = client.get("/api/planner").json()
    assert [e["text"] for e in planner["events"]] == ["주간 회의", "보고"]  # 추가한 순서
    assert [t["id"] for t in planner["todos"]] == ["td-1", "td-2"] and planner["todos"][1]["date"] is None

    # 보낸 필드만 바뀐다. date 를 null 로 보내면 날짜 없는 할 일
    done = client.patch("/api/planner/todos/td-1", json={"done": True}).json()["todo"]
    assert done["done"] is True and done["date"] == "2026-10-27" and done["text"] == "인수인계서 검토"
    moved = client.patch("/api/planner/todos/td-1", json={"date": None, "text": "검토 완료"}).json()["todo"]
    assert moved == {"id": "td-1", "text": "검토 완료", "done": True, "date": None}

    cleared = client.post("/api/planner/todos/clear-done").json()
    assert cleared["removed"] == 1
    assert client.delete("/api/planner/events/ev-2").status_code == 200
    assert client.delete("/api/planner/todos/td-2").status_code == 200
    assert client.delete("/api/planner/todos/td-2").status_code == 404

    planner = client.get("/api/planner").json()
    assert [e["text"] for e in planner["events"]] == ["주간 회의"] and planner["todos"] == []


def test_same_id_is_not_added_twice(client):
    login(client)
    first = client.post("/api/planner/todos", json={"id": "td-1", "text": "한 번만"})
    again = client.post("/api/planner/todos", json={"id": "td-1", "text": "한 번만"})
    assert first.status_code == 201 and again.status_code == 200 and again.json()["todo"]["id"] == "td-1"
    assert client.post("/api/planner/events", json={"id": "ev-1", "date": "2026-10-26", "text": "회의"}).status_code == 201
    assert client.post("/api/planner/events", json={"id": "ev-1", "date": "2026-10-26", "text": "회의"}).status_code == 200
    planner = client.get("/api/planner").json()
    assert len(planner["todos"]) == 1 and len(planner["events"]) == 1


def test_validation(client):
    login(client)
    assert client.post("/api/planner/events", json={"date": "2026-13-01", "text": "회의"}).status_code == 422
    assert client.post("/api/planner/events", json={"date": "26-10-01", "text": "회의"}).status_code == 422
    assert client.post("/api/planner/events", json={"date": "2026-10-26", "text": "   "}).status_code == 422
    assert client.post("/api/planner/todos", json={"text": "x" * 201}).status_code == 422
    assert client.post("/api/planner/todos", json={"id": "bad id!", "text": "할 일"}).status_code == 422
    client.post("/api/planner/todos", json={"id": "td-1", "text": "할 일"})
    assert client.patch("/api/planner/todos/td-1", json={"date": "2026-02-30"}).status_code == 422
    assert client.patch("/api/planner/todos/nope", json={"done": True}).status_code == 404


def test_accounts_are_separate(client):
    login(client, "minsu.kim")
    client.post("/api/planner/events", json={"id": "shared-id", "date": "2026-10-26", "text": "민수 일정"})
    client.post("/api/planner/todos", json={"id": "shared-id", "text": "민수 할 일"})

    login(client, "seoyeon.lee")
    assert client.get("/api/planner").json()["events"] == []
    # 다른 계정의 id 와 겹쳐도 각자 따로 저장되고, 남의 것은 고치거나 지울 수 없다
    assert client.post("/api/planner/todos", json={"id": "shared-id", "text": "서연 할 일"}).status_code == 201
    assert client.delete("/api/planner/events/shared-id").status_code == 404

    login(client, "minsu.kim", signup=False)
    planner = client.get("/api/planner").json()
    assert [e["text"] for e in planner["events"]] == ["민수 일정"] and [t["text"] for t in planner["todos"]] == ["민수 할 일"]


def test_another_browser_sees_the_same_planner(client):
    """같은 계정으로 다른 브라우저(쿠키가 따로인 클라이언트)에서 로그인하면 같은 일정·할 일을 본다."""
    login(client)
    client.post("/api/planner/events", json={"date": "2026-10-28", "text": "정기인사 발령"})
    client.post("/api/planner/todos", json={"text": "자료 정리", "date": "2026-10-27"})

    other = TestClient(client.app)
    assert other.post("/api/auth/login", json={"login_id": "minsu.kim", "password": PASSWORD}).status_code == 200
    planner = other.get("/api/planner").json()
    assert [e["text"] for e in planner["events"]] == ["정기인사 발령"]
    assert [(t["text"], t["date"]) for t in planner["todos"]] == [("자료 정리", "2026-10-27")]


def test_import_moves_local_data_once(client):
    login(client)
    client.post("/api/planner/todos", json={"id": "td-1", "text": "이미 서버에 있음"})
    body = {
        "events": [{"id": "local-1", "date": "2026-10-26", "text": "회의"}, {"id": "local-2", "date": "2026-10-26", "text": "회의"}],
        "todos": [{"id": "td-1", "text": "이미 서버에 있음"}, {"id": "td-2", "text": "끝낸 일", "done": True, "date": "2026-10-20"}],
    }
    res = client.post("/api/planner/import", json=body).json()
    assert res["message"] == "3개를 옮겼습니다."
    assert [e["id"] for e in res["events"]] == ["local-1", "local-2"]  # 같은 날 같은 내용도 id 가 다르면 둘 다
    assert [(t["id"], t["done"]) for t in res["todos"]] == [("td-1", False), ("td-2", True)]

    # 다시 보내도(새로고침 중 끊겨 재시도) 두 번 쌓이지 않는다
    again = client.post("/api/planner/import", json=body).json()
    assert again["message"] == "옮길 새 항목이 없습니다." and len(again["events"]) == 2 and len(again["todos"]) == 2


def test_deleting_account_removes_planner(client):
    from sqlalchemy import func, select

    from app.models import PlannerEvent, PlannerTodo, User

    login(client)
    client.post("/api/planner/events", json={"date": "2026-10-26", "text": "회의"})
    client.post("/api/planner/todos", json={"text": "할 일"})
    services = client.app.state.services
    with services.db() as db:
        db.delete(db.scalar(select(User).where(User.login_id == "minsu.kim")))
        db.commit()
        assert db.scalar(select(func.count(PlannerEvent.id))) == 0 and db.scalar(select(func.count(PlannerTodo.id))) == 0
