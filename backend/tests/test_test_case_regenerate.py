"""Generating again replaces only untouched AI suggestions (rule 2); everything a person touched stays."""

import pytest

from tests.clarify_helpers import make_project, use_llm
from tests.fake_llm import FakeLLM
from tests.case_helpers import call, generate, group, seed_approved, case_body


@pytest.fixture
def project(client, make_user):
    return make_project(client, make_user)


@pytest.fixture
def fake():
    return use_llm(FakeLLM())


def test_regenerate_keeps_accepted_edited_user_and_rejected_items(client, project, db_session_factory, fake):
    rid = seed_approved(client, project, db_session_factory)
    generate(client, project, rid)
    g = group(client, project, rid)
    tc1, tc2, tc3 = (t["id"] for t in g["test_cases"])
    ac1, ac2 = (c["id"] for c in g["criteria"])

    call(client, project, "POST", f"/test-cases/{tc1}/accept")  # also accepts AC-1
    call(client, project, "POST", f"/test-cases/{tc2}/reject")
    # TC-3 (on AC-2) stays an untouched suggestion; AC-2 too
    manual = call(client, project, "POST", f"/requirements/{rid}/test-cases", case_body(criterion_id=ac1))
    assert manual.status_code == 201, manual.text

    res = generate(client, project, rid)
    assert res.status_code == 200, res.text
    g = group(client, project, rid)
    by_code = {t["code"]: t for t in g["test_cases"]}
    # TC-3 was replaced; the new ones get new numbers (codes are never reused)
    assert set(by_code) == {"TC-1", "TC-2", "TC-4", "TC-5", "TC-6", "TC-7"}
    assert by_code["TC-1"]["status"] == "accepted" and by_code["TC-2"]["status"] == "rejected"
    assert by_code["TC-4"]["origin"] == "user"
    assert all("(run 2)" in by_code[c]["title"] for c in ("TC-5", "TC-6", "TC-7"))
    assert tc3 not in {t["id"] for t in g["test_cases"]}

    criteria = {c["code"]: c for c in g["criteria"]}
    assert criteria["AC-1"]["id"] == ac1 and criteria["AC-1"]["status"] == "accepted"
    assert ac2 not in {c["id"] for c in g["criteria"]}  # an untouched suggestion no kept test case used
    assert {"AC-3", "AC-4"} <= set(criteria)

    # The second prompt saw what is kept: the accepted criterion and the kept titles, rejected ones too
    sent = fake.test_case_calls[1]
    assert [c["ref"] for c in sent["existing_criteria"]] == [f"E{ac1}"]
    assert len(sent["existing_test_cases"]) == 3


def test_a_suggested_criterion_used_by_a_kept_test_case_stays(client, project, db_session_factory, fake):
    rid = seed_approved(client, project, db_session_factory)
    generate(client, project, rid)
    g = group(client, project, rid)
    ac2 = g["criteria"][1]
    tc3 = g["test_cases"][2]
    assert ac2["status"] == "suggested" and tc3["criterion_id"] == ac2["id"]

    # Rejecting TC-3 keeps it; the criterion it points at must stay as well
    call(client, project, "POST", f"/test-cases/{tc3['id']}/reject")
    generate(client, project, rid)
    g = group(client, project, rid)
    assert ac2["id"] in {c["id"] for c in g["criteria"]}
    kept = next(t for t in g["test_cases"] if t["id"] == tc3["id"])
    assert kept["criterion_code"] == "AC-2"


def test_a_failed_regeneration_changes_nothing(client, project, db_session_factory, fake):
    from app.ai.llm import LLMError

    rid = seed_approved(client, project, db_session_factory)
    generate(client, project, rid)
    before = group(client, project, rid)
    fake.test_cases_fail = LLMError("Groq returned an error (HTTP 500)")
    assert generate(client, project, rid).status_code == 502
    assert group(client, project, rid) == before


def test_new_criteria_can_link_to_kept_ones(client, project, db_session_factory, fake):
    rid = seed_approved(client, project, db_session_factory)
    generate(client, project, rid)
    g = group(client, project, rid)
    ac1 = g["criteria"][0]["id"]
    call(client, project, "POST", f"/criteria/{ac1}/accept")
    statement = g["requirement"]["statement"]
    fake.test_case_answers[statement] = {
        "criteria": [],
        "test_cases": [
            {"title": f"Extra {n}", "type": "edge", "given": "g", "when": "w", "then": "t", "criterion_ref": f"E{ac1}"}
            for n in range(2)
        ],
    }
    body = generate(client, project, rid).json()
    assert (body["created_test_cases"], body["created_criteria"]) == (2, 0)
    g = group(client, project, rid)
    assert [t["criterion_id"] for t in g["test_cases"]] == [ac1, ac1]
