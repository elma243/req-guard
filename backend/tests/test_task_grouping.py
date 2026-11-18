"""Grouping approved requirements into task groups (FR-11): module first, then embedding clusters the AI names."""

import pytest

from app.ai.llm import LLMError
from tests.case_helpers import call
from tests.clarify_helpers import make_project, seed_requirement, use_llm
from tests.fake_llm import FakeLLM
from tests.fake_models import vector_at
from tests.tasks_helpers import (
    PAY,
    REFUND,
    RESET,
    SALES_PDF,
    STOCK_PDF,
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
    return project


@pytest.fixture
def five(client, project, db_session_factory, fake_models):
    embed_reports_apart(fake_models)
    return approved(client, project, db_session_factory, PAY, REFUND, SALES_PDF, STOCK_PDF, RESET)


def test_groups_by_module_then_by_cluster_with_reasons(client, project, five):
    llm = use_llm(FakeLLM())
    llm.group_names["G1"] = {"name": "Report Export", "reason": "Both export a report as PDF."}
    result = run_grouping(client, project)
    assert result["created_tasks"] == 5
    assert result["created_groups"] == 3

    tasks = tasks_by_requirement(client, project)
    assert tasks["REQ-001"]["group"] == "Payments"
    assert tasks["REQ-001"]["group_kind"] == "module"
    assert tasks["REQ-001"]["grouping_reason"].startswith("Matches module 'Payments' (score ")
    assert tasks["REQ-002"]["group"] == "Payments"

    assert tasks["REQ-003"]["group"] == "Report Export"
    assert tasks["REQ-003"]["group_kind"] == "cluster"
    assert tasks["REQ-003"]["grouping_reason"] == "No module matched; similar to REQ-004 (similarity 0.90)."
    assert tasks["REQ-004"]["group"] == "Report Export"

    assert tasks["REQ-005"]["group"] == "Other requirements"
    assert tasks["REQ-005"]["grouping_reason"] == "No module matched and not similar to other unmatched requirements."

    groups = {g["name"]: g for g in board(client, project)["groups"]}
    assert groups["Report Export"]["reason"] == "Both export a report as PDF."
    assert groups["Report Export"]["ai_model"] == "fake-model"
    assert groups["Payments"]["reason"] == "A module of the project."
    # Only the cluster was sent to the AI, with its statements
    assert llm.group_name_calls == [[{"ref": "G1", "statements": [SALES_PDF, STOCK_PDF]}]]

    t = tasks["REQ-001"]
    assert t["code"] == "T-001" and t["status"] == "suggested" and t["assignee"] is None
    assert t["requirement_version"] == 1 and t["group_origin"] == "ai"


def test_unapproved_requirements_get_no_task(client, project, db_session_factory):
    seed_requirement(db_session_factory, project[2], PAY)  # accepted but not approved
    result = run_grouping(client, project)
    assert result["created_tasks"] == 0
    assert result["notices"] == ["Every approved requirement already has a task."]
    assert board(client, project)["groups"] == []


def test_a_second_run_adds_only_new_requirements_and_joins_existing_groups(
    client, project, db_session_factory, fake_models, five
):
    use_llm(FakeLLM())
    run_grouping(client, project)
    before = tasks_by_requirement(client, project)
    fake_models.embedder.vectors["The admin shall export the order report as PDF."] = vector_at(0.8, axis=2)
    approved(client, project, db_session_factory, "The admin shall export the order report as PDF.", "The customer shall pay a fee by bKash.")

    result = run_grouping(client, project)
    assert result["created_tasks"] == 2
    assert result["created_groups"] == 0
    after = tasks_by_requirement(client, project)
    assert after["REQ-006"]["group"] == "Fake Group 1"
    assert after["REQ-006"]["grouping_reason"].startswith("No module matched; similar to the tasks of this group")
    assert after["REQ-007"]["group"] == "Payments"
    for code, task in before.items():
        assert after[code]["id"] == task["id"] and after[code]["group"] == task["group"]


def test_renamed_and_moved_items_stay_as_the_lead_left_them(client, project, db_session_factory, five):
    use_llm(FakeLLM())
    run_grouping(client, project)
    tasks = tasks_by_requirement(client, project)
    groups = {g["name"]: g["id"] for g in board(client, project)["groups"]}
    assert call(client, project, "PATCH", f"/task-groups/{groups['Fake Group 1']}", {"name": "Reports"}).status_code == 200
    moved = call(client, project, "PATCH", f"/tasks/{tasks['REQ-005']['id']}", {"group_id": groups["Payments"]})
    assert moved.status_code == 200

    approved(client, project, db_session_factory, "The customer shall pay a fee by bKash.")
    run_grouping(client, project)
    after = tasks_by_requirement(client, project)
    assert after["REQ-003"]["group"] == "Reports"
    assert after["REQ-005"]["group"] == "Payments"
    assert after["REQ-005"]["group_origin"] == "user"


def test_without_an_llm_groups_are_named_by_rule(client, project, five):
    result = run_grouping(client, project)
    assert "named from their most frequent words" in result["notices"][0]
    groups = {g["kind"]: g for g in board(client, project)["groups"]}
    assert groups["cluster"]["name"] == "Export, Report, Pdf"
    assert groups["cluster"]["ai_model"] == "rule-based"


def test_an_llm_error_falls_back_to_rule_names(client, project, five):
    llm = use_llm(FakeLLM())
    llm.group_names_fail = LLMError("All AI models are busy right now.")
    result = run_grouping(client, project)
    assert result["created_tasks"] == 5
    assert "All AI models are busy right now." in result["notices"][0]
    assert any(g["name"] == "Export, Report, Pdf" for g in board(client, project)["groups"])


def test_an_unusable_ai_name_falls_back_to_the_rule(client, project, five):
    llm = use_llm(FakeLLM())
    llm.group_names["G1"] = {"name": "A name that is much too long for a group", "reason": "x"}
    run_grouping(client, project)
    assert any(g["name"] == "Export, Report, Pdf" for g in board(client, project)["groups"])


def test_without_the_embedding_model_unmatched_ones_go_to_one_group(client, project, five, fake_models):
    fake_models.unavailable = "no network for the first download"
    result = run_grouping(client, project)
    assert result["created_tasks"] == 5
    assert any("embedding model is unavailable: no network" in n for n in result["notices"])
    tasks = tasks_by_requirement(client, project)
    assert {tasks[c]["group"] for c in ("REQ-003", "REQ-004", "REQ-005")} == {"Other requirements"}
    assert tasks["REQ-001"]["group"] == "Payments"


def test_a_removed_module_no_longer_groups(client, project, db_session_factory, five):
    set_modules(client, project, [{"name": "Reports", "description": "pdf export"}])
    result = run_grouping(client, project)
    tasks = tasks_by_requirement(client, project)
    # Validation stored "Payments" before the modules changed; it is no longer a module of the project
    assert tasks["REQ-001"]["group_kind"] != "module"
    assert result["created_tasks"] == 5


def test_another_owners_project_is_not_found(client, project, make_user):
    _, other_headers = make_user()
    _, base, _ = project
    assert client.post(f"{base}/tasks/group", headers=other_headers).status_code == 404
    assert client.get(f"{base}/tasks", headers=other_headers).status_code == 404
