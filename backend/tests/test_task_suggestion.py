"""Assignee suggestion (AR-06): skills by words, synonyms and embeddings, workload, reasons, never an assignment."""

import pytest

from app.tasks.assign import normalise, word_match
from tests.case_helpers import call
from tests.clarify_helpers import make_project, use_llm
from tests.fake_llm import FakeLLM
from tests.fake_models import FakeEmbedder
from tests.tasks_helpers import (
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

BACKEND = [{"name": "Backend API", "description": "python fastapi server"}]
SYNC = "The admin shall call the API to sync the orders."
SYNC_STOCK = "The admin shall call the API to sync the stock."
SYNC_PRICES = "The admin shall call the API to sync the prices."


@pytest.fixture
def project(client, make_user):
    project = make_project(client, make_user)
    use_llm(FakeLLM())
    return project


def test_word_synonym_and_plural_matches():
    assert word_match("Python", normalise("Use Python for the job"))
    assert word_match("Backend", normalise("The admin shall call the API."))  # synonym
    assert word_match("UI", normalise("A new settings screen"))
    assert word_match("Report", normalise("Two reports"))  # plural
    assert word_match("Node.js", normalise("Written in node.js"))
    assert not word_match("React", normalise("Export the order list as CSV"))
    assert not word_match("Java", normalise("JavaScript only"))


def test_reason_names_matched_skills_and_open_tasks(client, project, db_session_factory):
    set_modules(client, project, BACKEND)
    rahim = add_member(client, project, "Rahim", ["Python", "FastAPI", "Figma"])
    add_member(client, project, "Karim", ["Photoshop"])
    approved(client, project, db_session_factory, SYNC, SYNC_STOCK, SYNC_PRICES)
    run_grouping(client, project)
    tasks = tasks_by_requirement(client, project)
    for code in ("REQ-001", "REQ-002"):
        assert call(client, project, "POST", f"/tasks/{tasks[code]['id']}/assign", {"member_id": rahim}).status_code == 200

    res = call(client, project, "POST", f"/tasks/{tasks['REQ-003']['id']}/suggest")
    assert res.status_code == 200, res.text
    suggestion = res.json()["suggestion"]
    assert suggestion["member"]["name"] == "Rahim"
    assert suggestion["reason"] == "Python, FastAPI skills; 2 open tasks"
    assert suggestion["alternative"]["name"] == "Karim"
    assert suggestion["status"] == "suggested"
    assert res.json()["assignee"] is None  # never assigned automatically


def test_skill_matched_by_embedding(client, project, db_session_factory, fake_models):
    fake_models.embedder.vectors["Charts"] = FakeEmbedder._bag_of_words(SALES_PDF)
    add_member(client, project, "Mina", ["Charts"])
    add_member(client, project, "Tanvir", ["Kotlin"])
    approved(client, project, db_session_factory, SALES_PDF)
    result = run_grouping(client, project)
    assert result["suggested"] == 1
    suggestion = tasks_by_requirement(client, project)["REQ-001"]["suggestion"]
    assert suggestion["member"]["name"] == "Mina"
    assert suggestion["reason"] == "Charts skill; 0 open tasks"


def test_workload_spreads_suggestions_within_one_run(client, project, db_session_factory, fake_models):
    embed_reports_apart(fake_models)
    add_member(client, project, "Rahim", ["PDF"])
    add_member(client, project, "Karim", ["PDF"])
    approved(client, project, db_session_factory, SALES_PDF, STOCK_PDF)
    run_grouping(client, project)
    tasks = tasks_by_requirement(client, project)
    first, second = tasks["REQ-001"]["suggestion"], tasks["REQ-002"]["suggestion"]
    assert {first["member"]["name"], second["member"]["name"]} == {"Rahim", "Karim"}
    assert second["reason"] == "PDF skill; 0 open tasks"
    workload = {w["member"]["name"]: w for w in board(client, project)["workload"]}
    assert workload["Rahim"]["pending_suggestions"] == 1 and workload["Karim"]["pending_suggestions"] == 1


def test_without_matching_skills_the_lowest_workload_wins(client, project, db_session_factory):
    add_member(client, project, "Rahim", ["Kotlin"])
    approved(client, project, db_session_factory, SALES_PDF)
    run_grouping(client, project)
    reason = tasks_by_requirement(client, project)["REQ-001"]["suggestion"]["reason"]
    assert reason == "No matching skills; 0 open tasks (lowest workload)"


def test_a_rejected_member_is_not_suggested_again(client, project, db_session_factory):
    add_member(client, project, "Rahim", ["PDF"])
    add_member(client, project, "Karim", ["Kotlin"])
    approved(client, project, db_session_factory, SALES_PDF)
    run_grouping(client, project)
    task = tasks_by_requirement(client, project)["REQ-001"]
    assert task["suggestion"]["member"]["name"] == "Rahim"

    rejected = call(client, project, "POST", f"/tasks/{task['id']}/suggestion/reject").json()
    assert rejected["suggestion"]["status"] == "rejected" and rejected["assignee"] is None
    again = call(client, project, "POST", f"/tasks/{task['id']}/suggest").json()
    assert again["suggestion"]["member"]["name"] == "Karim"
    assert again["suggestion"]["alternative"] is None

    call(client, project, "POST", f"/tasks/{task['id']}/suggestion/reject")
    res = call(client, project, "POST", "/tasks/suggest").json()
    assert res["suggested"] == 0
    task = tasks_by_requirement(client, project)["REQ-001"]
    assert task["suggestion"] is None


def test_an_assigned_task_gets_no_new_suggestion(client, project, db_session_factory):
    rahim = add_member(client, project, "Rahim", ["PDF"])
    approved(client, project, db_session_factory, SALES_PDF)
    run_grouping(client, project)
    task = tasks_by_requirement(client, project)["REQ-001"]
    call(client, project, "POST", f"/tasks/{task['id']}/assign", {"member_id": rahim})
    assert call(client, project, "POST", f"/tasks/{task['id']}/suggest").status_code == 409
    res = call(client, project, "POST", "/tasks/suggest").json()
    assert res["suggested"] == 0


def test_suggest_all_fills_tasks_made_before_members_existed(client, project, db_session_factory):
    approved(client, project, db_session_factory, SALES_PDF)
    result = run_grouping(client, project)
    assert result["suggested"] == 0
    assert "Add team members (with their skills) to get assignee suggestions." in result["notices"]
    assert tasks_by_requirement(client, project)["REQ-001"]["suggestion"] is None

    add_member(client, project, "Rahim", ["PDF"])
    res = call(client, project, "POST", "/tasks/suggest").json()
    assert res["suggested"] == 1
    assert tasks_by_requirement(client, project)["REQ-001"]["suggestion"]["member"]["name"] == "Rahim"


def test_words_only_when_the_embedding_model_is_unavailable(client, project, db_session_factory, fake_models):
    add_member(client, project, "Rahim", ["PDF"])
    approved(client, project, db_session_factory, SALES_PDF)
    fake_models.unavailable = "out of memory"
    result = run_grouping(client, project)
    assert "Skills were matched by words only: out of memory" in result["notices"]
    reason = tasks_by_requirement(client, project)["REQ-001"]["suggestion"]["reason"]
    assert reason == "PDF skill; 0 open tasks (skills matched by words only)"
