"""Clarification questions (AR-04): generated from open findings through the (fake) LLM, linked to the finding they
resolve, editable by the lead, and replaced on regenerate only while they are untouched AI suggestions (rule 2)."""

import pytest

from app.ai.llm import LLMError, LLMUnavailableError
from tests.clarify_helpers import (
    CLEAN,
    add_conflict,
    findings_of,
    make_project,
    seed_requirement,
    seed_slow_report,
    use_llm,
)


@pytest.fixture
def project(client, make_user):
    return make_project(client, make_user)


def generate(client, project, rid: int):
    headers, base, _ = project
    return client.post(f"{base}/requirements/{rid}/questions/generate", headers=headers)


def questions(client, project, rid: int | None = None) -> list[dict]:
    headers, base, _ = project
    params = {"requirement_id": rid} if rid else {}
    res = client.get(f"{base}/clarifications/questions", params=params, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def test_questions_are_linked_to_the_findings_they_resolve(client, project, db_session_factory):
    _, _, pid = project
    rid = seed_slow_report(db_session_factory, pid)
    findings = {f.id: f for f in findings_of(db_session_factory, rid)}
    assert {f.rule for f in findings.values()} >= {"vague_word", "no_measurable_value"}
    fake = use_llm()

    res = generate(client, project, rid)
    assert res.status_code == 200, res.text
    body = res.json()
    sent = fake.question_calls[0][0]
    # Only vague / missing info / conflict / change request findings are sent, with the client's sentence
    assert {f["category"] for f in sent["findings"]} <= {"vague", "missing_info", "conflict", "change_request"}
    assert sent["client_sentences"] == ["Report ta taratari load hote hobe."]
    assert body["created"] == len(sent["findings"]) and body["created"] <= 3
    listed = questions(client, project, rid)
    assert all(q["finding_id"] in findings for q in listed)
    assert all((q["origin"], q["status"], q["state"]) == ("ai", "suggested", "draft") for q in listed)
    assert listed[0]["finding_category"] == findings[listed[0]["finding_id"]].category
    assert listed[0]["ai_model"] == "fake-model"


def test_out_of_scope_and_module_findings_get_no_question(client, project, db_session_factory):
    _, base, pid = project
    headers = project[0]
    client.patch(base, json={"modules": [{"name": "Payments", "description": "bKash card"}]}, headers=headers)
    rid = seed_requirement(db_session_factory, pid, CLEAN)
    assert {f.category for f in findings_of(db_session_factory, rid)} == {"module_alignment"}
    fake = use_llm()
    assert generate(client, project, rid).json() == {"created": 0, "items": [], "error": None}
    assert fake.question_calls == []


def test_conflicts_send_the_other_statement(client, project, db_session_factory):
    _, _, pid = project
    older = seed_requirement(db_session_factory, pid, "The customer shall pay by card.", actor="customer")
    newer = seed_requirement(db_session_factory, pid, "The customer shall not pay by card.", actor="customer")
    finding_id = add_conflict(db_session_factory, newer, older)
    fake = use_llm()
    assert generate(client, project, newer).json()["created"] == 1
    sent = fake.question_calls[0][0]["findings"][0]
    assert (sent["finding_id"], sent["related_statement"]) == (finding_id, "The customer shall pay by card.")


def test_unknown_finding_ids_long_and_repeated_questions_are_dropped_and_three_is_the_cap(
    client, project, db_session_factory
):
    _, _, pid = project
    rid = seed_slow_report(db_session_factory, pid)
    older = seed_requirement(db_session_factory, pid, "The system shall load the sales report in 9 seconds.")
    conflict = add_conflict(db_session_factory, rid, older)
    ids = [f.id for f in findings_of(db_session_factory, rid) if f.category in ("vague", "missing_info", "conflict")]
    assert len(ids) >= 3
    fake = use_llm()
    fake.question_answers[rid] = {
        "questions": [
            {"finding_id": 99999, "text": "Unknown finding?"},
            {"finding_id": None, "text": "No finding?"},
            {"finding_id": ids[0], "text": "x" * 301},
            {"finding_id": ids[0], "text": "How fast should the sales report load, in seconds?"},
            {"finding_id": ids[0], "text": "Second question for the same problem?"},
            {"finding_id": ids[1], "text": "how fast should the sales report load, in seconds?"},
            {"finding_id": ids[1], "text": "Is a limit of 2 seconds fine?"},
            {"finding_id": conflict, "text": "Should it load in 9 seconds or faster?"},
            {"finding_id": ids[2], "text": "A fourth question?"},
        ]
    }
    assert generate(client, project, rid).json()["created"] == 3
    assert [q["text"] for q in questions(client, project, rid)] == [
        "How fast should the sales report load, in seconds?",
        "Is a limit of 2 seconds fine?",
        "Should it load in 9 seconds or faster?",
    ]


def test_not_enough_information(client, project, db_session_factory):
    _, _, pid = project
    rid = seed_slow_report(db_session_factory, pid)
    fake = use_llm()
    fake.question_answers[rid] = {"not_enough_information": True, "questions": []}
    body = generate(client, project, rid).json()
    assert body["created"] == 0
    assert body["items"] == [{"requirement_id": rid, "code": "REQ-001", "created": 0, "not_enough_information": True}]
    assert questions(client, project, rid) == []


def test_regenerate_keeps_edited_user_shared_and_answered_questions(client, project, db_session_factory):
    headers, base, pid = project
    rid = seed_slow_report(db_session_factory, pid)
    use_llm()
    # "fast", the client's "taratari" and no measurable value
    assert generate(client, project, rid).json()["created"] == 3
    first, second, third = questions(client, project, rid)
    client.patch(f"{base}/clarifications/questions/{first['id']}", json={"text": "Within how many seconds?"}, headers=headers)
    own = client.post(f"{base}/requirements/{rid}/questions", json={"text": "Who reads the report?"}, headers=headers)
    assert own.status_code == 201, own.text
    assert (own.json()["origin"], own.json()["status"]) == ("user", "accepted")

    # Regenerate: the untouched AI questions are written again, the edited one and the lead's stay
    assert generate(client, project, rid).json()["created"] == 2
    after = questions(client, project, rid)
    assert [(q["text"], q["status"]) for q in after[:2]] == [
        ("Within how many seconds?", "edited"),
        ("Who reads the report?", "accepted"),
    ]
    assert {q["id"] for q in after[2:]}.isdisjoint({second["id"], third["id"]})
    assert {q["finding_id"] for q in after[2:]} == {second["finding_id"], third["finding_id"]}

    # A shared question stays too
    assert client.post(f"{base}/clarifications/links", json={"question_ids": [after[2]["id"]]}, headers=headers).status_code == 201
    assert generate(client, project, rid).json()["created"] == 1  # only the last, untouched one again
    again = questions(client, project, rid)
    assert [q["id"] for q in again[:3]] == [q["id"] for q in after[:3]]
    assert again[2]["state"] == "shared" and again[3]["id"] != after[3]["id"]


def test_generate_for_the_whole_project_skips_closed_and_covered_requirements(client, project, db_session_factory):
    headers, base, pid = project
    a = seed_slow_report(db_session_factory, pid)
    b = seed_slow_report(db_session_factory, pid, status="rejected")
    c = seed_requirement(db_session_factory, pid, CLEAN)
    fake = use_llm()
    res = client.post(f"{base}/clarifications/generate", headers=headers)
    assert res.status_code == 200, res.text
    assert [i["requirement_id"] for i in res.json()["items"]] == [a]
    assert {item["requirement_id"] for call in fake.question_calls for item in call} == {a}
    # Already covered: nothing more to ask
    again = client.post(f"{base}/clarifications/generate", headers=headers).json()
    assert again["created"] == 0 and len(fake.question_calls) == 1
    assert b and c


def test_lead_edits_adds_and_deletes(client, project, db_session_factory):
    headers, base, pid = project
    rid = seed_slow_report(db_session_factory, pid)
    use_llm()
    generate(client, project, rid)
    q = questions(client, project, rid)[0]
    edited = client.patch(f"{base}/clarifications/questions/{q['id']}", json={"text": "  New text?  "}, headers=headers)
    assert (edited.json()["text"], edited.json()["status"]) == ("New text?", "edited")
    assert client.patch(f"{base}/clarifications/questions/{q['id']}", json={"text": " "}, headers=headers).status_code == 422
    assert client.delete(f"{base}/clarifications/questions/{q['id']}", headers=headers).status_code == 204
    assert q["id"] not in [x["id"] for x in questions(client, project, rid)]
    other_finding = client.post(f"{base}/requirements/{rid}/questions", json={"text": "Q?", "finding_id": 99999}, headers=headers)
    assert other_finding.status_code == 404


def test_a_shared_question_cannot_be_deleted(client, project, db_session_factory):
    headers, base, pid = project
    rid = seed_slow_report(db_session_factory, pid)
    use_llm()
    generate(client, project, rid)
    q = questions(client, project, rid)[0]
    link = client.post(f"{base}/clarifications/links", json={"question_ids": [q["id"]]}, headers=headers).json()
    res = client.delete(f"{base}/clarifications/questions/{q['id']}", headers=headers)
    assert res.status_code == 409 and "Revoke" in res.json()["detail"]
    client.post(f"{base}/clarifications/links/{link['id']}/revoke", headers=headers)
    assert client.delete(f"{base}/clarifications/questions/{q['id']}", headers=headers).status_code == 204


def test_without_a_model_generation_says_so(client, project, db_session_factory):
    _, _, pid = project
    rid = seed_slow_report(db_session_factory, pid)
    res = generate(client, project, rid)  # the test default: no LLM configured
    assert res.status_code == 503


@pytest.mark.parametrize(
    ("error", "code"),
    [(LLMError("The AI returned an invalid answer twice."), 502), (LLMUnavailableError("All AI models are busy."), 503)],
)
def test_llm_errors_are_clear_and_store_nothing(client, project, db_session_factory, error, code):
    _, _, pid = project
    rid = seed_slow_report(db_session_factory, pid)
    fake = use_llm()
    fake.questions_fail = error
    res = generate(client, project, rid)
    assert (res.status_code, res.json()["detail"]) == (code, str(error))
    assert questions(client, project, rid) == []


def test_other_owners_cannot_see_or_change_questions(client, project, make_user, db_session_factory):
    headers, base, pid = project
    rid = seed_slow_report(db_session_factory, pid)
    use_llm()
    generate(client, project, rid)
    q = questions(client, project, rid)[0]
    _, stranger = make_user()
    assert client.get(f"{base}/clarifications/questions", headers=stranger).status_code == 404
    assert client.patch(f"{base}/clarifications/questions/{q['id']}", json={"text": "x?"}, headers=stranger).status_code == 404
    assert client.post(f"{base}/requirements/{rid}/questions/generate", headers=stranger).status_code == 404
    # Their own project cannot reach this question either
    other = client.post("/projects", json={"name": "Other"}, headers=stranger).json()
    assert client.delete(f"/projects/{other['id']}/clarifications/questions/{q['id']}", headers=stranger).status_code == 404
