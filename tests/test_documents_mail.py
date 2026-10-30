"""인수인계서 직접 수정·버전 기록과 메일 조회 API(가상 메일함)."""

import email
import time
from email import policy

import pytest
from conftest import PROFILE, idle, wait_for
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.main import create_app
from app.models import DocumentVersion, HandoverSession
from app.services.documents import backfill_versions
from app.services.exporters import strip_citation_marks


@pytest.fixture()
def mock_client(tmp_path):
    settings = Settings(data_dir=tmp_path / "real-data", mock_mode=True, mock_delay=0, workers=4)
    with TestClient(create_app(settings)) as c:
        c.post("/api/auth/login", json={"login_id": "demo", "password": "demo1234"})
        yield c


def task_by_name(client, name: str) -> dict:
    return next(t for t in client.get("/api/unit-tasks").json()["tasks"] if t["name"] == name)


def wait_session(client, sid, predicate, timeout=20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/sessions/{sid}").json()
        if predicate(s):
            return s
        time.sleep(0.05)
    raise AssertionError(f"timeout: {s['status']} / {s['stage']}")


def test_user_edits_are_saved_as_versions_and_reloaded(mock_client):
    sid = task_by_name(mock_client, "기관 홈페이지 운영")["session_id"]
    doc = mock_client.get(f"/api/sessions/{sid}/document").json()
    assert doc["version"] == 1 and doc["origin"] == "ai" and doc["version_count"] == 1
    original = doc["markdown"]

    # 화면에서 고친 문서를 저장하면 새 버전이 되고, 다시 불러오면 고친 문서가 온다
    edited = original + "\n\n> 인계자 메모: 11월 SSL 인증서 갱신을 꼭 챙겨 주세요.\n"
    res = mock_client.put(f"/api/sessions/{sid}/document", json={"markdown": edited, "base_version": 1, "note": "SSL 메모 추가"})
    body = res.json()
    assert res.status_code == 200 and body["saved"] is True and body["message"] == "인수인계서를 v2(으)로 저장했습니다."
    assert body["document"]["version"] == 2 and body["document"]["origin"] == "user" and body["document"]["based_on"] == 1
    assert body["document"]["author_name"] == "김민수" and body["document"]["note"] == "SSL 메모 추가"
    doc = mock_client.get(f"/api/sessions/{sid}/document").json()
    assert doc["markdown"] == edited and doc["version"] == 2 and doc["origin"] == "user" and doc["origin_label"] == "직접 수정"
    assert mock_client.get(f"/api/sessions/{sid}").json()["document_version"] == 2

    # 대화 기록에도 남는다
    last = mock_client.get(f"/api/sessions/{sid}/messages").json()[-1]
    assert last["role"] == "system" and "v2" in last["content"]

    # 다른 창에서 예전 버전을 고쳐 저장하면 덮어쓰지 않는다 / 같은 내용은 버전을 늘리지 않는다
    stale = mock_client.put(f"/api/sessions/{sid}/document", json={"markdown": original + "x", "base_version": 1})
    assert stale.status_code == 409 and "v2" in stale.json()["message"]
    same = mock_client.put(f"/api/sessions/{sid}/document", json={"markdown": edited, "base_version": 2}).json()
    assert same["saved"] is False and same["document"]["version"] == 2
    blank = mock_client.put(f"/api/sessions/{sid}/document", json={"markdown": "  \n", "base_version": 2})
    assert blank.status_code == 422 and blank.json()["message"] == "입력값을 확인해 주세요: 인수인계서 내용"

    # AI 작업 뒤 스냅숏 동기화가 직접 고친 문서를 되돌리지 않는다
    services = mock_client.app.state.services
    services.runner._sync_snapshot(sid)
    assert mock_client.get(f"/api/sessions/{sid}/document").json()["markdown"] == edited

    # 버전 기록 · 특정 버전 · 복원
    versions = mock_client.get(f"/api/sessions/{sid}/document/versions").json()
    assert versions["current_version"] == 2
    assert [(v["version"], v["origin"], v["is_current"]) for v in versions["versions"]] == [(2, "user", True), (1, "ai", False)]
    assert "markdown" not in versions["versions"][0]
    assert mock_client.get(f"/api/sessions/{sid}/document/versions/1").json()["markdown"] == original
    assert mock_client.get(f"/api/sessions/{sid}/document/versions/9").status_code == 404

    restored = mock_client.post(f"/api/sessions/{sid}/document/versions/1/restore", json={"base_version": 2}).json()
    assert restored["document"]["version"] == 3 and restored["document"]["origin"] == "restore" and restored["document"]["based_on"] == 1
    assert mock_client.get(f"/api/sessions/{sid}/document").json()["markdown"] == original
    assert mock_client.post(f"/api/sessions/{sid}/document/versions/3/restore").status_code == 400

    # 다운로드: 현재 문서 또는 특정 버전
    assert mock_client.get(f"/api/sessions/{sid}/download?format=md").content.decode() == strip_citation_marks(original)
    old = mock_client.get(f"/api/sessions/{sid}/download?format=md&version=2")
    assert old.content.decode() == strip_citation_marks(edited) and "v2.md" in old.headers["content-disposition"]
    assert mock_client.get(f"/api/sessions/{sid}/download?format=docx&version=2").status_code == 200

    # 채팅으로 고칠 점을 말하면 AI 가 새 초안을 쓰고, 번호는 이어서 매긴다
    assert mock_client.post(f"/api/sessions/{sid}/chat", json={"text": "비상 연락망도 넣어 주세요"}).status_code == 202
    wait_session(mock_client, sid, lambda s: s["document_version"] == 4 and s["status"] != "running")
    latest = mock_client.get(f"/api/sessions/{sid}/document/versions").json()["versions"][0]
    assert latest["version"] == 4 and latest["origin"] == "ai" and latest["ai_version"] == 2

    # 내 인수인계서 목록
    mine = mock_client.get("/api/documents").json()["documents"]
    assert [(d["task_name"], d["version"], d["version_count"]) for d in mine] == [("기관 홈페이지 운영", 4, 4)]
    assert mine[0]["task_id"] == task_by_name(mock_client, "기관 홈페이지 운영")["id"]

    # 단위업무를 지우면 버전 기록도 함께 지워진다
    assert mock_client.delete(f"/api/unit-tasks/{mine[0]['task_id']}").status_code == 200
    with services.db() as db:
        assert db.scalars(select(DocumentVersion).where(DocumentVersion.session_id == sid)).first() is None
    assert mock_client.get("/api/documents").json()["documents"] == []


def test_edit_requires_document_and_owner(mock_client):
    pending = task_by_name(mock_client, "유지보수 계약 관리")["session_id"]
    res = mock_client.put(f"/api/sessions/{pending}/document", json={"markdown": "# 문서", "base_version": 0})
    assert res.status_code == 404 and res.json()["message"] == "아직 생성된 인수인계서가 없습니다."
    assert mock_client.get(f"/api/sessions/{pending}/document/versions").json()["versions"] == []

    sid = task_by_name(mock_client, "기관 홈페이지 운영")["session_id"]
    mock_client.post("/api/auth/logout")
    assert mock_client.put(f"/api/sessions/{sid}/document", json={"markdown": "# x", "base_version": 1}).status_code == 401
    assert mock_client.get("/api/documents").status_code == 401


def test_existing_documents_are_backfilled(settings):
    with TestClient(create_app(settings)) as c:
        services = c.app.state.services
        with services.db() as db:  # 버전 기록이 생기기 전에 만든 문서
            db.add(HandoverSession(id="a" * 32, title="예전 문서", owner_name="김민수", document_md="# 예전 문서", document_version=3))
            db.commit()
        assert backfill_versions(services.SessionLocal) == 1
        assert backfill_versions(services.SessionLocal) == 0
        with services.db() as db:
            rows = list(db.scalars(select(DocumentVersion).where(DocumentVersion.session_id == "a" * 32)))
        assert [(v.version, v.origin, v.markdown) for v in rows] == [(3, "ai", "# 예전 문서")]
        doc = c.get(f"/api/sessions/{'a' * 32}/document").json()
        assert doc["version"] == 3 and doc["version_count"] == 1
        saved = c.put(f"/api/sessions/{'a' * 32}/document", json={"markdown": "# 고친 문서", "base_version": 3}).json()
        assert saved["document"]["version"] == 4


def test_mail_list_search_and_detail(mock_client):
    task = task_by_name(mock_client, "유지보수 계약 관리")
    res = mock_client.get(f"/api/mail/messages?task_id={task['id']}&size=5")
    body = res.json()
    assert res.status_code == 200 and body["provider"] == "mock" and body["mailbox"] == {"name": "김민수", "email": "demo@amigo.example"}
    assert body["total"] == 22 and len(body["messages"]) == 5 and body["has_next"] is True
    assert "유지보수" in body["keywords"] and "계약" in body["keywords"]
    first = body["messages"][0]
    assert first["recommended"] is True and "유지보수" in first["subject"] + first["snippet"]
    assert [m["relevance"] for m in body["messages"]] == sorted((m["relevance"] for m in body["messages"]), reverse=True)

    ssl = mock_client.get("/api/mail/messages?q=SSL 만료").json()
    assert [m["id"] for m in ssl["messages"]] == ["m-0012"] and ssl["messages"][0]["recommended"] is False
    sent = mock_client.get("/api/mail/messages?folder=sent").json()["messages"]
    assert {m["id"] for m in sent} == {"m-0006", "m-0014"} and all(m["sender"]["name"] == "김민수" for m in sent)
    with_files = mock_client.get("/api/mail/messages?has_attachments=true&size=50").json()["messages"]
    assert with_files and all(m["has_attachments"] for m in with_files)
    by_sender = mock_client.get("/api/mail/messages?sender=정보보호팀").json()["messages"]
    assert {m["id"] for m in by_sender} == {"m-0003", "m-0010", "m-0020"}
    recent = mock_client.get(f"/api/mail/messages?date_from={time.strftime('%Y-%m-%d', time.localtime(time.time() - 3 * 86400))}").json()
    assert 0 < recent["total"] < 22
    assert mock_client.get("/api/mail/messages?q=없는검색어").json()["message"] == "조건에 맞는 메일이 없습니다."

    detail = mock_client.get("/api/mail/messages/m-0001").json()
    assert detail["subject"].startswith("[요청] 9월 홈페이지 유지보수 대금") and "검수확인서" in detail["body"]
    assert detail["attachments"][0]["name"] == "유지보수_검수확인서_양식.txt" and detail["cc"][0]["name"] == "웹서비스팀"
    assert mock_client.get("/api/mail/messages/nope").status_code == 404
    assert mock_client.get("/api/mail/messages?task_id=nope").status_code == 404

    mock_client.post("/api/auth/logout")
    assert mock_client.get("/api/mail/messages").status_code == 401


def test_import_mails_into_unit_task_starts_analysis(mock_client):
    task = task_by_name(mock_client, "유지보수 계약 관리")
    sid = task["session_id"]
    res = mock_client.post(f"/api/unit-tasks/{task['id']}/mails", json={"message_ids": ["m-0009", "m-0001", "m-0009"]})
    body = res.json()
    assert res.status_code == 201 and body["success"] is True and body["analysis_scheduled"] is True
    assert body["message"] == "메일 2건을 업무 자료로 가져왔습니다. 자료 처리가 끝나면 AI 분석을 시작합니다."
    names = [f["name"] for f in body["files"]]
    assert all(n.startswith("메일_") and n.endswith(".eml") for n in names) and "유지보수 계약 갱신" in names[0]
    assert {f["link_type"] for f in body["files"]} == {"mail"} and body["files"][1]["url"] == "mail:m-0001"

    # 가져온 메일은 표준 .eml 원본으로 DB 에 저장된다(첨부 포함)
    raw = mock_client.get(f"/api/unit-tasks/{task['id']}/files/{body['files'][1]['id']}").content
    parsed = email.message_from_bytes(raw, policy=policy.default)
    assert parsed["Subject"].startswith("[요청] 9월 홈페이지 유지보수 대금") and "demo@amigo.example" in parsed["To"]
    assert [a.get_filename() for a in parsed.iter_attachments()] == ["유지보수_검수확인서_양식.txt"]

    # 업로드와 똑같이 적재가 끝나면 AI 분석이 시작된다
    wait_session(mock_client, sid, lambda s: s["stage"] != "setup" and s["status"] in ("waiting", "idle"))
    listed = mock_client.get(f"/api/mail/messages?task_id={task['id']}&size=50").json()["messages"]
    assert {m["id"] for m in listed if m["imported"]} == {"m-0009", "m-0001"}
    assert mock_client.get(f"/api/unit-tasks/{task['id']}").json()["task"]["file_count"] == 2

    again = mock_client.post(f"/api/unit-tasks/{task['id']}/mails", json={"message_ids": ["m-0001"]}).json()
    assert again["files"] == [] and again["skipped"] == ["[요청] 9월 홈페이지 유지보수 대금 지급 서류 제출"]
    assert "이미 가져온 메일이라 건너뛰었습니다" in again["message"]
    missing = mock_client.post(f"/api/unit-tasks/{task['id']}/mails", json={"message_ids": ["m-0002", "nope"]})
    assert missing.status_code == 404 and "nope" in missing.json()["message"]
    assert mock_client.post(f"/api/unit-tasks/{task['id']}/mails", json={"message_ids": []}).status_code == 422


def test_import_mails_into_session_and_rag_reads_them(client):
    """실제 RAG 가 가져온 메일(.eml)의 본문과 첨부를 읽는지."""
    client.post("/api/auth/signup", json={"login_id": "minsu.kim", "password": "correct-horse-1", "name": "김민수"})
    client.post("/api/auth/login", json={"login_id": "minsu.kim", "password": "correct-horse-1"})
    created = client.post("/api/sessions", json=PROFILE).json()
    sid = created["id"]
    res = client.post(f"/api/sessions/{sid}/mails", json={"message_ids": ["m-0002"]})
    assert res.status_code == 201 and res.json()["analysis_scheduled"] is False
    state = wait_for(client, sid, idle)
    source = state["session"]["sources"][0]
    assert source["status"] == "ready" and source["link_type"] == "mail"
    chunks = client.get(f"/api/sessions/{sid}/sources/{source['id']}/chunks").json()["chunks"]
    text = "\n".join(c["text"] for c in chunks)
    assert "9월 유지보수 작업 결과" in text and "메인 배너 미노출 조치" in text  # 본문과 첨부(CSV)

    listed = client.get(f"/api/mail/messages?session_id={sid}&q=9월_작업내역").json()["messages"]
    assert [(m["id"], m["imported"]) for m in listed] == [("m-0002", True)]
    assert client.post(f"/api/sessions/{sid}/mails", json={"message_ids": ["m-0002"]}).json()["skipped"]

    # 다른 계정은 남의 세션으로 메일을 가져오거나 관련도를 볼 수 없다
    client.post("/api/auth/signup", json={"login_id": "other", "password": "other-password-1", "name": "이서연"})
    client.post("/api/auth/login", json={"login_id": "other", "password": "other-password-1"})
    assert client.post(f"/api/sessions/{sid}/mails", json={"message_ids": ["m-0001"]}).status_code == 404
    assert client.get(f"/api/mail/messages?session_id={sid}").status_code == 404


def test_gantt_edit_keeps_ai_version_or_user_edits(mock_client):
    import re

    sid = task_by_name(mock_client, "기관 홈페이지 운영")["session_id"]
    doc = mock_client.get(f"/api/sessions/{sid}/document").json()
    name = re.search(r"^<tr><td>([^<]+)</td><td", doc["markdown"], re.M).group(1)

    # AI 초안이 현재 문서면 같은 버전을 그 자리에서 고친다
    first = mock_client.put(f"/api/sessions/{sid}/document/gantt", json={"name": name, "nth": 0, "months": [1, 2]}).json()
    assert first["version"] == doc["version"] == 1 and first["origin"] == "ai" and first["markdown"] != doc["markdown"]
    assert mock_client.get(f"/api/sessions/{sid}/document/versions/1").json()["markdown"] == first["markdown"]

    # 직접 고친 문서면 막대만 옮긴 새 버전이 생기고, 직접 쓴 글은 남는다
    edited = first["markdown"] + "\n\n> 인계자 메모: 직접 쓴 글\n"
    mock_client.put(f"/api/sessions/{sid}/document", json={"markdown": edited, "base_version": 1})
    second = mock_client.put(f"/api/sessions/{sid}/document/gantt", json={"name": name, "nth": 0, "months": [5, 6, 7]}).json()
    assert second["version"] == 3 and second["origin"] == "user" and "인계자 메모: 직접 쓴 글" in second["markdown"]
    row = re.search(rf"^<tr><td>{re.escape(name)}</td>(.*?)<td>[^<]*</td></tr>$", second["markdown"], re.M).group(1)
    # 목업 자료에 해를 넘기는 기간(2026.10~2027.01)이 있어 표가 분기별(2026·2027, 8칸)이다. 5~7월은 2·3분기 두 칸
    assert row == "<td></td>" + '<td class="bar" bgcolor="#7055C8" colspan="2">&nbsp;</td>' + "<td></td>" * 5
    assert mock_client.get(f"/api/sessions/{sid}/document/versions").json()["versions"][0]["note"] == "월별 일정표 수정"


    # AI 상태와 맞추는 스냅숏 동기화가 직접 고친 문서를 덮어쓰지 않는다
    mock_client.app.state.services.runner._sync_snapshot(sid)
    assert mock_client.get(f"/api/sessions/{sid}/document").json()["version"] == 3

    # 해를 넘기는 표에서는 화면이 YYYYMM 으로 보낸다(2026.12~2027.01 이 이어진 막대 하나)
    third = mock_client.put(f"/api/sessions/{sid}/document/gantt", json={"name": name, "nth": 0, "months": [202612, 202701]}).json()
    row = re.search(rf"^<tr><td>{re.escape(name)}</td>(.*?)<td>[^<]*</td></tr>$", third["markdown"], re.M).group(1)
    assert row == "<td></td>" * 3 + '<td class="bar" bgcolor="#7055C8" colspan="2">&nbsp;</td>' + "<td></td>" * 3  # 2026 4분기 + 2027 1분기
    assert mock_client.put(f"/api/sessions/{sid}/document/gantt", json={"name": name, "months": [13]}).status_code == 422


def test_gantt_edit_works_even_when_the_ai_has_no_state_for_the_document(mock_client, monkeypatch):
    """데모·가져온 문서처럼 AI 작업 기록이 없어도(예전에는 409 '아직 분석을 시작하지 않았습니다') 문서의 일정표 행을 직접 고쳐 저장한다."""
    import re

    from amigo_agent import AgentStateError

    sid = task_by_name(mock_client, "기관 홈페이지 운영")["session_id"]
    doc = mock_client.get(f"/api/sessions/{sid}/document").json()
    name = re.search(r"^<tr><td>([^<]+)</td><td", doc["markdown"], re.M).group(1)

    def no_state(*args, **kwargs):
        raise AgentStateError("아직 분석을 시작하지 않았습니다.")

    monkeypatch.setattr(mock_client.app.state.services.agent, "edit_gantt", no_state)

    # AI 초안이 현재 문서면 같은 버전을 그 자리에서 고친다
    first = mock_client.put(f"/api/sessions/{sid}/document/gantt", json={"name": name, "nth": 0, "months": [202612, 202701]})
    assert first.status_code == 200, first.text
    assert first.json()["version"] == doc["version"] and first.json()["markdown"] != doc["markdown"]
    row = re.search(rf"^<tr><td>{re.escape(name)}</td>(.*?)<td>[^<]*</td></tr>$", first.json()["markdown"], re.M).group(1)
    assert row == "<td></td>" * 3 + '<td class="bar" bgcolor="#7055C8" colspan="2">&nbsp;</td>' + "<td></td>" * 3  # 2026 4분기 + 2027 1분기(8칸 분기 표)

    # 직접 고친 문서면 막대만 옮긴 새 버전이 생기고 직접 쓴 글은 남는다
    edited = first.json()["markdown"] + "\n\n> 인계자 메모: 직접 쓴 글\n"
    mock_client.put(f"/api/sessions/{sid}/document", json={"markdown": edited, "base_version": first.json()["version"]})
    second = mock_client.put(f"/api/sessions/{sid}/document/gantt", json={"name": name, "nth": 0, "months": [202603]})
    assert second.status_code == 200 and second.json()["origin"] == "user" and "인계자 메모: 직접 쓴 글" in second.json()["markdown"]
    assert mock_client.get(f"/api/sessions/{sid}/document/versions").json()["versions"][0]["note"] == "월별 일정표 수정"

    # 문서에도 없는 이름이면 예전처럼 409
    missing = mock_client.put(f"/api/sessions/{sid}/document/gantt", json={"name": "없는 업무", "nth": 0, "months": [1]})
    assert missing.status_code == 409 and "분석을 시작하지 않았습니다" in missing.json()["detail"]
