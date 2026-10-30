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
    assert client.post("/api/planner/onetel/import").status_code == 401
    assert client.get("/api/planner/onetel/events").status_code == 401


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
    assert todo == {"id": "td-1", "text": "인수인계서 검토", "done": False, "date": "2026-10-27", "memo": "", "history": []}
    client.post("/api/planner/todos", json={"id": "td-2", "text": "날짜 없는 일"})

    planner = client.get("/api/planner").json()
    assert [e["text"] for e in planner["events"]] == ["주간 회의", "보고"]  # 추가한 순서
    assert [t["id"] for t in planner["todos"]] == ["td-1", "td-2"] and planner["todos"][1]["date"] is None

    # 보낸 필드만 바뀐다. date 를 null 로 보내면 날짜 없는 할 일
    done = client.patch("/api/planner/todos/td-1", json={"done": True}).json()["todo"]
    assert done["done"] is True and done["date"] == "2026-10-27" and done["text"] == "인수인계서 검토"
    moved = client.patch("/api/planner/todos/td-1", json={"date": None, "text": "검토 완료"}).json()["todo"]
    assert moved == {"id": "td-1", "text": "검토 완료", "done": True, "date": None, "memo": "", "history": []}

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


def test_week_tasks_round_trip(client):
    login(client)
    res = client.post("/api/planner/week-tasks", json={"id": "w1", "week": "2026-10-28", "text": "주간 보고서 작성"})
    assert res.status_code == 201 and res.json()["task"]["week"] == "2026-10-25"  # 수요일을 보내도 그 주 일요일로 맞춘다
    assert client.post("/api/planner/week-tasks", json={"id": "w1", "week": "2026-10-25", "text": "주간 보고서 작성"}).status_code == 200
    assert client.post("/api/planner/week-tasks", json={"week": "2026-10-25", "text": ""}).status_code == 422
    assert client.get("/api/planner").json()["week_tasks"] == [{"id": "w1", "week": "2026-10-25", "text": "주간 보고서 작성", "memo": "", "done": False}]
    assert client.delete("/api/planner/week-tasks/w1").status_code == 200
    assert client.delete("/api/planner/week-tasks/w1").status_code == 404
    assert client.get("/api/planner").json()["week_tasks"] == []


def test_memo_on_event_week_task_and_todo(client):
    """일정·주간 업무·할 일에 짧은 메모를 남기고 지운다."""
    login(client)
    client.post("/api/planner/events", json={"id": "e1", "date": "2026-10-28", "text": "VPN 점검"})
    client.post("/api/planner/week-tasks", json={"id": "w1", "week": "2026-10-25", "text": "주간보고"})
    client.post("/api/planner/todos", json={"id": "t1", "text": "견적 확인"})
    assert client.patch("/api/planner/events/e1", json={"memo": "업체 오지훈 대리 동행"}).json()["event"]["memo"] == "업체 오지훈 대리 동행"
    assert client.patch("/api/planner/week-tasks/w1", json={"memo": "금요일 오전까지"}).json()["task"]["memo"] == "금요일 오전까지"
    assert client.patch("/api/planner/todos/t1", json={"memo": "재무팀 양식"}).json()["todo"]["memo"] == "재무팀 양식"
    data = client.get("/api/planner").json()
    assert [e["memo"] for e in data["events"]] == ["업체 오지훈 대리 동행"] and data["week_tasks"][0]["memo"] == "금요일 오전까지" and data["todos"][0]["memo"] == "재무팀 양식"
    assert client.patch("/api/planner/todos/t1", json={"memo": ""}).json()["todo"]["memo"] == ""
    assert client.patch("/api/planner/events/nope", json={"memo": "x"}).status_code == 404
    assert client.patch("/api/planner/events/e1", json={"done": True}).json()["event"]["done"] is True  # 체크리스트
    assert client.patch("/api/planner/week-tasks/w1", json={"done": True}).json()["task"]["done"] is True
    assert client.get("/api/planner").json()["events"][0]["done"] is True


