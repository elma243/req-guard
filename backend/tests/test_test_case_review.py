"""Reviewing criteria and test cases: accept, edit (a new version, rule 6), reject, add by hand, and the filters."""

import pytest

from tests.clarify_helpers import CLEAN, make_project, seed_requirement, use_llm
from tests.fake_llm import FakeLLM
from tests.case_helpers import call, generate, group, seed_approved, case_body

OTHER = "The admin shall print the invoice of an order."


@pytest.fixture
def project(client, make_user):
    return make_project(client, make_user)


@pytest.fixture
def generated(client, project, db_session_factory):
    use_llm(FakeLLM())
    rid = seed_approved(client, project, db_session_factory)
    generate(client, project, rid)
    return rid, group(client, project, rid)


def test_edit_adds_a_version_written_by_the_lead(client, project, generated):
    rid, g = generated
    tc = g["test_cases"][0]
    ac2 = g["criteria"][1]["id"]
    res = call(
        client, project, "PATCH", f"/test-cases/{tc['id']}", case_body("Export the list", ac2, "edge")
    )
    assert res.status_code == 200, res.text
    edited = next(t for t in res.json()["test_cases"] if t["id"] == tc["id"])
    assert edited["status"] == "edited" and edited["origin"] == "ai"  # it stays the AI's item, edited
    assert edited["title"] == "Export the list" and edited["type"] == "edge" and edited["criterion_code"] == "AC-2"
    assert [v["version"] for v in edited["versions"]] == [1, 2]
    assert edited["versions"][0]["title"] == tc["title"] and edited["versions"][0]["origin"] == "ai"
    assert edited["versions"][1]["origin"] == "user" and edited["versions"][1]["created_by_name"]
    # Editing is a review: the suggested criterion it now checks is accepted too
    assert next(c for c in res.json()["criteria"] if c["id"] == ac2)["status"] == "accepted"

    # The same content again adds no version
    again = call(client, project, "PATCH", f"/test-cases/{tc['id']}", case_body("Export the list", ac2, "edge"))
    assert len(next(t for t in again.json()["test_cases"] if t["id"] == tc["id"])["versions"]) == 2


def test_accept_also_accepts_the_suggested_criterion(client, project, generated):
    _, g = generated
    res = call(client, project, "POST", f"/test-cases/{g['test_cases'][0]['id']}/accept")
    body = res.json()
    assert body["test_cases"][0]["status"] == "accepted"
    assert body["criteria"][0]["status"] == "accepted"  # AC-1
    assert body["criteria"][1]["status"] == "suggested"  # AC-2 untouched


def test_reject_and_accept_again(client, project, generated):
    _, g = generated
    tid = g["test_cases"][1]["id"]
    assert call(client, project, "POST", f"/test-cases/{tid}/reject").json()["test_cases"][1]["status"] == "rejected"
    assert call(client, project, "POST", f"/test-cases/{tid}/accept").json()["test_cases"][1]["status"] == "accepted"


def test_rejecting_a_criterion_in_use_is_refused(client, project, generated):
    _, g = generated
    ac1 = g["criteria"][0]["id"]
    res = call(client, project, "POST", f"/criteria/{ac1}/reject")
    assert res.status_code == 409 and "TC-1, TC-2 verify AC-1" in res.json()["detail"]
    for t in g["test_cases"][:2]:
        call(client, project, "POST", f"/test-cases/{t['id']}/reject")
    res = call(client, project, "POST", f"/criteria/{ac1}/reject")
    assert res.status_code == 200 and res.json()["criteria"][0]["status"] == "rejected"
    # A rejected criterion cannot be linked to
    linked = call(client, project, "PATCH", f"/test-cases/{g['test_cases'][2]['id']}", case_body(criterion_id=ac1))
    assert linked.status_code == 409


