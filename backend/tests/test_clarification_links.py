"""Clarification links (FR-22): the lead picks questions and gets a single-use link that expires in 7 days and can be
revoked. The client's page needs no login, shows only the link's questions, and takes answers in English, Bangla
or Banglish, stored exactly as written."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.api import clarifications
from app.core.security import hash_secret
from app.models import ClarificationAnswer, ClarificationLink
from tests.clarify_helpers import make_project, seed_slow_report, use_llm

BANGLA = "রিপোর্ট ২ সেকেন্ডের মধ্যে লোড হতে হবে।"
BANGLISH = "Report ta 2 second er moddhe load hote hobe, admin dekhbe.  "


@pytest.fixture
def project(client, make_user):
    return make_project(client, make_user)


@pytest.fixture
def asked(client, project, db_session_factory):
    """A requirement with three generated questions; returns their ids."""
    headers, base, pid = project
    rid = seed_slow_report(db_session_factory, pid)
    use_llm()
    assert client.post(f"{base}/requirements/{rid}/questions/generate", headers=headers).json()["created"] == 3
    return [q["id"] for q in client.get(f"{base}/clarifications/questions", headers=headers).json()]


def create_link(client, project, question_ids):
    headers, base, _ = project
    return client.post(f"{base}/clarifications/links", json={"question_ids": question_ids}, headers=headers)


def token_of(link: dict) -> str:
    return link["url"].rsplit("/clarify/", 1)[1]


def test_link_is_shown_once_and_only_its_hash_is_stored(client, project, asked, db_session_factory):
    res = create_link(client, project, asked[:2])
    assert res.status_code == 201, res.text
    link = res.json()
    assert link["url"].startswith("http://localhost:3000/clarify/")
    token = token_of(link)
    assert len(token) >= 40
    assert link["state"] == "active"
    expires = datetime.fromisoformat(link["expires_at"])
    assert timedelta(days=6, hours=23) < expires - datetime.now(UTC) <= timedelta(days=7)
    with db_session_factory() as db:
        stored = db.scalar(select(ClarificationLink))
        assert stored.token_hash == hash_secret(token) and token not in stored.token_hash

    headers, base, _ = project
    listed = client.get(f"{base}/clarifications/links", headers=headers).json()
    assert "url" not in listed[0] and token not in str(listed)
    # The chosen AI suggestions count as accepted now
    questions = {q["id"]: q for q in client.get(f"{base}/clarifications/questions", headers=headers).json()}
    assert [questions[i]["status"] for i in asked] == ["accepted", "accepted", "suggested"]
    assert [questions[i]["state"] for i in asked] == ["shared", "shared", "draft"]


def test_client_page_needs_no_login_and_shows_only_the_chosen_questions(client, project, asked):
    token = token_of(create_link(client, project, [asked[2], asked[0]]).json())
    res = client.get(f"/public/clarify/{token}")  # no Authorization header
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == {"project_name", "expires_at", "questions"}
    assert body["project_name"] == "Acme Shop"
    assert [q["id"] for q in body["questions"]] == sorted([asked[2], asked[0]])
    assert set(body["questions"][0]) == {"id", "text"}  # no requirement code, finding or status
    assert "REQ-" not in res.text


def test_answers_in_bangla_and_banglish_are_stored_exactly(client, project, asked, db_session_factory):
    token = token_of(create_link(client, project, asked[:2]).json())
    answers = [{"question_id": asked[0], "text": BANGLA}, {"question_id": asked[1], "text": BANGLISH}]
    res = client.post(f"/public/clarify/{token}/answers", json={"answers": answers})
    assert res.status_code == 200, res.text
    assert res.json() == {"answered": 2}
    with db_session_factory() as db:
        stored = db.scalars(select(ClarificationAnswer).order_by(ClarificationAnswer.id)).all()
        assert [a.text for a in stored] == [BANGLA, BANGLISH]
        assert all(a.answered_at is not None and a.link_id is not None for a in stored)


def test_blank_answers_are_skipped_and_all_blank_is_refused(client, project, asked, db_session_factory):
    token = token_of(create_link(client, project, asked[:2]).json())
    blank = client.post(f"/public/clarify/{token}/answers", json={"answers": [{"question_id": asked[0], "text": "  "}]})
    assert blank.status_code == 422
    # Still open after a refused attempt
    assert client.get(f"/public/clarify/{token}").status_code == 200
    answers = [{"question_id": asked[0], "text": "2 seconds"}, {"question_id": asked[1], "text": ""}]
    assert client.post(f"/public/clarify/{token}/answers", json={"answers": answers}).json() == {"answered": 1}


def test_too_long_or_too_many_answers_are_refused(client, project, asked):
    token = token_of(create_link(client, project, asked[:1]).json())
    long = client.post(f"/public/clarify/{token}/answers", json={"answers": [{"question_id": asked[0], "text": "x" * 2001}]})
    assert long.status_code == 422
    empty = client.post(f"/public/clarify/{token}/answers", json={"answers": []})
    assert empty.status_code == 422


def test_an_answer_to_a_question_outside_the_link_is_refused(client, project, asked, db_session_factory):
    token = token_of(create_link(client, project, asked[:1]).json())
    res = client.post(f"/public/clarify/{token}/answers", json={"answers": [{"question_id": asked[1], "text": "yes"}]})
    assert res.status_code == 400
    with db_session_factory() as db:
        assert db.scalar(select(ClarificationAnswer)) is None
        assert db.scalar(select(ClarificationLink)).submitted_at is None


def test_unknown_token(client):
    assert client.get("/public/clarify/not-a-real-token").status_code == 404
    res = client.post("/public/clarify/not-a-real-token/answers", json={"answers": [{"question_id": 1, "text": "x"}]})
    assert res.status_code == 404


def test_expired_link(client, project, asked, db_session_factory):
    token = token_of(create_link(client, project, asked[:1]).json())
    with db_session_factory() as db:
        link = db.scalar(select(ClarificationLink))
        link.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    res = client.get(f"/public/clarify/{token}")
    assert res.status_code == 410 and "expired" in res.json()["detail"]
    post = client.post(f"/public/clarify/{token}/answers", json={"answers": [{"question_id": asked[0], "text": "x"}]})
    assert post.status_code == 410
    headers, base, _ = project
    assert client.get(f"{base}/clarifications/links", headers=headers).json()[0]["state"] == "expired"
    # The question is no longer shared, so it can be put in a new link
    questions = client.get(f"{base}/clarifications/questions", headers=headers).json()
    assert next(q for q in questions if q["id"] == asked[0])["state"] == "draft"


def test_revoked_link(client, project, asked):
    headers, base, _ = project
    link = create_link(client, project, asked[:1]).json()
    token = token_of(link)
    revoked = client.post(f"{base}/clarifications/links/{link['id']}/revoke", headers=headers)
    assert revoked.status_code == 200 and revoked.json()["state"] == "revoked"
    res = client.get(f"/public/clarify/{token}")
    assert res.status_code == 410 and "no longer active" in res.json()["detail"]
    post = client.post(f"/public/clarify/{token}/answers", json={"answers": [{"question_id": asked[0], "text": "x"}]})
    assert post.status_code == 410


def test_a_link_is_used_only_once(client, project, asked, db_session_factory):
    headers, base, _ = project
    link = create_link(client, project, asked[:2]).json()
    token = token_of(link)
    first = client.post(f"/public/clarify/{token}/answers", json={"answers": [{"question_id": asked[0], "text": "2 s"}]})
    assert first.status_code == 200
    again = client.post(f"/public/clarify/{token}/answers", json={"answers": [{"question_id": asked[1], "text": "x"}]})
    assert again.status_code == 410 and "already answered" in again.json()["detail"]
    opened = client.get(f"/public/clarify/{token}")
    assert opened.status_code == 410 and "already answered" in opened.json()["detail"]
    with db_session_factory() as db:
        assert len(db.scalars(select(ClarificationAnswer)).all()) == 1
    assert client.get(f"{base}/clarifications/links", headers=headers).json()[0]["state"] == "answered"
    # An answered link cannot be revoked, and an answered question cannot go into a new link
    assert client.post(f"{base}/clarifications/links/{link['id']}/revoke", headers=headers).status_code == 409
    assert create_link(client, project, [asked[0]]).status_code == 409
    # The unanswered one can be asked again
    assert create_link(client, project, [asked[1]]).status_code == 201


def test_links_only_take_questions_of_the_project(client, project, asked, make_user):
    _, stranger = make_user()
    other = client.post("/projects", json={"name": "Other"}, headers=stranger).json()
    res = client.post(f"/projects/{other['id']}/clarifications/links", json={"question_ids": asked[:1]}, headers=stranger)
    assert res.status_code == 404
    assert create_link(client, project, [99999]).status_code == 404
    assert create_link(client, project, []).status_code == 422
    assert create_link(client, project, list(range(1, 22))).status_code == 422
    headers, base, _ = project
    link = create_link(client, project, asked[:1]).json()
    assert client.post(f"/projects/{other['id']}/clarifications/links/{link['id']}/revoke", headers=stranger).status_code == 404


def test_the_public_page_is_rate_limited(client, project, asked, monkeypatch):
    monkeypatch.setattr(clarifications, "PUBLIC_RATE_LIMIT", 2)
    token = token_of(create_link(client, project, asked[:1]).json())
    assert client.get(f"/public/clarify/{token}").status_code == 200
    assert client.get("/public/clarify/guess").status_code == 404
    limited = client.get(f"/public/clarify/{token}")
    assert limited.status_code == 429 and limited.headers["Retry-After"]
