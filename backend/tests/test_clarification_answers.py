"""Client answers (FR-09): each linked to its question and requirement with its time, a "new answers" badge, and an
AI proposal that the lead accepts (a new version by the lead, validated again), edits or rejects."""

import pytest
from sqlalchemy import select

from app.ai.llm import LLMError
from app.models import ClarificationAnswer, RequirementVersion
from tests.clarify_helpers import make_project, seed_slow_report, use_llm

NEW_STATEMENT = "The system shall load the sales report within 2 seconds."


@pytest.fixture
def project(client, make_user):
    return make_project(client, make_user)


@pytest.fixture
def answered(client, project, db_session_factory):
    """REQ-001 (load the report "fast") with its questions sent and two of them answered in Banglish."""
    headers, base, pid = project
    rid = seed_slow_report(db_session_factory, pid)
    fake = use_llm()
    client.post(f"{base}/requirements/{rid}/questions/generate", headers=headers)
    qids = [q["id"] for q in client.get(f"{base}/clarifications/questions", headers=headers).json()]
    link = client.post(f"{base}/clarifications/links", json={"question_ids": qids}, headers=headers).json()
    token = link["url"].rsplit("/", 1)[1]
    answers = [
        {"question_id": qids[0], "text": "2 second er moddhe load hote hobe"},
        {"question_id": qids[2], "text": "Admin report dekhbe"},
    ]
    assert client.post(f"/public/clarify/{token}/answers", json={"answers": answers}).status_code == 200
    return rid, qids, fake