def test_criteria_edit_accept_and_add(client, project, generated):
    rid, g = generated
    ac = g["criteria"][0]
    res = call(client, project, "PATCH", f"/criteria/{ac['id']}", {"text": "Valid requests return the list."})
    edited = res.json()["criteria"][0]
    assert edited["status"] == "edited" and edited["text"] == "Valid requests return the list."
    assert [v["origin"] for v in edited["versions"]] == ["ai", "user"]
    assert call(client, project, "POST", f"/criteria/{ac['id']}/accept").json()["criteria"][0]["status"] == "accepted"

    added = call(client, project, "POST", f"/requirements/{rid}/criteria", {"text": "Only admins can export."})
    assert added.status_code == 201
    new = added.json()["criteria"][-1]
    assert (new["code"], new["origin"], new["status"]) == ("AC-3", "user", "accepted")
    assert call(client, project, "POST", f"/requirements/{rid}/criteria", {"text": "  "}).status_code == 422


def test_add_by_hand_only_for_approved_requirements(client, project, db_session_factory, generated):
    rid, g = generated
    res = call(client, project, "POST", f"/requirements/{rid}/test-cases", case_body("By hand", g["criteria"][0]["id"]))
    assert res.status_code == 201, res.text
    added = res.json()["test_cases"][-1]
    assert (added["code"], added["origin"], added["status"], added["criterion_code"]) == ("TC-4", "user", "accepted", "AC-1")
    assert added["versions"][0]["origin"] == "user"

    other = seed_requirement(db_session_factory, project[2], OTHER)  # not approved
    res = call(client, project, "POST", f"/requirements/{other}/test-cases", case_body())
    assert res.status_code == 409
    assert call(client, project, "POST", f"/requirements/{other}/criteria", {"text": "x"}).status_code == 409


def test_foreign_criteria_and_items_are_404(client, project, make_user, db_session_factory, generated):
    rid, g = generated
    other = seed_approved(client, project, db_session_factory, OTHER)
    # A criterion of another requirement
    res = call(client, project, "POST", f"/requirements/{other}/test-cases", case_body(criterion_id=g["criteria"][0]["id"]))
    assert res.status_code == 404
    # Another owner cannot touch the items
    other_headers, other_base, _ = make_project(client, make_user, "Other")
    tid = g["test_cases"][0]["id"]
    assert client.post(f"{other_base}/test-cases/{tid}/accept", headers=other_headers).status_code == 404
    assert client.patch(f"{other_base}/criteria/{g['criteria'][0]['id']}", json={"text": "x"}, headers=other_headers).status_code == 404


def test_list_filters(client, project, db_session_factory, generated):
    rid, g = generated
    other = seed_approved(client, project, db_session_factory, OTHER)  # approved, no items yet
    call(client, project, "POST", f"/test-cases/{g['test_cases'][0]['id']}/accept")
    call(client, project, "POST", f"/test-cases/{g['test_cases'][1]['id']}/reject")

    every = call(client, project, "GET", "/test-cases").json()
    assert [x["requirement"]["id"] for x in every] == [rid, other]
    assert every[1]["test_cases"] == [] and every[1]["requirement"]["approved"] is True

    accepted = call(client, project, "GET", "/test-cases?status=accepted").json()
    assert len(accepted) == 1
    assert [t["code"] for t in accepted[0]["test_cases"]] == ["TC-1"]
    assert [c["code"] for c in accepted[0]["criteria"]] == ["AC-1"]

    edge = call(client, project, "GET", "/test-cases?type=edge").json()
    assert [t["code"] for t in edge[0]["test_cases"]] == ["TC-3"] and edge[0]["criteria"] == []

    only = call(client, project, "GET", f"/test-cases?requirement_id={other}").json()
    assert [x["requirement"]["id"] for x in only] == [other]
    assert call(client, project, "GET", "/test-cases?needs_review=true").json() == []
    assert call(client, project, "GET", "/test-cases?status=bogus").status_code == 422


def test_requirement_counts(client, project, generated):
    _, g = generated
    call(client, project, "POST", f"/test-cases/{g['test_cases'][0]['id']}/accept")
    call(client, project, "POST", f"/test-cases/{g['test_cases'][1]['id']}/reject")
    row = call(client, project, "GET", "/requirements").json()[0]
    assert (row["test_case_count"], row["accepted_test_cases"], row["test_cases_need_review"]) == (2, 1, 0)
    assert row["current"]["statement"] == CLEAN
