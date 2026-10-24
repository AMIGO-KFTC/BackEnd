"""인수인계 챗봇: 올린 자료(파일·메일)·인계자 답변·완성된 인수인계서를 근거로 질문에 답한다."""

import pytest
from amigo_agent import AgentConfig, QAScope, answer_from_materials
from amigo_agent.qa import QAOutput, plain_tables
from amigo_rag.models import SearchResult
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

BARS = (
    '<tr><td>SSL 인증서 갱신</td><td class="bar" bgcolor="#7055C8">&nbsp;</td><td></td><td></td>'
    '<td class="bar" bgcolor="#7055C8" colspan="2">&nbsp;</td>' + "<td></td>" * 6 + '<td class="bar" bgcolor="#7055C8">&nbsp;</td><td></td>'
    "<td>매년 12월</td></tr></table>"
)
MONTHS = "".join(f"<th>{m}월</th>" for m in range(1, 13))
# 지금 양식(render.py): 머리글 2줄 — '단위업무명 | 계획기간(12칸) | 진행상황 요약', 그 아래 '1월 … 12월'
GANTT = '<table><tr><th rowspan="2">단위업무명</th><th colspan="12">계획기간</th><th rowspan="2">진행상황 요약</th></tr><tr>' + MONTHS + "</tr>" + BARS
# 예전 양식: 머리글 1줄 — '단위업무 | 1월 … 12월 | 비고'(이미 만들어 둔 문서는 이 모양 그대로 남아 있다)
OLD_GANTT = "<table><tr><th>단위업무</th>" + MONTHS + "<th>비고</th></tr>" + BARS


def hit(text: str, score: float = 0.5, relevant: bool | None = True, **meta) -> SearchResult:
    meta = {"source_id": "s1", "source_name": "자료", **meta}
    return SearchResult(chunk_id=f"c-{abs(hash(text)) % 10_000}", text=text, score=score, metadata=meta, relevant=relevant)


class FakeKB:
    def __init__(self, *results):
        self.results, self.queries = list(results), []

    def search(self, query, top_k=5, **kwargs):
        self.queries.append(query)
        return self.results[:top_k]


class FakeLLM:
    def __init__(self, output):
        self.output, self.calls = output, []

    def structured(self, **kwargs):
        self.calls.append(kwargs)
        return self.output


@pytest.fixture()
def mock_client(tmp_path):
    settings = Settings(data_dir=tmp_path / "real-data", mock_mode=True, mock_delay=0, workers=4)
    with TestClient(create_app(settings)) as c:
        yield c


def login(client, login_id, password="demo1234"):
    res = client.post("/api/auth/login", json={"login_id": login_id, "password": password})
    assert res.status_code == 200, res.text


def my_scope(client, **want) -> dict:
    scopes = client.get("/api/handover-chat/scopes").json()["scopes"]
    return next(s for s in scopes if s["kind"] == "mine" and all(s[k] == v for k, v in want.items()))


# ---------------------------------------------------------------- AI 모듈
def test_gantt_html_becomes_text_and_other_tables_become_markdown():
    text = plain_tables(f"## 일정\n\n{GANTT}\n\n<table><tr><th>항목</th><th colspan=\"3\">내용</th></tr><tr><th>개요</th><td colspan=\"3\">홈페이지<br>운영</td></tr></table>")
    assert "- SSL 인증서 갱신: 1월, 4~5월, 12월 (매년 12월)" in text
    assert "<table" not in text and "| 개요 | 홈페이지 / 운영 |" in text


def test_gantt_with_old_single_row_header_is_still_read():
    assert "- SSL 인증서 갱신: 1월, 4~5월, 12월 (매년 12월)" in plain_tables(OLD_GANTT)


def test_claude_gets_uploaded_materials_answers_and_the_document_together_and_sources_are_mapped():
    kb = FakeKB(
        hit("유지보수 계약은 2027-03-31 에 끝나며 월 330만원입니다.", 0.9, source_type="pdf", file_name="유지보수계약서.pdf", page=3),
        hit("업체 담당자는 한빛웹솔루션 박과장(010-1234-5678)입니다.", 0.7, source_type="chat", source_name="인계자 답변 - 협업 관계", created_at="2026-10-20"),
        hit("점심은 구내식당입니다.", 0.08, relevant=False, source_type="pdf", file_name="식당안내.pdf"),  # 관련 없다고 판정되고 점수도 낮은 조각은 안 쓴다
        hit("계약 갱신 협의는 총무팀과 합니다.", 0.36, relevant=False, source_type="eml", file_type="eml", subject="계약 갱신"),  # 판정은 거짓이어도 점수가 높으면 후보로 둔다(구어체 질문 대비)
    )
    llm = FakeLLM(QAOutput(answer="3월 말에 끝납니다.", grounded=True, evidence_ids=["E2", "[E3]", "E99"]))
    scopes = [QAScope("홈페이지 운영 · 인계자 김민수", markdown="## 개요\n\n홈페이지 운영\n\n## 일정\n\n" + GANTT, version=4, kb=kb)]
    history = [{"role": "user", "content": "업체가 어디예요?"}, {"role": "assistant", "content": "한빛웹솔루션입니다."}]

    result = answer_from_materials(llm, AgentConfig(), "계약은 언제 끝나요?", history, scopes)

    prompt = llm.calls[0]["user"]
    assert "[E1] 인수인계서 · 인수인계서 v4" in prompt and "SSL 인증서 갱신: 1월" in prompt  # 짧은 문서는 전문
    assert "파일 · 유지보수계약서.pdf p.3" in prompt and "330만원" in prompt
    assert "인계자 답변 · 인계자 답변 - 협업 관계 (2026-10-20)" in prompt and "박과장" in prompt
    assert "메일 · 메일 '계약 갱신'" in prompt and "점심" not in prompt
    assert "업체가 어디예요? 계약은 언제 끝나요?" in kb.queries[0]  # 꼬리 질문은 앞선 질문과 함께 찾는다
    assert result["grounded"] is True and result["searched"] == 4
    assert result["sources"] == [  # 쓴 번호(E2 계약서, E3 인계자 답변)만, 없는 번호는 버린다. 올린 자료의 ID 도 함께 돌려준다
        {"scope": 0, "kind": "file", "label": "유지보수계약서.pdf p.3", "source_id": "s1"},
        {"scope": 0, "kind": "answer", "label": "인계자 답변 - 협업 관계 (2026-10-20)", "source_id": "s1"},
    ]