def detail(client, project, rid):
    headers, base, _ = project
    res = client.get(f"{base}/requirements/{rid}", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def post(client, project, path, body=None):
    headers, base, _ = project
    return client.post(f"{base}{path}", json=body, headers=headers)


def test_answers_are_linked_to_question_and_requirement_with_time(client, project, answered, db_session_factory):
    rid, qids, _ = answered
    with db_session_factory() as db:
        stored = db.scalars(select(ClarificationAnswer).order_by(ClarificationAnswer.id)).all()
        assert [(a.question_id, a.requirement_id) for a in stored] == [(qids[0], rid), (qids[2], rid)]
        assert all(a.answered_at is not None and a.seen_at is None for a in stored)

    thread = detail(client, project, rid)["answers"]
    assert [(a["question_id"], a["text"]) for a in thread] == [
        (qids[0], "2 second er moddhe load hote hobe"),
        (qids[2], "Admin report dekhbe"),
    ]
    assert thread[0]["question_text"].startswith("Could you tell us more")
    states = {q["id"]: q["state"] for q in detail(client, project, rid)["questions"]}
    assert states == {qids[0]: "answered", qids[1]: "draft", qids[2]: "answered"}  # the link is closed now


def test_new_answers_badge_and_mark_seen(client, project, answered):
    rid, _, _ = answered
    headers, base, _ = project
    listed = client.get(f"{base}/requirements", headers=headers).json()[0]
    assert (listed["new_answers"], listed["open_questions"]) == (2, 1)
    assert client.get(f"{base}/clarifications/unseen", headers=headers).json() == {"count": 2}

    seen = post(client, project, f"/requirements/{rid}/answers/seen")
    assert seen.status_code == 200
    assert seen.json()["new_answers"] == 0 and all(a["seen_at"] for a in seen.json()["answers"])
    assert client.get(f"{base}/clarifications/unseen", headers=headers).json() == {"count": 0}


def test_accepting_a_proposal_adds_a_version_by_the_lead_and_validates_again(client, project, answered):
    rid, _, fake = answered
    before = detail(client, project, rid)
    assert {f["rule"] for f in before["findings"]} >= {"vague_word", "no_measurable_value"}
    fake.proposal = {"statement": NEW_STATEMENT, "reason": "Adds the 2-second limit the client gave."}

    res = post(client, project, f"/requirements/{rid}/proposals")
    assert res.status_code == 200, res.text
    proposal = res.json()["proposals"][-1]
    assert (proposal["status"], proposal["origin"], proposal["statement"]) == ("suggested", "ai", NEW_STATEMENT)
    assert proposal["based_on_version"] == 1 and len(proposal["answer_ids"]) == 2
    sent = fake.proposal_calls[0]
    assert sent["statement"] == before["current"]["statement"]
    assert [a["answer"] for a in sent["answers"]] == ["2 second er moddhe load hote hobe", "Admin report dekhbe"]
    # Nothing changes before the lead decides
    assert detail(client, project, rid)["version_count"] == 1

    accepted = post(client, project, f"/requirements/{rid}/proposals/{proposal['id']}/accept")
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["status"] == "edited" and body["version_count"] == 2
    assert (body["current"]["statement"], body["current"]["origin"]) == (NEW_STATEMENT, "user")
    assert body["current"]["created_by_name"]
    # Validated again: "fast" and the missing value are gone (the client's own "taratari" stays as a note)
    rules = {f["rule"] for f in body["findings"]}
    assert "no_measurable_value" not in rules
    assert not [f for f in body["findings"] if f["rule"] == "vague_word" and f["evidence"] == "fast"]
    done = body["proposals"][-1]
    assert done["status"] == "accepted" and done["version_id"] is not None and done["decided_at"]

    # The used answers are not sent again
    assert post(client, project, f"/requirements/{rid}/proposals").status_code == 409


def test_edit_and_accept(client, project, answered, db_session_factory):
    rid, _, fake = answered
    fake.proposal = {"statement": NEW_STATEMENT, "reason": "Adds the limit."}
    proposal = post(client, project, f"/requirements/{rid}/proposals").json()["proposals"][-1]
    mine = "The admin shall see the sales report within 2 seconds."
    body = post(client, project, f"/requirements/{rid}/proposals/{proposal['id']}/accept", {"statement": mine}).json()
    assert body["current"]["statement"] == mine and body["proposals"][-1]["status"] == "edited"
    with db_session_factory() as db:
        assert [v.statement for v in db.scalars(select(RequirementVersion).order_by(RequirementVersion.version))] == [
            "The system shall load the sales report fast.",
            mine,
        ]


def test_not_enough_information(client, project, answered):
    rid, _, fake = answered
    fake.proposal = {"not_enough_information": True, "statement": "", "reason": "The client did not give a value."}
    proposal = post(client, project, f"/requirements/{rid}/proposals").json()["proposals"][-1]
    assert proposal["not_enough_information"] is True and proposal["statement"] is None
    # It cannot be accepted as it is, only with the lead's own text
    assert post(client, project, f"/requirements/{rid}/proposals/{proposal['id']}/accept").status_code == 422
    ok = post(client, project, f"/requirements/{rid}/proposals/{proposal['id']}/accept", {"statement": NEW_STATEMENT})
    assert ok.status_code == 200


def test_an_unchanged_statement_counts_as_not_enough_information(client, project, answered):
    rid, _, fake = answered
    fake.proposal = {"statement": "The system shall load the sales report fast.", "reason": "Same."}
    proposal = post(client, project, f"/requirements/{rid}/proposals").json()["proposals"][-1]
    assert proposal["not_enough_information"] is True


def test_a_new_proposal_replaces_the_open_one(client, project, answered):
    rid, _, fake = answered
    fake.proposal = {"statement": NEW_STATEMENT, "reason": "First."}
    post(client, project, f"/requirements/{rid}/proposals")
    fake.proposal = {"statement": NEW_STATEMENT + " ", "reason": "Second."}
    proposals = post(client, project, f"/requirements/{rid}/proposals").json()["proposals"]
    assert [p["reason"] for p in proposals] == ["Second."]


def test_a_stale_proposal_is_refused(client, project, answered):
    rid, _, fake = answered
    fake.proposal = {"statement": NEW_STATEMENT, "reason": "Adds the limit."}
    proposal = post(client, project, f"/requirements/{rid}/proposals").json()["proposals"][-1]
    headers, base, _ = project
    client.patch(f"{base}/requirements/{rid}", json={"statement": "The system shall load the report in 3 seconds."}, headers=headers)
    res = post(client, project, f"/requirements/{rid}/proposals/{proposal['id']}/accept")
    assert res.status_code == 409 and "changed" in res.json()["detail"]


def test_reject_a_proposal(client, project, answered):
    rid, _, fake = answered
    fake.proposal = {"statement": NEW_STATEMENT, "reason": "Adds the limit."}
    proposal = post(client, project, f"/requirements/{rid}/proposals").json()["proposals"][-1]
    body = post(client, project, f"/requirements/{rid}/proposals/{proposal['id']}/reject").json()
    assert body["proposals"][-1]["status"] == "rejected" and body["version_count"] == 1
    # Decided once only
    assert post(client, project, f"/requirements/{rid}/proposals/{proposal['id']}/accept").status_code == 409
    # Rejected proposals did not use the answers: a new one may be asked for
    assert post(client, project, f"/requirements/{rid}/proposals").status_code == 200


def test_proposal_errors(client, project, answered, db_session_factory):
    rid, _, fake = answered
    fake.proposal_fail = LLMError("The AI returned an invalid answer twice.")
    res = post(client, project, f"/requirements/{rid}/proposals")
    assert res.status_code == 502 and detail(client, project, rid)["proposals"] == []
    # A requirement without answers has nothing to propose from
    _, _, pid = project
    other = seed_slow_report(db_session_factory, pid)
    assert post(client, project, f"/requirements/{other}/proposals").status_code == 409
