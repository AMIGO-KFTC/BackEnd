"""가상 Mattermost·Confluence 연동: 최근 대화 목록·페이지 검색과, 고른 항목을 자료로 등록해 지식베이스에 적재하는지."""

from conftest import PROFILE, idle, wait_for


def _session(client) -> str:
    return client.post("/api/sessions", json=PROFILE, headers={"X-User-Id": "u1"}).json()["id"]


def test_mattermost_recent_conversations_sorted_and_searchable(client):
    data = client.get("/api/integrations/mattermost/conversations").json()
    assert data["mock"] is True and data["me"] == "김민수"
    rows = data["conversations"]
    assert len(rows) == data["total"] >= 8
    assert [r["last_post_at"] for r in rows] == sorted((r["last_post_at"] for r in rows), reverse=True)
    first = rows[0]
    assert {"id", "type", "display_name", "members", "message_count", "last_message", "url"} <= set(first)
    assert first["url"].startswith("mock://mattermost/")

    hits = client.get("/api/integrations/mattermost/conversations", params={"q": "SSL"}).json()["conversations"]
    assert hits and all("SSL" in str(r) or r["id"] in ("dm-jung-daeun", "dm-yoon-taeho") for r in hits)

    detail = client.get(f"/api/integrations/mattermost/conversations/{first['id']}").json()
    assert detail["id"] == first["id"] and len(detail["posts"]) == first["message_count"]
    assert detail["posts"][0]["user_name"] and detail["posts"][0]["text"]
    assert client.get("/api/integrations/mattermost/conversations/nope").status_code == 404


def test_confluence_search_matches_title_body_and_labels(client):
    data = client.get("/api/integrations/confluence/search", params={"q": "인증서"}).json()
    assert data["mock"] is True and data["terms"] == ["인증서"]
    titles = [r["title"] for r in data["results"]]
    assert "SSL 인증서 갱신 절차" in titles and "웹 접근성 품질인증 갱신 안내" in titles
    assert titles[0] == "SSL 인증서 갱신 절차"  # 제목 일치가 먼저
    assert all("인증서" in (r["excerpt"] + r["title"] + " ".join(r["labels"])) for r in data["results"])
    assert all({"id", "space_key", "space_name", "updated_at", "excerpt", "url", "matched_in"} <= set(r) for r in data["results"])

    both = client.get("/api/integrations/confluence/search", params={"q": "유지보수 검수"}).json()
    assert all("유지보수" in r["title"] + r["excerpt"] or "검수" in r["title"] + r["excerpt"] for r in both["results"])
    assert any(r["id"] == "1002" for r in both["results"])

    only_sec = client.get("/api/integrations/confluence/search", params={"q": "홈페이지", "space": "SEC"}).json()
    assert only_sec["results"] and all(r["space_key"] == "SEC" for r in only_sec["results"])

    assert client.get("/api/integrations/confluence/search", params={"q": "없는단어xyz"}).json()["results"] == []
    everything = client.get("/api/integrations/confluence/search").json()
    assert everything["total"] == len(everything["results"]) >= 10

    page = client.get("/api/integrations/confluence/pages/1001").json()
    assert page["title"] == "기관 홈페이지 운영 매뉴얼" and "## 2. 콘텐츠 게시 절차" in page["body"]
    assert client.get("/api/integrations/confluence/pages/9999").status_code == 404


def test_import_selected_items_as_sources(client):
    sid = _session(client)
    res = client.post(f"/api/sessions/{sid}/integrations/mattermost", json={"conversation_ids": ["ch-homepage-ops", "dm-jung-daeun"]})
    assert res.status_code == 201, res.text
    body = res.json()
    assert len(body["sources"]) == 2 and body["skipped"] == []
    assert body["sources"][0]["name"] == "Mattermost · #홈페이지-운영" and body["sources"][0]["link_type"] == "mattermost"
    assert body["sources"][0]["kind"] == "file" and body["sources"][0]["extension"] == ".md"

    res = client.post(f"/api/sessions/{sid}/integrations/confluence", json={"page_ids": ["1001", "1009"]})
    assert res.status_code == 201, res.text
    assert [s["name"] for s in res.json()["sources"]] == ["컨플루언스 · 기관 홈페이지 운영 매뉴얼", "컨플루언스 · SSL 인증서 갱신 절차"]

    state = wait_for(client, sid, idle)
    sources = state["session"]["sources"]
    assert len(sources) == 4 and all(s["status"] == "ready" and s["chunk_count"] > 0 for s in sources)

    # 적재된 조각에 대화·페이지 원문이 들어간다(실제 RAG 파서가 Markdown 을 읽음)
    mm = next(s for s in sources if s["link_type"] == "mattermost")
    chunks = client.get(f"/api/sessions/{sid}/sources/{mm['id']}/chunks").json()
    text = " ".join(c["text"] for c in chunks["chunks"]) if isinstance(chunks, dict) else " ".join(c["text"] for c in chunks)
    assert "게시요청" in text and "정다은" in text

    # 같은 대화를 또 고르면 건너뛰고, 전부 중복이면 409
    again = client.post(f"/api/sessions/{sid}/integrations/mattermost", json={"conversation_ids": ["ch-homepage-ops", "dm-park-jihoon"]})
    assert again.status_code == 201 and again.json()["skipped"] == ["#홈페이지-운영"] and len(again.json()["sources"]) == 1
    assert client.post(f"/api/sessions/{sid}/integrations/confluence", json={"page_ids": ["1001"]}).status_code == 409
    assert client.post(f"/api/sessions/{sid}/integrations/confluence", json={"page_ids": ["9999"]}).status_code == 404
    assert client.post(f"/api/sessions/{sid}/integrations/mattermost", json={"conversation_ids": []}).status_code == 422

    # 삭제도 다른 자료와 같다
    assert client.delete(f"/api/sessions/{sid}/sources/{mm['id']}").status_code == 204
    assert len(client.get(f"/api/sessions/{sid}/sources").json()) == 4