def test_mail_and_link_kinds_and_multiple_scopes_are_labelled():
    kbs = [FakeKB(hit("SSL 갱신 요청드립니다.", source_type="eml", file_type="eml", subject="SSL 갱신 건", sender_name="최영호")), FakeKB(hit("운영 위키", source_type="confluence", url="https://wiki/x"))]
    llm = FakeLLM(QAOutput(answer="네.", grounded=True, evidence_ids=["E1", "E2"]))
    scopes = [QAScope("업무 A", kb=kbs[0]), QAScope("업무 B", kb=kbs[1])]
    result = answer_from_materials(llm, AgentConfig(), "SSL", [], scopes)
    assert "메일 · 업무: 업무 A · 메일 'SSL 갱신 건'" in llm.calls[0]["user"] and "링크 · 업무: 업무 B" in llm.calls[0]["user"]
    assert {(s["scope"], s["kind"]) for s in result["sources"]} == {(0, "mail"), (1, "link")}


def test_long_document_sends_only_matching_sections_and_a_broken_kb_does_not_stop_the_answer():
    filler = "관련 없는 설명입니다. " * 300
    markdown = "\n\n".join(f"## 장 {n}\n\n{filler}" for n in range(10)) + "\n\n## 계약 만료일\n\n계약은 2027-03-31 에 끝납니다."

    class BrokenKB:
        def search(self, *a, **k):
            raise RuntimeError("chroma down")

    llm = FakeLLM(QAOutput(answer="2027-03-31 입니다.", grounded=True, evidence_ids=["E1"]))
    result = answer_from_materials(llm, AgentConfig(), "계약 만료일이 언제예요?", [], [QAScope("업무", markdown=markdown, version=2, kb=BrokenKB())])
    prompt = llm.calls[0]["user"]
    assert "인수인계서 v2 › 계약 만료일" in prompt and len(prompt) < 20_000
    assert result["sources"] == [{"scope": 0, "kind": "document", "label": "인수인계서 v2 › 계약 만료일", "source_id": ""}]


def test_no_evidence_means_no_claude_call_and_no_scope_means_nothing_to_ask():
    llm = FakeLLM(QAOutput(answer="x", grounded=True, evidence_ids=[]))
    result = answer_from_materials(llm, AgentConfig(), "zzzz", [], [QAScope("업무", kb=FakeKB())])
    assert llm.calls == [] and result["grounded"] is False and "찾지 못했어요" in result["answer"]
    empty = answer_from_materials(llm, AgentConfig(), "아무거나", [], [])
    assert empty["grounded"] is False and "자료가 아직 없어요" in empty["answer"]


def test_offline_answer_quotes_materials_with_their_source_and_ignores_single_word_matches():
    kb = FakeKB(hit("유지보수 계약은 2027-03-31 에 끝납니다.", source_type="pdf", file_name="계약서.pdf", page=2))
    scopes = [QAScope("업무", markdown="## 메뉴 구조\n\n홈페이지 메뉴 개편", version=1, kb=kb)]
    found = answer_from_materials(None, AgentConfig(), "계약은 언제 끝나요?", [], scopes)
    assert found["grounded"] is True and "파일 · 계약서.pdf p.2" in found["answer"] and found["sources"][0]["kind"] == "file"
    nothing = answer_from_materials(None, AgentConfig(), "오늘 점심 메뉴 추천해 줘", [], [QAScope("업무", markdown="## 메뉴 구조\n\n홈페이지 메뉴 개편", version=1)])
    assert nothing["grounded"] is False and nothing["sources"] == []


# ---------------------------------------------------------------- API
def test_requires_login(mock_client):
    assert mock_client.get("/api/handover-chat/scopes").status_code == 401
    assert mock_client.post("/api/handover-chat", json={"question": "안녕"}).status_code == 401


