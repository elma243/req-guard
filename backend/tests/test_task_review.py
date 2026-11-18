"""The lead's decisions on tasks: accept, change or reject a suggestion, accept all, status, move, rename, filters."""

import pytest

from tests.case_helpers import call
from tests.clarify_helpers import make_project, use_llm
from tests.fake_llm import FakeLLM
from tests.tasks_helpers import (
    PAY,
    RESET,
    SALES_PDF,
    STOCK_PDF,
    add_member,
    approved,
    board,
    embed_reports_apart,
    run_grouping,
    set_modules,
    tasks_by_requirement,
)


@pytest.fixture
def project(client, make_user):
    project = make_project(client, make_user)
    set_modules(client, project)
    use_llm(FakeLLM())
    return project


@pytest.fixture
def team(client, project):
    return {
        "Rahim": add_member(client, project, "Rahim", ["PDF", "Python"]),
        "Karim": add_member(client, project, "Karim", ["bKash", "Payments"]),
        "Mina": add_member(client, project, "Mina", ["Security"], role="qa"),
    }


@pytest.fixture
def tasks(client, project, team, db_session_factory, fake_models):
    embed_reports_apart(fake_models)
    approved(client, project, db_session_factory, PAY, SALES_PDF, STOCK_PDF, RESET)
    run_grouping(client, project)
    return tasks_by_requirement(client, project)


def test_accept_change_and_reject(client, project, team, tasks):
    pay = tasks["REQ-001"]
    assert pay["suggestion"]["member"]["name"] == "Karim"
    accepted = call(client, project, "POST", f"/tasks/{pay['id']}/suggestion/accept").json()
    assert accepted["assignee"]["name"] == "Karim"
    assert accepted["status"] == "assigned"
    assert accepted["suggestion"]["status"] == "accepted"
    assert call(client, project, "POST", f"/tasks/{pay['id']}/suggestion/accept").status_code == 409

    sales = tasks["REQ-002"]
    changed = call(client, project, "POST", f"/tasks/{sales['id']}/assign", {"member_id": team["Mina"]}).json()
    assert changed["assignee"]["name"] == "Mina"
    assert changed["suggestion"]["status"] == "changed"
    assert changed["status"] == "assigned"

    stock = tasks["REQ-003"]
    rejected = call(client, project, "POST", f"/tasks/{stock['id']}/suggestion/reject").json()
    assert rejected["suggestion"]["status"] == "rejected"
    assert rejected["assignee"] is None and rejected["status"] == "suggested"


def test_accept_all_skips_tasks_that_need_review(client, project, tasks):
    reset = tasks["REQ-004"]
    rid = reset["requirement"]["id"]
    assert call(client, project, "POST", f"/requirements/{rid}/unapprove").status_code == 200

    res = call(client, project, "POST", "/tasks/accept-suggestions").json()
    assert res == {"accepted": 3, "skipped_needs_review": 1}
    after = tasks_by_requirement(client, project)
    assert all(after[c]["status"] == "assigned" for c in ("REQ-001", "REQ-002", "REQ-003"))
    assert after["REQ-004"]["assignee"] is None
    assert call(client, project, "POST", "/tasks/accept-suggestions").json()["accepted"] == 0


def test_status_flow_needs_an_assignee(client, project, team, tasks):
    tid = tasks["REQ-001"]["id"]
    res = call(client, project, "PATCH", f"/tasks/{tid}", {"status": "in_progress"})
    assert res.status_code == 409
    assert "Assign T-" in res.json()["detail"]

    call(client, project, "POST", f"/tasks/{tid}/suggestion/accept")
    assert call(client, project, "PATCH", f"/tasks/{tid}", {"status": "in_progress"}).json()["status"] == "in_progress"
    assert call(client, project, "PATCH", f"/tasks/{tid}", {"status": "done"}).json()["status"] == "done"

    unassigned = call(client, project, "PATCH", f"/tasks/{tid}", {"status": "suggested"}).json()
    assert unassigned["assignee"] is None
    assert unassigned["suggestion"]["status"] == "suggested"  # the suggestion can be accepted again


