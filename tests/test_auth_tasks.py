"""로그인 → 프로필 → 단위업무 → 업무파일 업로드(→ DB 저장 → AI 자동 분석) 흐름."""

import hashlib

from conftest import idle, wait_for
from sqlalchemy import select

from app.models import AuthSession, FileBlob, User

ACCOUNT = {"login_id": "minsu.kim", "password": "correct-horse-1", "name": "김민수"}
PROFILE = {"department": "디지털전략부", "team": "웹서비스팀", "position": "과장", "name": "김민수"}


def signup_and_login(client, account=ACCOUNT) -> str:
    assert client.post("/api/auth/signup", json=account).status_code == 201
    res = client.post("/api/auth/login", json={"login_id": account["login_id"], "password": account["password"]})
    assert res.status_code == 200
    return res.json()["token"]


def test_signup_login_logout(client):
    res = client.post("/api/auth/signup", json=ACCOUNT)
    body = res.json()
    assert res.status_code == 201 and body["success"] is True and body["user"]["name"] == "김민수"
    assert "password" not in str(body) and "hash" not in str(body)
    dup = client.post("/api/auth/signup", json={**ACCOUNT, "login_id": "MINSU.KIM"})  # 대소문자만 다른 아이디도 중복
    assert dup.status_code == 409 and dup.json() == {"success": False, "message": "이미 사용 중인 아이디입니다.", "detail": "이미 사용 중인 아이디입니다."}

    short = client.post("/api/auth/signup", json={**ACCOUNT, "login_id": "other", "password": "short"})
    assert short.status_code == 422 and short.json()["success"] is False and "비밀번호" in short.json()["message"]

    # 실패: 틀린 비밀번호와 없는 아이디는 같은 메시지
    wrong = client.post("/api/auth/login", json={"login_id": "minsu.kim", "password": "wrong-password"})
    unknown = client.post("/api/auth/login", json={"login_id": "nobody", "password": "wrong-password"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["message"] == unknown.json()["message"] == "아이디 또는 비밀번호가 올바르지 않습니다."
    assert client.get("/api/auth/me").status_code == 401

    # 성공: 이름과 세션(쿠키 + 토큰)
    res = client.post("/api/auth/login", json={"login_id": " Minsu.Kim ", "password": "correct-horse-1"})
    body = res.json()
    assert res.status_code == 200 and body["success"] is True and body["name"] == "김민수" and body["message"] == "김민수님, 환영합니다."
    cookie = res.headers["set-cookie"]
    assert "amigo_session=" in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    assert client.get("/api/auth/me").json()["user"]["login_id"] == "minsu.kim"  # 쿠키로 인증

    # DB 에는 토큰 원문·비밀번호 원문이 없다
    services = client.app.state.services
    with services.db() as db:
        user = db.scalar(select(User))
        assert user.password_hash.startswith("scrypt$") and "correct-horse-1" not in user.password_hash
        stored = db.scalars(select(AuthSession.token_hash)).all()
        assert hashlib.sha256(body["token"].encode()).hexdigest() in stored and body["token"] not in stored

    # 쿠키 없이 Bearer 토큰으로도 인증
    client.cookies.clear()
    headers = {"Authorization": f"Bearer {body['token']}"}
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    assert client.post("/api/auth/logout", headers=headers).json()["success"] is True
    assert client.get("/api/auth/me", headers=headers).status_code == 401  # 로그아웃한 세션은 무효


def test_expired_session_is_rejected(client, settings):
    token = signup_and_login(client)
    services = client.app.state.services
    with services.db() as db:
        auth = db.scalar(select(AuthSession))
        auth.expires_at = auth.created_at.replace(year=2000)
        db.commit()
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_profile_save_and_validation(client):
    assert client.put("/api/profile", json=PROFILE).status_code == 401
    signup_and_login(client)
    assert client.get("/api/profile").status_code == 404

    missing = client.put("/api/profile", json={**PROFILE, "department": " "})
    assert missing.status_code == 422 and missing.json()["message"] == "입력값을 확인해 주세요: 부서"

    res = client.put("/api/profile", json={**PROFILE, "name": "김민수 "})
    assert res.status_code == 200
    assert res.json() == {"success": True, "message": "프로필이 저장되었습니다.", "profile": PROFILE}
    assert client.get("/api/profile").json()["profile"] == PROFILE

    updated = client.put("/api/profile", json={**PROFILE, "position": "차장", "name": "김민준"}).json()
    assert updated["profile"]["position"] == "차장"
    me = client.get("/api/auth/me").json()["user"]
    assert me["name"] == "김민준" and me["profile"]["position"] == "차장"


def test_unit_tasks_crud(client):
    signup_and_login(client)
    tasks = {"tasks": [{"name": "홈페이지 운영", "description": "기관 홈페이지 콘텐츠 관리"}, {"name": "웹 접근성 관리"}]}
    need_profile = client.post("/api/unit-tasks", json=tasks)
    assert need_profile.status_code == 400 and "프로필" in need_profile.json()["message"]

    client.put("/api/profile", json=PROFILE)
    res = client.post("/api/unit-tasks", json=tasks)
    body = res.json()
    assert res.status_code == 201 and body["success"] is True and body["message"] == "단위업무 2건을 등록했습니다."
    first, second = body["tasks"]
    assert [first["sort_order"], second["sort_order"]] == [1, 2]
    assert first["session_id"] != second["session_id"] and first["stage"] == "setup" and first["file_count"] == 0

    # 단위업무마다 인계자 프로필이 반영된 인수인계 세션이 따로 있다
    session = client.get(f"/api/sessions/{first['session_id']}").json()
    assert session["title"] == "홈페이지 운영 인수인계" and session["owner_name"] == "김민수"
    assert session["organization"] == "디지털전략부 웹서비스팀" and session["duties"] == "홈페이지 운영: 기관 홈페이지 콘텐츠 관리"

    assert client.post("/api/unit-tasks", json={"tasks": [{"name": "홈페이지 운영"}]}).status_code == 409
    dup = client.post("/api/unit-tasks", json={"tasks": [{"name": "A"}, {"name": "A"}]})
    assert dup.status_code == 400 and "중복" in dup.json()["message"]
    empty = client.post("/api/unit-tasks", json={"tasks": [{"name": "  "}]})
    assert empty.status_code == 422 and empty.json()["message"] == "입력값을 확인해 주세요: 단위업무명"

    renamed = client.patch(f"/api/unit-tasks/{second['id']}", json={"name": "접근성 점검"}).json()
    assert renamed["task"]["name"] == "접근성 점검"
    assert client.get(f"/api/sessions/{second['session_id']}").json()["title"] == "접근성 점검 인수인계"
    assert client.patch(f"/api/unit-tasks/{second['id']}", json={"name": "홈페이지 운영"}).status_code == 409

    # 프로필을 고치면 단위업무 세션의 인계자 정보도 바뀐다
    client.put("/api/profile", json={**PROFILE, "position": "차장"})
    assert client.get(f"/api/sessions/{first['session_id']}").json()["position"] == "차장"

    assert [t["name"] for t in client.get("/api/unit-tasks").json()["tasks"]] == ["홈페이지 운영", "접근성 점검"]
    deleted = client.delete(f"/api/unit-tasks/{second['id']}")
    assert deleted.status_code == 200 and deleted.json()["success"] is True
    assert client.get(f"/api/unit-tasks/{second['id']}").status_code == 404
    assert client.get(f"/api/sessions/{second['session_id']}").status_code == 404
    assert [t["name"] for t in client.get("/api/unit-tasks").json()["tasks"]] == ["홈페이지 운영"]


def test_upload_stores_files_in_db_and_starts_ai(client, sample_dir):
    signup_and_login(client)
    client.put("/api/profile", json=PROFILE)
    task = client.post("/api/unit-tasks", json={"tasks": [{"name": "홈페이지 운영"}]}).json()["tasks"][0]
    sid = task["session_id"]

    pdf = sample_dir / "업무정의서_웹서비스팀.pdf"
    hwpx = sample_dir / "홈페이지_운영매뉴얼.hwpx"
    files = [("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in (pdf, hwpx)]
    files.append(("files", ("악성.exe", b"MZ", "application/octet-stream")))
    res = client.post(f"/api/unit-tasks/{task['id']}/files", files=files)
    body = res.json()
    assert res.status_code == 201 and body["success"] is True and body["analysis_scheduled"] is True
    assert body["session_id"] == sid and "파일 2개를 저장했습니다. 1개는 저장하지 못했습니다(악성.exe)." in body["message"]

    # 원본이 DB 에 그대로 저장된다(실패한 파일은 저장하지 않음)
    by_name = {f["name"]: f for f in body["files"]}
    services = client.app.state.services
    with services.db() as db:
        blob = db.get(FileBlob, by_name[pdf.name]["id"])
        assert blob.content == pdf.read_bytes() and blob.sha256 == hashlib.sha256(pdf.read_bytes()).hexdigest()
        assert db.get(FileBlob, by_name["악성.exe"]["id"]) is None
    original = client.get(f"/api/unit-tasks/{task['id']}/files/{by_name[hwpx.name]['id']}")
    assert original.status_code == 200 and original.content == hwpx.read_bytes()
    assert "filename*=UTF-8''" in original.headers["content-disposition"]

    # '분석 시작'을 누르지 않아도 적재가 끝나면 AI 가 분석하고 첫 질문을 한다
    state = wait_for(client, sid, lambda s: idle(s) and s["status"] == "waiting")
    assert state["session"]["stage"] == "qna"
    assert state["messages"][-1]["kind"] == "question"
    assert client.get(f"/api/unit-tasks/{task['id']}").json()["task"]["file_count"] == 3

    # 질의응답은 기존 세션 API 를 그대로 쓴다
    assert client.post(f"/api/sessions/{sid}/chat", json={"text": "재무팀 박지훈 차장, 내선 2345"}).status_code == 202
    wait_for(client, sid, lambda s: s["status"] == "waiting")

    # 모두 거절되면 실패
    bad = client.post(f"/api/unit-tasks/{task['id']}/files", files=[("files", ("a.exe", b"MZ", "application/octet-stream"))])
    assert bad.status_code == 400 and bad.json()["success"] is False and bad.json()["analysis_scheduled"] is False


def test_other_users_cannot_access_tasks_or_sessions(client, sample_dir):
    signup_and_login(client)
    client.put("/api/profile", json=PROFILE)
    task = client.post("/api/unit-tasks", json={"tasks": [{"name": "홈페이지 운영"}]}).json()["tasks"][0]
    sid = task["session_id"]

    client.cookies.clear()
    # 로그인하지 않으면 단위업무 세션에 접근할 수 없다(X-User-Id 로 흉내 내도 마찬가지)
    assert client.get(f"/api/sessions/{sid}").status_code == 401
    assert client.get(f"/api/sessions/{sid}/state", headers={"X-User-Id": "user:1"}).status_code == 401
    assert client.get("/api/sessions", headers={"X-User-Id": "user:1"}).json() == []

    signup_and_login(client, {"login_id": "other", "password": "other-password", "name": "이서연"})
    assert client.get(f"/api/unit-tasks/{task['id']}").status_code == 404
    assert client.post(f"/api/unit-tasks/{task['id']}/files", files=[("files", ("a.txt", b"x", "text/plain"))]).status_code == 404
    assert client.get(f"/api/sessions/{sid}").status_code == 404
    assert client.post(f"/api/sessions/{sid}/chat", json={"text": "hi"}).status_code == 404
    assert client.get("/api/unit-tasks").json()["tasks"] == []
