"""Generating acceptance criteria and Given/When/Then test cases (AR-05) with a fake LLM: only for approved
requirements, stored as AI suggestions linked to their criteria, and nothing stored on "not enough information"."""

import pytest

from app.ai import test_cases as generator
from app.ai.llm import LLMError, LLMUnavailableError
from tests.clarify_helpers import CLEAN, make_project, seed_requirement, use_llm
from tests.fake_llm import FakeLLM
from tests.case_helpers import call, generate, group, requirement_row, seed_approved

OTHER = "The admin shall print the invoice of an order."
THIRD = "The customer shall see the delivery date of an order."


@pytest.fixture
def project(client, make_user):
    return make_project(client, make_user)


@pytest.fixture
def fake():
    return use_llm(FakeLLM(model="groq:fake-model"))


def test_generate_stores_ai_suggestions_linked_to_criteria(client, project, db_session_factory, fake):
    rid = seed_approved(client, project, db_session_factory)
    res = generate(client, project, rid)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["created_test_cases"] == 3 and body["created_criteria"] == 2
    assert body["items"] == [
        {
            "requirement_id": rid,
            "code": "REQ-001",
            "created_test_cases": 3,
            "created_criteria": 2,
            "not_enough_information": False,
            "reason": "",
        }
    ]

    g = group(client, project, rid)
    assert g["requirement"]["approved"] is True
    assert [c["code"] for c in g["criteria"]] == ["AC-1", "AC-2"]
    assert [t["code"] for t in g["test_cases"]] == ["TC-1", "TC-2", "TC-3"]
    assert [t["type"] for t in g["test_cases"]] == ["positive", "negative", "edge"]
    assert [t["criterion_code"] for t in g["test_cases"]] == ["AC-1", "AC-1", "AC-2"]
    for item in g["criteria"] + g["test_cases"]:
        assert item["origin"] == "ai" and item["status"] == "suggested"
        assert item["ai_model"] == "groq:fake-model" and item["requirement_version"] == 1
        assert item["needs_review"] is False and len(item["versions"]) == 1
    assert g["criteria"][0]["test_case_count"] == 2

    # The prompt got the statement and the client's sentence, and no existing items
    sent = fake.test_case_calls[0]
    assert sent["requirement"]["statement"] == CLEAN
    assert sent["requirement"]["client_sentences"] == [CLEAN]
    assert sent["existing_criteria"] == "(none)" and sent["existing_test_cases"] == "(none)"

    listed = call(client, project, "GET", "/requirements").json()[0]
    assert (listed["test_case_count"], listed["accepted_test_cases"], listed["test_cases_need_review"]) == (3, 0, 0)


def test_only_approved_requirements_get_test_cases(client, project, db_session_factory, fake):
    rid = seed_requirement(db_session_factory, project[2], CLEAN)  # accepted, not approved
    res = generate(client, project, rid)
    assert res.status_code == 409 and "Approve REQ-001 first" in res.json()["detail"]
    assert fake.test_case_calls == []
    assert call(client, project, "GET", "/test-cases").json() == []  # not approved and no items: no group


def test_not_enough_information_stores_nothing(client, project, db_session_factory, fake):
    rid = seed_approved(client, project, db_session_factory)
    fake.test_case_answers[CLEAN] = {
        "not_enough_information": True,
        "reason": "The requirement has no observable result.",
        "criteria": [{"ref": "C1", "text": "Ignored"}],
    }
    body = generate(client, project, rid).json()
    assert body["created_test_cases"] == 0
    assert body["items"][0]["not_enough_information"] is True
    assert body["items"][0]["reason"] == "The requirement has no observable result."
    g = group(client, project, rid)
    assert g["criteria"] == [] and g["test_cases"] == []


def _case(title, ref, type="positive"):
    return {"title": title, "type": type, "given": "g", "when": "w", "then": "t", "criterion_ref": ref}


def test_unusable_items_are_dropped_and_fewer_than_two_stores_nothing(client, project, db_session_factory, fake):
    rid = seed_approved(client, project, db_session_factory)
    fake.test_case_answers[CLEAN] = {
        "criteria": [{"ref": "C1", "text": "It works."}],
        "test_cases": [
            _case("Good one", "C1"),
            _case("Unknown criterion", "C9"),
            _case("Good one", "C1", "negative"),  # repeated title
            {**_case("Empty step", "C1"), "then": "  "},
        ],
    }
    body = generate(client, project, rid).json()
    assert body["items"][0]["not_enough_information"] is True
    assert "fewer than 2 usable" in body["items"][0]["reason"]
    assert group(client, project, rid)["test_cases"] == []


