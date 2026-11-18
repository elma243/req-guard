"""Push to Jira (mocked with respx): only assigned, unpushed tasks; keys are stored at once; clear errors."""

import json

import httpx
import pytest
import respx

from app.core.config import settings
from tests.case_helpers import call
from tests.clarify_helpers import make_project
from tests.tasks_helpers import PAY, REFUND, RESET, add_member, approved, run_grouping, set_modules, tasks_by_requirement

BASE = "https://acme-dev.atlassian.net"
ISSUE_URL = f"{BASE}/rest/api/3/issue"


@pytest.fixture
def jira_env(monkeypatch):
    monkeypatch.setattr(settings, "jira_base_url", BASE)
    monkeypatch.setattr(settings, "jira_email", "lead@example.com")
    monkeypatch.setattr(settings, "jira_api_token", "secret-token")


@pytest.fixture
def project(client, make_user):
    project = make_project(client, make_user)
    set_modules(client, project)
    return project


@pytest.fixture
def tasks(client, project, db_session_factory):
    rahim = add_member(client, project, "Rahim", ["bKash"])
    approved(client, project, db_session_factory, PAY, REFUND, RESET)
    run_grouping(client, project)
    tasks = tasks_by_requirement(client, project)
    for code in ("REQ-001", "REQ-002"):
        call(client, project, "POST", f"/tasks/{tasks[code]['id']}/assign", {"member_id": rahim})
    return tasks


def _set_target(client, project, key="dev", issue_type="Story"):
    res = call(client, project, "PATCH", "", {"jira_task_project_key": key, "jira_task_issue_type": issue_type})
    assert res.status_code == 200, res.text
    return res.json()


def _push(client, project, ids):
    return call(client, project, "POST", "/tasks/jira-push", {"task_ids": ids})


def test_project_setting_is_validated_and_can_be_cleared(client, project):
    assert _set_target(client, project)["jira_task_project_key"] == "DEV"
    assert call(client, project, "PATCH", "", {"jira_task_project_key": "not a key"}).status_code == 422
    assert call(client, project, "PATCH", "", {"jira_task_project_key": ""}).json()["jira_task_project_key"] is None


@respx.mock
def test_creates_issues_and_stores_the_keys(client, project, tasks, jira_env):
    _set_target(client, project)
    keys = iter(["DEV-1", "DEV-2"])
    route = respx.post(ISSUE_URL).mock(side_effect=lambda request: httpx.Response(201, json={"id": "1", "key": next(keys)}))
    ids = [tasks[c]["id"] for c in ("REQ-001", "REQ-002", "REQ-003")]

    res = _push(client, project, ids)
    assert res.status_code == 200, res.text
    body = res.json()
    assert [p["key"] for p in body["pushed"]] == ["DEV-1", "DEV-2"]
    assert body["skipped"] == [{"task_id": ids[2], "code": "T-003", "reason": "Not assigned yet"}]
    assert body["error"] is None

    sent = json.loads(route.calls[0].request.content)
    fields = sent["fields"]
    assert fields["project"] == {"key": "DEV"}
    assert fields["issuetype"] == {"name": "Story"}
    assert fields["summary"] == f"REQ-001: {PAY}"
    assert fields["description"]["type"] == "doc"
    text = json.dumps(fields["description"])
    assert "Assigned in ReqGuard to" in text and "Rahim" in text and "Created by ReqGuard from task T-001." in text
    assert route.calls[0].request.headers["authorization"].startswith("Basic ")

    after = tasks_by_requirement(client, project)
    assert after["REQ-001"]["jira_issue_key"] == "DEV-1"
    assert after["REQ-001"]["jira_url"] == f"{BASE}/browse/DEV-1"

    again = _push(client, project, ids[:1]).json()
    assert again["pushed"] == []
    assert again["skipped"][0]["reason"] == "Already in Jira as DEV-1"
    assert route.call_count == 2


@respx.mock
def test_an_error_part_way_keeps_the_earlier_keys(client, project, tasks, jira_env):
    _set_target(client, project)
    responses = iter(
        [httpx.Response(201, json={"key": "DEV-7"}), httpx.Response(400, json={"errors": {"issuetype": "invalid"}})]
    )
    respx.post(ISSUE_URL).mock(side_effect=lambda request: next(responses))
    body = _push(client, project, [tasks["REQ-001"]["id"], tasks["REQ-002"]["id"]]).json()
    assert [p["key"] for p in body["pushed"]] == ["DEV-7"]
    assert "Jira refused the issue (HTTP 400): issuetype: invalid" in body["error"]
    assert body["skipped"][0]["reason"] == "Not sent because of the error"
    after = tasks_by_requirement(client, project)
    assert after["REQ-001"]["jira_issue_key"] == "DEV-7"
    assert after["REQ-002"]["jira_issue_key"] is None


@respx.mock
def test_rejected_credentials_give_a_clear_message(client, project, tasks, jira_env):
    _set_target(client, project)
    respx.post(ISSUE_URL).respond(401)
    body = _push(client, project, [tasks["REQ-001"]["id"]]).json()
    assert body["pushed"] == []
    assert "Jira rejected the credentials (HTTP 401)" in body["error"]
    assert "secret-token" not in json.dumps(body)


def test_no_jira_project_for_tasks(client, project, tasks, jira_env):
    res = _push(client, project, [tasks["REQ-001"]["id"]])
    assert res.status_code == 409
    assert res.json()["detail"] == "Set the Jira project for tasks on the project page first."


def test_jira_not_configured_in_env(client, project, tasks, monkeypatch):
    for name in ("jira_base_url", "jira_email", "jira_api_token"):
        monkeypatch.setattr(settings, name, None)
    _set_target(client, project)
    res = _push(client, project, [tasks["REQ-001"]["id"]])
    assert res.status_code == 409
    assert res.json()["detail"].startswith("Not configured: set JIRA_BASE_URL")
    jira = call(client, project, "GET", "/tasks").json()["jira"]
    assert jira == {"configured": False, "project_key": "DEV", "issue_type": "Story",
                    "missing_env": ["JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"]}  # fmt: skip