def test_onetel_import_adds_sample_events_once_per_account(client):
    login(client)
    first = client.post("/api/planner/onetel/import")
    assert first.status_code == 200
    body = first.json()
    assert body["success"] is True and body["events"] == 7 and body["skipped"] == 0 and "7건" in body["message"]
    events = client.get("/api/planner").json()["events"]
    assert len(events) == 7 and all(e["text"].startswith("[원텔] ") for e in events)
    assert any("오픈지로 회비 정산 마감" in e["text"] for e in events)

    again = client.post("/api/planner/onetel/import").json()  # 다시 눌러도 두 번 쌓이지 않는다
    assert again["events"] == 0 and again["skipped"] == 7 and "이미" in again["message"]
    assert len(client.get("/api/planner").json()["events"]) == 7

    login(client, "other.user")  # 다른 계정은 자기 캘린더에 따로 받는다
    assert client.post("/api/planner/onetel/import").json()["events"] == 7


def test_onetel_modal_lists_events_and_imports_only_the_chosen_ones(client):
    login(client)
    listed = client.get("/api/planner/onetel/events").json()
    assert listed["success"] is True and len(listed["events"]) == 7 and not any(e["imported"] for e in listed["events"])
    assert all(e["id"] and e["date"] and e["text"].startswith("[원텔] ") for e in listed["events"])

    chosen = [listed["events"][0]["id"], listed["events"][2]["id"]]
    body = client.post("/api/planner/onetel/import", json={"ids": chosen}).json()
    assert body["events"] == 2 and body["skipped"] == 0
    assert [e["imported"] for e in client.get("/api/planner/onetel/events").json()["events"]] == [True, False, True, False, False, False, False]
    assert len(client.get("/api/planner").json()["events"]) == 2  # 고른 것만 들어온다

    again = client.post("/api/planner/onetel/import", json={"ids": chosen}).json()  # 고른 것이 이미 있으면 건너뜀
    assert again["events"] == 0 and again["skipped"] == 2
    assert client.post("/api/planner/onetel/import", json={"ids": []}).json()["message"] == "가져올 일정을 골라 주세요."
    assert client.post("/api/planner/onetel/import", json={"ids": ["nope"]}).status_code == 404
    rest = client.post("/api/planner/onetel/import", json={"ids": None}).json()  # ids 를 비우면(null) 나머지 모두
    assert rest["events"] == 5 and rest["skipped"] == 2


def test_todo_history(client):
    login(client)
    client.post("/api/planner/todos", json={"id": "td-h", "text": "실적 취합", "date": "2026-10-23"})
    first = client.post("/api/planner/todos/td-h/history", json={"text": "  유관부서 실적 확인 진행 중  "})
    assert first.status_code == 201
    body = first.json()
    assert body["entry"]["text"] == "유관부서 실적 확인 진행 중" and body["entry"]["id"] and body["entry"]["created_at"]
    assert [h["text"] for h in body["todo"]["history"]] == ["유관부서 실적 확인 진행 중"]
    client.post("/api/planner/todos/td-h/history", json={"text": "자료 취합 완료"})
    todo = client.get("/api/planner").json()["todos"][0]
    assert [h["text"] for h in todo["history"]] == ["유관부서 실적 확인 진행 중", "자료 취합 완료"]  # 오래된 것부터
    # 날짜를 바꿔도 이력은 남는다
    moved = client.patch("/api/planner/todos/td-h", json={"date": "2026-10-30"}).json()["todo"]
    assert len(moved["history"]) == 2


def test_todo_history_validation_and_owner(client):
    login(client)
    client.post("/api/planner/todos", json={"id": "td-h", "text": "실적 취합"})
    assert client.post("/api/planner/todos/td-h/history", json={"text": "   "}).status_code == 422
    assert client.post("/api/planner/todos/td-h/history", json={"text": "가" * 501}).status_code == 422
    assert client.post("/api/planner/todos/td-h/history", json={"text": "가" * 500}).status_code == 201
    assert client.post("/api/planner/todos/none/history", json={"text": "x"}).status_code == 404
    login(client, "other.user")
    assert client.post("/api/planner/todos/td-h/history", json={"text": "남의 것"}).status_code == 404
    assert client.get("/api/planner").json()["todos"] == []