def test_scopes_include_tasks_with_uploaded_materials_and_documents(mock_client):
    login(mock_client, "demo")
    scopes = mock_client.get("/api/handover-chat/scopes").json()["scopes"]
    assert len(scopes) >= 3
    done = my_scope(mock_client, has_document=True)
    assert done["session_id"] == done["id"] and done["version"] >= 1 and done["source_count"] >= 1
    assert any(not s["has_document"] and s["source_count"] >= 1 and s["version"] == 0 for s in scopes)  # 작성 중인 업무도 자료로 물어볼 수 있다


def test_ask_uses_the_knowledge_base_of_the_task_and_the_document(mock_client):
    login(mock_client, "demo")
    done = my_scope(mock_client, has_document=True)
    kb = mock_client.app.state.services.kb(done["id"])
    kb.search = lambda query, top_k=5, **kw: [hit("유지보수 계약은 2027-03-31 에 끝납니다.", 0.9, source_type="pdf", file_name="계약서.pdf", page=3)]

    res = mock_client.post("/api/handover-chat", json={"question": "유지보수 계약은 언제 끝나요?", "scope_ids": [done["id"]]})
    body = res.json()
    assert res.status_code == 200 and body["grounded"] is True and body["engine"] == "mock"
    assert any(s["kind"] == "file" and s["label"] == "계약서.pdf p.3" and s["scope_id"] == done["id"] and s["source_id"] == "s1" for s in body["sources"])
    assert all(s["scope_task"] == done["task_name"] and s["scope_kind"] == "mine" and s["scope_owner"] == done["owner_name"] for s in body["sources"])  # 어느 업무를 참조했는지
    assert "계약서.pdf p.3" in body["answer"] and "<table" not in body["answer"]

    ssl = mock_client.post("/api/handover-chat", json={"question": "SSL 인증서 갱신은 언제 하나요?", "scope_ids": [done["id"]]}).json()
    assert ssl["grounded"] is True and any(s["kind"] == "document" for s in ssl["sources"])


def test_off_topic_validation_and_unknown_scope(mock_client):
    login(mock_client, "demo")
    assert mock_client.post("/api/handover-chat", json={"question": "오늘 점심 메뉴 추천해 줘"}).json()["grounded"] is False
    assert mock_client.post("/api/handover-chat", json={"question": "   "}).status_code == 422
    assert mock_client.post("/api/handover-chat", json={"question": "안녕", "scope_ids": ["nope"]}).status_code == 404


def test_received_task_exposes_only_the_document_not_the_senders_raw_materials(mock_client):
    login(mock_client, "demo")
    done = my_scope(mock_client, has_document=True)
    mock_client.post("/api/auth/logout")

    login(mock_client, "seoyeon.lee")
    assert done["id"] not in {s["id"] for s in mock_client.get("/api/handover-chat/scopes").json()["scopes"]}
    assert mock_client.post("/api/handover-chat", json={"question": "SSL 갱신", "scope_ids": [done["id"]]}).status_code == 404  # 남의 업무
    mock_client.post("/api/auth/logout")

    login(mock_client, "demo")
    assert mock_client.post(f"/api/sessions/{done['id']}/transfers", json={"recipient_name": "이서연"}).status_code == 201
    mock_client.post("/api/auth/logout")

    login(mock_client, "seoyeon.lee")
    received = next(s for s in mock_client.get("/api/handover-chat/scopes").json()["scopes"] if s["id"] == done["id"])
    assert received["kind"] == "received" and received["source_count"] == 0 and received["has_document"]
    mock_client.app.state.services.kb(done["id"]).search = lambda *a, **k: pytest.fail("받은 사람은 보낸 사람의 원본 자료를 검색하면 안 된다")
    res = mock_client.post("/api/handover-chat", json={"question": "SSL 인증서 갱신은 언제 하나요?", "scope_ids": [done["id"]]})
    assert res.status_code == 200 and res.json()["grounded"] is True and {s["kind"] for s in res.json()["sources"]} == {"document"}
    assert all(s["scope_kind"] == "received" and s["scope_owner"] == received["owner_name"] and s["scope_task"] == received["task_name"] for s in res.json()["sources"])


def test_citation_numbers_after_values_are_not_shown_to_the_chatbot(mock_client):
    login(mock_client, "demo")
    done = my_scope(mock_client, has_document=True)
    doc = mock_client.get(f"/api/sessions/{done['id']}/document").json()
    marked = doc["markdown"] + "\n\n## 추가 안내\n\n긴급 연락은 한빛웹솔루션 박과장에게 합니다 [1] [3]\n"
    assert mock_client.put(f"/api/sessions/{done['id']}/document", json={"markdown": marked, "base_version": doc["version"]}).status_code == 200
    res = mock_client.post("/api/handover-chat", json={"question": "긴급 연락은 누구에게 하나요 한빛웹솔루션", "scope_ids": [done["id"]]})
    answer = res.json()["answer"]
    assert res.status_code == 200 and "한빛웹솔루션 박과장" in answer and "[1]" not in answer and "[3]" not in answer
