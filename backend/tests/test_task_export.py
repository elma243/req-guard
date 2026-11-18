"""CSV export of tasks: header rows, one row per task, confirmed assignee apart from the suggestion, formula escape."""

import csv
import io

import pytest

from tests.case_helpers import call
from tests.clarify_helpers import make_project, use_llm
from tests.fake_llm import FakeLLM
from tests.tasks_helpers import PAY, REFUND, add_member, approved, run_grouping, set_modules, tasks_by_requirement


@pytest.fixture
def project(client, make_user):
    project = make_project(client, make_user, "দোকান Shop")
    set_modules(client, project)
    return project


def _rows(client, project) -> tuple[list[list[str]], str]:
    res = call(client, project, "GET", "/tasks/export.csv")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/csv")
    assert res.content.startswith("﻿".encode())
    return list(csv.reader(io.StringIO(res.content.decode("utf-8-sig")))), res.headers["content-disposition"]


def test_csv_lists_every_task_with_its_group_and_assignee(client, project, db_session_factory):
    rahim = add_member(client, project, "Rahim", ["bKash"])
    add_member(client, project, "Karim", ["Kotlin"])
    approved(client, project, db_session_factory, PAY, REFUND)
    run_grouping(client, project)
    tasks = tasks_by_requirement(client, project)
    call(client, project, "POST", f"/tasks/{tasks['REQ-001']['id']}/assign", {"member_id": rahim})
    call(client, project, "POST", f"/requirements/{tasks['REQ-002']['requirement']['id']}/unapprove")

    rows, disposition = _rows(client, project)
    assert rows[0] == ["Project", "দোকান Shop"]  # Bangla survives in UTF-8
    assert rows[1][0] == "Exported"
    assert rows[3][:6] == ["Group", "Module", "Task", "Requirement", "Statement", "Status"]
    first, second = rows[4], rows[5]
    assert first == ["Payments", "Payments", "T-001", "REQ-001", PAY, "Assigned", "Rahim", "", "", "", "", ""]
    assert second[:7] == ["Payments", "Payments", "T-002", "REQ-002", REFUND, "Not assigned", ""]
    assert second[7] == "Rahim"  # the pending suggestion, not an assignee
    assert second[8] == "bKash skill; 0 open tasks (+1 suggested in this run)"  # as it was when grouped
    assert second[9] == "yes: Approval removed"
    assert "-tasks-" in disposition and disposition.endswith('.csv"')


def test_cells_that_look_like_formulas_are_escaped(client, project, db_session_factory):
    use_llm(FakeLLM())
    approved(client, project, db_session_factory, PAY)
    run_grouping(client, project)
    group_id = next(iter(tasks_by_requirement(client, project).values()))["group_id"]
    call(client, project, "PATCH", f"/task-groups/{group_id}", {"name": "=HYPERLINK(1)"})
    rows, _ = _rows(client, project)
    assert rows[4][0] == "'=HYPERLINK(1)"