def test_cap_of_five_and_unused_new_criteria_are_left_out(client, project, db_session_factory, fake):
    rid = seed_approved(client, project, db_session_factory)
    fake.test_case_answers[CLEAN] = {
        "criteria": [
            {"ref": "C1", "text": "Used."},
            {"ref": "C2", "text": "Never used by a test case."},
            {"ref": "C1", "text": "Same ref again."},
            {"ref": "C3", "text": "x" * 600},  # too long
        ],
        "test_cases": [_case(f"Case {n}", "C1") for n in range(7)],
    }
    body = generate(client, project, rid).json()
    assert (body["created_test_cases"], body["created_criteria"]) == (5, 1)
    g = group(client, project, rid)
    assert [c["text"] for c in g["criteria"]] == ["Used."]
    assert [t["title"] for t in g["test_cases"]] == [f"Case {n}" for n in range(5)]


def test_no_llm_gives_503_and_llm_errors_give_502_or_503(client, project, db_session_factory):
    rid = seed_approved(client, project, db_session_factory)
    res = generate(client, project, rid)  # the autouse fixture has no model configured
    assert res.status_code == 503

    fake = use_llm()
    fake.test_cases_fail = LLMError("The AI returned an invalid answer twice. Try again.")
    res = generate(client, project, rid)
    assert res.status_code == 502 and "invalid answer twice" in res.json()["detail"]
    fake.test_cases_fail = LLMUnavailableError("All AI models are busy or out of free quota. Try again later.")
    assert generate(client, project, rid).status_code == 503
    assert group(client, project, rid)["test_cases"] == []


def test_generate_for_all_approved_covers_missing_ones_only(client, project, db_session_factory, fake):
    first = seed_approved(client, project, db_session_factory)
    generate(client, project, first)
    second = seed_approved(client, project, db_session_factory, OTHER)
    seed_requirement(db_session_factory, project[2], THIRD)  # not approved
    fake.test_case_calls.clear()

    body = call(client, project, "POST", "/test-cases/generate").json()
    assert [i["requirement_id"] for i in body["items"]] == [second]
    assert [c["requirement"]["statement"] for c in fake.test_case_calls] == [OTHER]
    assert body["skipped"] == [] and body["error"] is None


def test_a_later_error_keeps_the_earlier_results(client, project, db_session_factory, fake):
    first = seed_approved(client, project, db_session_factory)
    second = seed_approved(client, project, db_session_factory, OTHER)
    fake.test_cases_fail = LLMError("Groq returned an error (HTTP 500)")
    fake.test_cases_fail_on = 2
    body = call(client, project, "POST", "/test-cases/generate").json()
    assert [i["requirement_id"] for i in body["items"]] == [first]
    assert body["error"] == "Groq returned an error (HTTP 500)" and body["skipped"] == ["REQ-002"]
    assert len(group(client, project, first)["test_cases"]) == 3
    assert group(client, project, second)["test_cases"] == []


def test_requirements_past_the_time_limit_are_skipped(client, project, db_session_factory, fake, monkeypatch):
    seed_approved(client, project, db_session_factory)
    seed_approved(client, project, db_session_factory, OTHER)
    seed_approved(client, project, db_session_factory, THIRD)
    clock = iter([0.0, 0.0, 140.0, 140.0])  # start, first check, second check (too late), ...
    monkeypatch.setattr(generator, "now", lambda: next(clock, 200.0))
    body = call(client, project, "POST", "/test-cases/generate").json()
    assert [i["code"] for i in body["items"]] == ["REQ-001"]
    assert body["skipped"] == ["REQ-002", "REQ-003"]


def test_another_owners_project_is_404(client, project, make_user, db_session_factory, fake):
    rid = seed_approved(client, project, db_session_factory)
    other_headers, _, _ = make_project(client, make_user, "Other")
    _, base, _ = project
    assert client.post(f"{base}/requirements/{rid}/test-cases/generate", headers=other_headers).status_code == 404
    assert client.get(f"{base}/test-cases", headers=other_headers).status_code == 404
    assert client.get(f"{base}/test-cases/export.csv", headers=other_headers).status_code == 404
    assert requirement_row(db_session_factory, rid).test_cases == []
