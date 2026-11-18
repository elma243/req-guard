"""A task is marked "needs review" when its requirement gets a new version or loses its approval."""

import pytest

from tests.case_helpers import approve, call
from tests.clarify_helpers import make_project
from tests.tasks_helpers import PAY, approved, run_grouping, set_modules, tasks_by_requirement


@pytest.fixture
def project(client, make_user):
    project = make_project(client, make_user)
    set_modules(client, project)
    return project


@pytest.fixture
def task(client, project, db_session_factory):
    approved(client, project, db_session_factory, PAY)
    run_grouping(client, project)
    return tasks_by_requirement(client, project)["REQ-001"]


def test_an_edit_marks_the_task(client, project, task):
    rid = task["requirement"]["id"]
    res = call(client, project, "PATCH", f"/requirements/{rid}", {"statement": "The customer shall pay by bKash or Nagad."})
    assert res.status_code == 200, res.text
    assert res.json()["task"] == {
        "id": task["id"],
        "code": "T-001",
        "status": "suggested",
        "assignee_name": None,
        "needs_review": True,
    }
    after = tasks_by_requirement(client, project)["REQ-001"]
    assert after["needs_review"] is True
    assert after["needs_review_reason"] == "Requirement changed (v2)"
    assert after["requirement"]["version"] == 2 and after["requirement_version"] == 1
    assert after["group"] == "Payments"  # it keeps its group


def test_an_accepted_proposal_marks_the_task(client, project, task, db_session_factory):
    from app.models import ClarificationProposal

    rid = task["requirement"]["id"]
    with db_session_factory() as db:
        proposal = ClarificationProposal(
            requirement_id=rid,
            based_on_version=1,
            statement="The customer shall pay by bKash within 30 seconds.",
            reason="The client answered.",
        )
        db.add(proposal)
        db.commit()
        proposal_id = proposal.id
    res = call(client, project, "POST", f"/requirements/{rid}/proposals/{proposal_id}/accept")
    assert res.status_code == 200, res.text
    after = tasks_by_requirement(client, project)["REQ-001"]
    assert after["needs_review"] is True
    assert after["needs_review_reason"] == "Requirement changed (v2)"


def test_removing_the_approval_marks_the_task_and_reviewed_clears_it(client, project, task):
    rid = task["requirement"]["id"]
    assert call(client, project, "POST", f"/requirements/{rid}/unapprove").status_code == 200
    after = tasks_by_requirement(client, project)["REQ-001"]
    assert after["needs_review"] is True
    assert after["needs_review_reason"] == "Approval removed"
    assert after["requirement"]["approved"] is False

    reviewed = call(client, project, "POST", f"/tasks/{task['id']}/reviewed").json()
    assert reviewed["needs_review"] is False and reviewed["needs_review_reason"] is None


def test_grouping_after_reapproval_makes_no_second_task(client, project, task):
    rid = task["requirement"]["id"]
    call(client, project, "PATCH", f"/requirements/{rid}", {"statement": "The customer shall pay by bKash or Nagad."})
    approve(client, project, rid)
    result = run_grouping(client, project)
    assert result["created_tasks"] == 0
    after = tasks_by_requirement(client, project)
    assert list(after) == ["REQ-001"] and after["REQ-001"]["id"] == task["id"]
    assert after["REQ-001"]["needs_review"] is True  # the lead still has to look at it

    reviewed = call(client, project, "POST", f"/tasks/{task['id']}/reviewed").json()
    assert reviewed["requirement_version"] == 2
