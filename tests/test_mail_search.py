"""메일함 검색: 제목 검색과 관련 사람(보낸 사람·받는 사람·참조) 검색."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path):
    settings = Settings(data_dir=tmp_path / "real-data", mock_mode=True, mock_delay=0, workers=4)
    with TestClient(create_app(settings)) as c:
        assert c.post("/api/auth/login", json={"login_id": "demo", "password": "demo1234"}).status_code == 200
        yield c


def search(client, **params) -> list[dict]:
    res = client.get("/api/mail/messages", params={"size": 50, **params})
    assert res.status_code == 200, res.text
    return res.json()["messages"]


def test_subject_search_matches_only_the_subject(client):
    everything = search(client)
    by_subject = search(client, subject="SSL")
    assert by_subject and len(by_subject) < len(everything)
    assert all("ssl" in m["subject"].lower() for m in by_subject)
    # q 는 본문까지 찾으므로 제목 검색과 결과가 다를 수 있다(제목에 없고 본문에만 있는 메일은 제목 검색에서 빠진다)
    assert {m["id"] for m in by_subject} <= {m["id"] for m in search(client, q="SSL")}
    assert search(client, subject="SSL 인증서") and search(client, subject="없는제목낱말xyz") == []


def people_of(client, mail_id: str) -> list[dict]:
    detail = client.get(f"/api/mail/messages/{mail_id}").json()
    return [detail["sender"], *detail["to"], *detail["cc"]]


def test_person_search_matches_sender_recipients_and_cc(client):
    everything = search(client)
    everyone = {mail["id"]: people_of(client, mail["id"]) for mail in everything}
    sent = next(m for m in everything if m["folder"] == "sent")
    name = sent["to"][0]["name"]  # 내가 보낸 메일의 받는 사람(보낸 사람이 아니다)
    assert name != sent["sender"]["name"]

    found = {m["id"] for m in search(client, person=name)}
    expected = {mail_id for mail_id, people in everyone.items() if any(name in p["name"] or name.lower() in p["email"].lower() for p in people)}
    assert sent["id"] in found and found == expected  # 보낸 사람·받는 사람·참조 중 어디에 있어도 찾고, 관계없는 메일은 빼고

    assert search(client, person="없는사람xyz") == []
    assert search(client, person=name, subject="없는제목낱말xyz") == []  # 제목 조건과 함께 쓰면 둘 다 맞아야 한다
    first_subject_word = sent["subject"].split()[0]
    assert sent["id"] in {m["id"] for m in search(client, person=name, subject=first_subject_word)}


def test_important_and_date_filters_for_favorites(client):
    everything = search(client)
    important = search(client, important="true")
    assert important and len(important) < len(everything) and all(m["important"] for m in important)
    assert {m["id"] for m in important} == {m["id"] for m in everything if m["important"]}
    newest_day = everything[0]["sent_at"][:10]  # 가장 최근 메일이 온 날(오늘온메일함처럼 날짜로 거른다)
    today = search(client, date_from=newest_day)
    assert today and all(m["sent_at"][:10] >= newest_day for m in today) and len(today) <= len(everything)
    assert search(client, date_from="2999-01-01") == []