def test_workload_counts_assigned_in_progress_and_done(client, project, team, tasks):
    for code in ("REQ-002", "REQ-003"):
        call(client, project, "POST", f"/tasks/{tasks[code]['id']}/assign", {"member_id": team["Rahim"]})
    call(client, project, "PATCH", f"/tasks/{tasks['REQ-003']['id']}", {"status": "done"})
    workload = {w["member"]["name"]: w for w in board(client, project)["workload"]}
    assert workload["Rahim"] == {
        "member": {"id": team["Rahim"], "name": "Rahim", "role": "developer", "skills": ["PDF", "Python"]},
        "assigned": 1,
        "in_progress": 0,
        "done": 1,
        "pending_suggestions": 0,
    }
    assert workload["Karim"]["pending_suggestions"] == 1


def test_move_and_rename(client, project, tasks):
    groups = {g["name"]: g for g in board(client, project)["groups"]}
    reset = tasks["REQ-004"]
    moved = call(client, project, "PATCH", f"/tasks/{reset['id']}", {"group_id": groups["Payments"]["id"]}).json()
    assert moved["group_id"] == groups["Payments"]["id"]
    assert moved["group_origin"] == "user"
    assert moved["grouping_reason"] == "Moved here by the team lead (from 'Other requirements')."
    # The AI group it left is empty now and is removed
    assert "Other requirements" not in {g["name"] for g in board(client, project)["groups"]}

    renamed = call(client, project, "PATCH", f"/task-groups/{groups['Payments']['id']}", {"name": "Checkout"}).json()
    assert renamed["name"] == "Checkout"
    assert renamed["origin"] == "user" and renamed["status"] == "edited"
    assert len(renamed["tasks"]) == 2


def test_foreign_member_task_and_group_are_not_found(client, project, tasks, make_user):
    other = make_project(client, make_user, "Other shop")
    stranger = add_member(client, other, "Stranger", ["PDF"])
    tid = tasks["REQ-001"]["id"]
    assert call(client, project, "POST", f"/tasks/{tid}/assign", {"member_id": stranger}).status_code == 404
    assert call(client, other, "POST", f"/tasks/{tid}/suggestion/accept").status_code == 404
    assert call(client, project, "PATCH", f"/tasks/{tid}", {"group_id": 999}).status_code == 404
    assert call(client, project, "PATCH", "/task-groups/999", {"name": "X"}).status_code == 404


def test_filters(client, project, team, tasks):
    call(client, project, "POST", f"/tasks/{tasks['REQ-002']['id']}/assign", {"member_id": team["Mina"]})

    def codes(query: str) -> set[str]:
        return {t["requirement"]["code"] for g in board(client, project, query)["groups"] for t in g["tasks"]}

    # Assigned to Mina, plus the password reset suggested to her ("Security" matches "password")
    assert codes(f"?member_id={team['Mina']}") == {"REQ-002", "REQ-004"}
    assert codes(f"?member_id={team['Karim']}") == {"REQ-001"}  # a pending suggestion counts
    assert codes("?status=assigned") == {"REQ-002"}
    assert codes("?module=payments") == {"REQ-001"}
    assert codes("?needs_review=true") == set()
    data = board(client, project, "?status=assigned")
    assert data["total_tasks"] == 4 and data["modules"] == ["Payments"]
    assert len(data["workload"]) == 3


def test_removing_a_member_unassigns_their_open_tasks(client, project, team, tasks):
    pay, sales = tasks["REQ-001"], tasks["REQ-002"]
    call(client, project, "POST", f"/tasks/{pay['id']}/suggestion/accept")  # Karim
    assert sales["suggestion"]["member"]["name"] == "Rahim"
    assert call(client, project, "DELETE", f"/team/{team['Karim']}").status_code == 204
    assert call(client, project, "DELETE", f"/team/{team['Rahim']}").status_code == 204
    after = tasks_by_requirement(client, project)
    assert after["REQ-001"]["assignee"] is None and after["REQ-001"]["status"] == "suggested"
    assert after["REQ-002"]["suggestion"] is None
    res = call(client, project, "POST", "/tasks/suggest").json()
    assert res["suggested"] == 3  # REQ-004 was already suggested to Mina
    after = tasks_by_requirement(client, project)
    assert {t["suggestion"]["member"]["name"] for t in after.values()} == {"Mina"}  # the only member left
