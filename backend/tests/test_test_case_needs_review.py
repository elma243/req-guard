"""A new requirement version (an edit, or an accepted proposal) ends the approval and marks the requirement's
criteria and test cases "needs review"; removing the approval alone does not."""

import pytest

from app.models import ClarificationProposal
from tests.case_helpers import approve, call, generate, group, seed_approved
from tests.clarify_helpers import make_project, use_llm
from tests.fake_llm import FakeLLM

NEW_TEXT = "The admin shall export the order list as CSV within 5 seconds."


@pytest.fixture
def project(client, make_user):
    return make_project(client, make_user)


@pytest.fixture
def generated(client, project, db_session_factory):
    use_llm(FakeLLM())
    rid = seed_approved(client, project, db_session_factory)
    generate(client, project, rid)
    g = group(client, project, rid)
    call(client, project, "POST", f"/test-cases/{g['test_cases'][0]['id']}/accept")
    call(client, project, "POST", f"/test-cases/{g['test_cases'][1]['id']}/reject")
    return rid, g


def _flags(client, project, rid):
    g = group(client, project, rid)
    return {t["code"]: t["needs_review"] for t in g["test_cases"]}, {c["code"]: c["needs_review"] for c in g["criteria"]}


def test_editing_an_approved_requirement_marks_its_items(client, project, generated):
    rid, _ = generated
    res = call(client, project, "PATCH", f"/requirements/{rid}", {"statement": NEW_TEXT})
    assert res.status_code == 200, res.text
    assert res.json()["approval"] is None  # the edit ended the approval
    assert res.json()["test_cases_need_review"] == 2

    tests, criteria = _flags(client, project, rid)
    assert tests == {"TC-1": True, "TC-2": False, "TC-3": True}  # the rejected one is not marked
    assert criteria == {"AC-1": True, "AC-2": True}
    g = group(client, project, rid)
    assert g["requirement"]["approved"] is False and g["requirement"]["version"] == 2
    assert all(t["requirement_version"] == 1 for t in g["test_cases"])
    assert len(call(client, project, "GET", "/test-cases?needs_review=true").json()[0]["test_cases"]) == 2


def test_accepting_a_proposal_marks_them_too(client, project, db_session_factory, generated):
    rid, _ = generated
    with db_session_factory() as db:
        proposal = ClarificationProposal(
            requirement_id=rid, based_on_version=1, statement=NEW_TEXT, reason="From the answers.", origin="ai"
        )
        db.add(proposal)
        db.commit()
        pid = proposal.id
    res = call(client, project, "POST", f"/requirements/{rid}/proposals/{pid}/accept")
    assert res.status_code == 200, res.text
    assert _flags(client, project, rid)[0] == {"TC-1": True, "TC-2": False, "TC-3": True}


def test_removing_the_approval_alone_marks_nothing(client, project, generated):
    rid, _ = generated
    assert call(client, project, "POST", f"/requirements/{rid}/unapprove").status_code == 200
    tests, criteria = _flags(client, project, rid)
    assert not any(tests.values()) and not any(criteria.values())
    # Not approved now, so no generation, but the items stay on the page for review
    assert generate(client, project, rid).status_code == 409
    assert len(group(client, project, rid)["test_cases"]) == 3


def test_accepting_or_editing_clears_the_mark(client, project, generated):
    rid, g = generated
    call(client, project, "PATCH", f"/requirements/{rid}", {"statement": NEW_TEXT})
    tc1, tc3 = g["test_cases"][0]["id"], g["test_cases"][2]["id"]
    res = call(client, project, "POST", f"/test-cases/{tc1}/accept").json()
    tc = next(t for t in res["test_cases"] if t["id"] == tc1)
    assert tc["needs_review"] is False and tc["requirement_version"] == 2

    ac2 = g["criteria"][1]["id"]
    res = call(client, project, "PATCH", f"/criteria/{ac2}", {"text": "At the limit, the export still works."}).json()
    assert next(c for c in res["criteria"] if c["id"] == ac2)["needs_review"] is False
    assert next(t for t in res["test_cases"] if t["id"] == tc3)["needs_review"] is True


def test_generate_for_all_picks_it_up_after_re_approval(client, project, generated):
    rid, g = generated
    call(client, project, "PATCH", f"/requirements/{rid}", {"statement": NEW_TEXT})
    assert call(client, project, "POST", "/test-cases/generate").json()["items"] == []  # not approved
    approve(client, project, rid)

    body = call(client, project, "POST", "/test-cases/generate").json()
    assert [i["requirement_id"] for i in body["items"]] == [rid]
    after = group(client, project, rid)
    codes = {t["code"]: t for t in after["test_cases"]}
    assert "TC-3" not in codes  # the untouched suggestion written for version 1 was replaced
    assert codes["TC-1"]["needs_review"] is True  # the accepted one stays, still to be looked at
    assert all(codes[c]["requirement_version"] == 2 and not codes[c]["needs_review"] for c in ("TC-4", "TC-5", "TC-6"))

    # Nothing is left to do: running it again generates nothing
    assert call(client, project, "POST", "/test-cases/generate").json()["items"] == []