def test_todo_history_limit(client):
    login(client)
    client.post("/api/planner/todos", json={"id": "td-h", "text": "실적 취합"})
    for i in range(50):
        assert client.post("/api/planner/todos/td-h/history", json={"text": f"진행 {i}"}).status_code == 201
    over = client.post("/api/planner/todos/td-h/history", json={"text": "하나 더"})
    assert over.status_code == 409 and "50" in over.json()["detail"]


def test_todo_history_edit_and_delete(client):
    login(client)
    client.post("/api/planner/todos", json={"id": "td-h", "text": "실적 취합"})
    first = client.post("/api/planner/todos/td-h/history", json={"text": "자료 취합 중"}).json()["entry"]
    second = client.post("/api/planner/todos/td-h/history", json={"text": "유관부서 회신 대기"}).json()["entry"]

    edited = client.patch(f"/api/planner/todos/td-h/history/{first['id']}", json={"text": "  자료 취합 완료  "})
    assert edited.status_code == 200
    body = edited.json()
    assert body["entry"] == {"id": first["id"], "text": "자료 취합 완료", "created_at": first["created_at"]}  # 적은 시각은 그대로
    assert [h["text"] for h in body["todo"]["history"]] == ["자료 취합 완료", "유관부서 회신 대기"]  # 순서도 그대로

    assert client.patch(f"/api/planner/todos/td-h/history/{first['id']}", json={"text": "   "}).status_code == 422
    assert client.patch("/api/planner/todos/td-h/history/nope", json={"text": "x"}).status_code == 404
    assert client.patch(f"/api/planner/todos/none/history/{first['id']}", json={"text": "x"}).status_code == 404

    assert client.delete(f"/api/planner/todos/td-h/history/{second['id']}").status_code == 200
    assert client.delete(f"/api/planner/todos/td-h/history/{second['id']}").status_code == 404  # 이미 지웠다
    assert [h["text"] for h in client.get("/api/planner").json()["todos"][0]["history"]] == ["자료 취합 완료"]

    login(client, "other.user")  # 남의 이력은 고치거나 지울 수 없다
    assert client.patch(f"/api/planner/todos/td-h/history/{first['id']}", json={"text": "남의 것"}).status_code == 404
    assert client.delete(f"/api/planner/todos/td-h/history/{first['id']}").status_code == 404


def test_onetel_event_works_like_a_regular_item(client):
    """원텔에서 가져온 일정도 마감일(날짜)을 바꾸고 진행 이력을 남기고 고치고 지울 수 있다."""
    login(client)
    chosen = client.get("/api/planner/onetel/events").json()["events"][0]
    client.post("/api/planner/onetel/import", json={"ids": [chosen["id"]]})
    event = client.get("/api/planner").json()["events"][0]
    assert event["id"] == chosen["id"] and event["text"].startswith("[원텔]") and event["history"] == []

    moved = client.patch(f"/api/planner/events/{event['id']}", json={"date": "2026-12-24"}).json()["event"]
    assert moved["date"] == "2026-12-24" and moved["text"] == event["text"]
    assert client.patch(f"/api/planner/events/{event['id']}", json={"date": "내일"}).status_code == 422
    assert client.patch(f"/api/planner/events/{event['id']}", json={"date": None}).json()["event"]["date"] == "2026-12-24"  # null 은 '바꾸지 않음'

    entry = client.post(f"/api/planner/events/{event['id']}/history", json={"text": "참석자 확인"})
    assert entry.status_code == 201
    entry_id = entry.json()["entry"]["id"]
    edited = client.patch(f"/api/planner/events/{event['id']}/history/{entry_id}", json={"text": "참석자 확인 완료"}).json()
    assert [h["text"] for h in edited["event"]["history"]] == ["참석자 확인 완료"]
    assert [h["text"] for h in client.get("/api/planner").json()["events"][0]["history"]] == ["참석자 확인 완료"]
    assert client.delete(f"/api/planner/events/{event['id']}/history/{entry_id}").status_code == 200
    assert client.get("/api/planner").json()["events"][0]["history"] == []
    assert client.post("/api/planner/events/none/history", json={"text": "x"}).status_code == 404
