"""Repository connection, analysis, explicit trace links, decisions, manual links, filters, the requirement panel
and the CSV export (Step 10). GitHub is faked with respx; the LLM is the fake one; no network."""

import pytest
import respx

from app.core.config import settings
from tests.case_helpers import call, seed_approved
from tests.clarify_helpers import make_project, seed_requirement, use_llm
from tests.trace_helpers import (
    API,
    RESET,
    SALES,
    FakeGitHub,
    analyze,
    connect,
    decide,
    embed_by_topic,
    links,
    matrix,
    requirements,
    rows,
)

TOKEN = "ghp_fake_token_for_tests_123456"


@pytest.fixture
def github():
    with respx.mock(assert_all_called=False) as router:
        yield FakeGitHub(router)


@pytest.fixture
def project(client, make_user):
    return make_project(client, make_user)


@pytest.fixture
def setup(client, project, github, db_session_factory, fake_models):
    """Three approved requirements, the repo connected and analysed with the fake LLM."""
    embed_by_topic(fake_models)
    llm = use_llm()
    ids = requirements(client, project, db_session_factory)
    connect(client, project)
    result = analyze(client, project)
    return {"ids": ids, "llm": llm, "result": result}


# ---------- connection ----------


@pytest.mark.parametrize("name", ["acme/shop", "https://github.com/acme/shop.git"])
def test_connect(client, project, github, name):
    state = connect(client, project, name)
    repo = state["repo"]
    assert repo["full_name"] == "acme/shop"
    assert repo["default_branch"] == "main"
    assert repo["url"] == "https://github.com/acme/shop"
    assert repo["last_analyzed_at"] is None
    assert state["token_configured"] is False
    assert call(client, project, "GET", "/repo").json()["repo"]["full_name"] == "acme/shop"


def test_no_repo_yet(client, project):
    assert call(client, project, "GET", "/repo").json() == {"repo": None, "token_configured": False}
    assert call(client, project, "POST", "/repo/analyze").status_code == 409
    assert call(client, project, "POST", "/trace/recover").status_code == 409
    # The matrix still works and lists approved requirements as not started
    assert matrix(client, project)["repo"] is None


def test_bad_name_is_422(client, project):
    res = call(client, project, "PUT", "/repo", {"repository": "not a repo"})
    assert res.status_code == 422
    assert "owner/repo" in res.text


def test_repo_github_cannot_find(client, project):
    with respx.mock:
        respx.get(API).respond(404, json={"message": "Not Found"})
        res = call(client, project, "PUT", "/repo", {"repository": "acme/shop"})
    assert res.status_code == 502
    assert "could not find acme/shop" in res.json()["detail"]


def test_token_is_never_returned(client, project, github, monkeypatch, db_session_factory, fake_models):
    monkeypatch.setattr(settings, "github_token", TOKEN)
    embed_by_topic(fake_models)
    requirements(client, project, db_session_factory)
    state = connect(client, project)
    assert state["token_configured"] is True and state["repo"]["token_configured"] is True
    texts = [
        call(client, project, "GET", "/repo").text,
        call(client, project, "POST", "/repo/analyze").text,
        call(client, project, "GET", "/repo/snapshot").text,
        call(client, project, "GET", "/trace").text,
        call(client, project, "GET", "/trace/export.csv").text,
    ]
    assert all(TOKEN not in text for text in texts)
    assert github.repo_route.calls.last.request.headers["Authorization"] == f"Bearer {TOKEN}"


def test_other_owners_project_is_404(client, make_user, project, github):
    connect(client, project)
    other = make_project(client, make_user)
    _, base, _ = project
    headers, _, _ = other
    for method, path in (("GET", "/repo"), ("POST", "/repo/analyze"), ("GET", "/trace"), ("DELETE", "/repo")):
        assert client.request(method, f"{base}{path}", headers=headers).status_code == 404


# ---------- analysis ----------


def test_analysis_counts_and_snapshot(client, project, setup, github):
    result = setup["result"]
    assert result["head_sha"] == github.c3 and result["head_changed"]
    assert result["new_commits"] == 3 and result["commit_files_pending"] == 0
    assert result["pulls_updated"] == 2
    assert (result["files"], result["python_files"], result["test_functions"]) == (7, 6, 3)
    assert result["explicit_added"] == 8
    assert result["suggested_added"] == 4
    assert result["rate_limit_remaining"] is not None

    repo = call(client, project, "GET", "/repo").json()["repo"]
    assert repo["head_sha"] == github.c3 and repo["last_analyzed_at"]
    assert repo["last_result"]["explicit_added"] == 8

    snapshot = call(client, project, "GET", "/repo/snapshot").json()
    assert snapshot["commit_sha"] == github.c3
    assert [c["title"] for c in snapshot["commits"]] == [
        "Add password reset email (REQ-002)",
        "Export sales report as CSV",
        "Initial commit",
    ]
    assert snapshot["commits"][0]["files"] == ["app/auth.py", "tests/test_auth.py"]
    assert snapshot["commits"][0]["url"] == f"https://github.com/acme/shop/commit/{github.c3}"
    assert [(p["number"], p["merged"]) for p in snapshot["pulls"]] == [(2, True), (1, False)]
    tests = {f["path"]: f["tests"] for f in snapshot["python"]}
    assert tests["tests/test_auth.py"] == 3
    assert next(f for f in snapshot["python"] if f["path"] == "app/broken.py")["parse_error"]


def test_explicit_links_from_every_source(client, project, setup, github):
    found = rows(client, project)
    req1, req2, req3 = (links(found[code]) for code in ("REQ-001", "REQ-002", "REQ-003"))

    explicit1 = {k for k, v in req1.items() if v["origin"] == "explicit"}
    assert explicit1 == {
        f"commit:{github.c1}",  # branch feature/REQ-001-sign-in has no pull request
        "file:app/auth.py",
        "test_function:tests/test_auth.py::test_req_001_login_with_valid_password",
    }
    explicit2 = {k for k, v in req2.items() if v["origin"] == "explicit"}
    assert explicit2 == {
        f"commit:{github.c3}",
        "pr:2",
        "file:app/auth.py",
        "test_function:tests/test_auth.py::TestReset.test_reset_link_expires",
    }
    assert {k for k, v in req3.items() if v["origin"] == "explicit"} == {"file:app/broken.py"}

    commit = req2[f"commit:{github.c3}"]
    assert commit["link_type"] == "implements"
    assert commit["confidence"] == 1.0 and commit["status"] == "suggested" and commit["live"]
    assert commit["evidence"] == "REQ-002 in the commit message"
    assert commit["url"] == f"https://github.com/acme/shop/commit/{github.c3}"
    assert req2["pr:2"]["url"] == "https://github.com/acme/shop/pull/2"
    assert req2["pr:2"]["evidence"] == "REQ-002 in the branch name feature/REQ-002-reset"
    auth = req1["file:app/auth.py"]
    assert auth["evidence"] == "REQ-001 in a comment (line 5)"
    assert auth["url"] == f"https://github.com/acme/shop/blob/{github.c3}/app/auth.py#L5"
    test = req2["test_function:tests/test_auth.py::TestReset.test_reset_link_expires"]
    assert test["link_type"] == "verifies"
    assert test["url"].endswith("/tests/test_auth.py#L16")


def test_unapproved_and_unknown_ids_are_ignored(client, project, github, db_session_factory, fake_models):
    fake_models.unavailable = "off for this test"
    seed_approved(client, project, db_session_factory, RESET)  # REQ-001
    seed_requirement(db_session_factory, project[2], SALES)  # REQ-002, not approved
    connect(client, project)
    result = analyze(client, project)
    found = rows(client, project)
    assert set(found) == {"REQ-001"}  # only approved requirements are in the matrix
    # REQ-001 is named in the auth.py comment, the test name and a branch; REQ-002 is not approved; REQ-003 unknown
    assert set(links(found["REQ-001"])) == {
        f"commit:{github.c1}",
        "file:app/auth.py",
        "test_function:tests/test_auth.py::test_req_001_login_with_valid_password",
    }
    assert result["explicit_added"] == 3
    assert "Only explicit links were found: off for this test" in result["notices"]


def test_second_analysis_uses_the_cache(client, project, setup, github):
    assert github.tarball_route.call_count == 1
    files_calls = github.commit_route.call_count
    assert files_calls == 3

    again = analyze(client, project)
    assert github.tarball_route.call_count == 1  # same head: nothing downloaded
    assert github.commit_route.call_count == files_calls  # no file list read again
    assert again["new_commits"] == 0 and again["pulls_updated"] == 0 and not again["head_changed"]
    assert again["explicit_added"] == 0 and again["suggested_added"] == 0
    assert "Nothing new on GitHub since the last analysis." in again["notices"]
    pulls_query = str(github.pulls_route.calls.last.request.url)
    assert "sort=updated" in pulls_query

    new = github.push(
        "Show the sales report total (REQ-003)",
        ["app/reports.py"],
        extra={"tests/test_reports.py": "def test_req_003_sales_total():\n    assert True\n"},
    )
    third = analyze(client, project)
    assert third["new_commits"] == 1 and third["head_changed"]
    assert github.commit_route.call_count == files_calls + 1  # only the new commit's files
    assert github.tarball_route.call_count == 2
    assert third["test_functions"] == 4
    req3 = links(rows(client, project)["REQ-003"])
    assert req3[f"commit:{new}"]["origin"] == "explicit"
    assert req3["test_function:tests/test_reports.py::test_req_003_sales_total"]["live"]


def test_commit_files_wait_when_few_requests_are_left(client, project, github, db_session_factory, fake_models):
    fake_models.unavailable = "off"
    github.remaining = 53
    connect(client, project)  # 52 left
    result = analyze(client, project)
    assert result["commit_files_pending"] > 0
    assert any("not read yet" in n for n in result["notices"])
    github.remaining = 4000
    later = analyze(client, project)
    assert later["commit_files_pending"] == 0
    snapshot = call(client, project, "GET", "/repo/snapshot").json()
    assert all(c["files_fetched"] for c in snapshot["commits"])


def test_rate_limit_stops_the_analysis_with_a_clear_message(client, project, github):
    connect(client, project)
    github.rate_limited = True
    res = call(client, project, "POST", "/repo/analyze")
    assert res.status_code == 502
    assert "rate limit" in res.json()["detail"]
    assert call(client, project, "GET", "/repo").json()["repo"]["last_analyzed_at"] is None


def test_connecting_another_repo_drops_the_old_links(client, project, setup, github):
    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.github.com/repos/acme/other").respond(
            json={"full_name": "acme/other", "default_branch": "dev", "private": True}
        )
        state = connect(client, project, "acme/other")
    assert state["repo"]["default_branch"] == "dev" and state["repo"]["private"]
    assert state["repo"]["head_sha"] is None
    found = matrix(client, project)
    assert all(not r["implements"] and not r["verifies"] for r in found["rows"])


def test_disconnect_removes_links(client, project, setup):
    assert call(client, project, "DELETE", "/repo").status_code == 204
    assert call(client, project, "GET", "/repo").json()["repo"] is None
    assert all(r["coverage"] == "not_started" for r in matrix(client, project)["rows"])


# ---------- decisions and manual links ----------


def test_confirm_reject_and_undo(client, project, setup):
    req3 = links(rows(client, project)["REQ-003"])
    suggestion = req3["file:app/reports.py"]
    assert suggestion["origin"] == "ai" and suggestion["status"] == "suggested" and not suggestion["live"]

    confirmed = decide(client, project, suggestion["id"], "confirm")
    assert confirmed["status"] == "accepted" and confirmed["live"] and confirmed["decided_by"]

    rejected = decide(client, project, suggestion["id"], "reject")
    assert rejected["status"] == "rejected" and not rejected["live"]

    explicit = req3["file:app/broken.py"]
    assert decide(client, project, explicit["id"], "reject")["live"] is False
    assert rows(client, project)["REQ-003"]["coverage"] == "not_started"
    assert decide(client, project, explicit["id"], "confirm")["live"] is True


def test_rejected_links_are_not_suggested_again(client, project, setup):
    llm = setup["llm"]
    req3 = links(rows(client, project)["REQ-003"])
    decide(client, project, req3["file:app/reports.py"]["id"], "reject")
    decide(client, project, req3["file:app/broken.py"]["id"], "reject")  # explicit, rejected
    llm.verify_link_calls.clear()

    res = call(client, project, "POST", "/trace/recover")
    assert res.status_code == 200, res.text
    assert res.json()["suggested_added"] == 0 and res.json()["explicit_added"] == 0
    assert llm.verify_link_calls == []
    again = links(rows(client, project)["REQ-003"])
    assert again["file:app/reports.py"]["status"] == "rejected"
    assert again["file:app/broken.py"]["status"] == "rejected"


def test_add_a_link_by_hand(client, project, setup, github):
    rid = setup["ids"][0]
    body = {"requirement_id": rid, "target_type": "file", "target_key": "app/reports.py"}
    res = call(client, project, "POST", "/trace/links", body)
    assert res.status_code == 201, res.text
    link = res.json()
    assert (link["origin"], link["status"], link["live"], link["link_type"]) == ("user", "accepted", True, "implements")
    assert call(client, project, "POST", "/trace/links", body).status_code == 409

    # An AI suggestion becomes the person's own link
    req3 = links(rows(client, project)["REQ-003"])
    body3 = {"requirement_id": setup["ids"][2], "target_type": "pr", "target_key": "1"}
    assert req3["pr:1"]["origin"] == "ai"
    assert call(client, project, "POST", "/trace/links", body3).json()["origin"] == "user"

    unknown = {"requirement_id": rid, "target_type": "file", "target_key": "app/missing.py"}
    assert call(client, project, "POST", "/trace/links", unknown).status_code == 422
    wrong_kind = {"requirement_id": rid, "target_type": "test_file", "target_key": "app/reports.py"}
    assert call(client, project, "POST", "/trace/links", wrong_kind).status_code == 422


def test_manual_link_needs_an_approved_requirement(client, project, setup, db_session_factory):
    draft = seed_requirement(db_session_factory, project[2], "The admin shall see the stock level.")
    body = {"requirement_id": draft, "target_type": "file", "target_key": "app/reports.py"}
    assert call(client, project, "POST", "/trace/links", body).status_code == 409
    body["requirement_id"] = 999_999
    assert call(client, project, "POST", "/trace/links", body).status_code == 404


def test_target_search(client, project, setup, github):
    res = call(client, project, "GET", "/trace/targets?q=reset")
    keys = {(t["target_type"], t["key"]) for t in res.json()}
    assert ("test_function", "tests/test_auth.py::TestReset.test_reset_link_expires") in keys
    assert ("pr", "2") in keys
    only = call(client, project, "GET", "/trace/targets?target_type=commit").json()
    assert {t["key"] for t in only} == {github.c1, github.c2, github.c3}
    assert all(t["link_type"] == "implements" for t in only)


def test_links_to_removed_files_are_marked(client, project, setup, github):
    github.push("Remove the reports module", ["app/reports.py"])
    github.tarball = __import__("tests.repo_fixture", fromlist=["sample_tarball"]).sample_tarball(drop=("app/reports.py",))
    analyze(client, project)
    req3 = links(rows(client, project)["REQ-003"])
    assert req3["file:app/reports.py"]["present"] is False
    assert req3["file:app/broken.py"]["present"] is True


# ---------- matrix, filters, panel ----------


def test_coverage_and_summary(client, project, setup):
    found = matrix(client, project)
    by_code = {r["requirement"]["code"]: r for r in found["rows"]}
    assert by_code["REQ-001"]["coverage"] == "code_and_tests"
    assert by_code["REQ-003"]["coverage"] == "code_only"
    assert "possible test gap" in by_code["REQ-003"]["coverage_reason"]
    assert by_code["REQ-003"]["pending"] == 3
    summary = found["summary"]
    assert summary["total"] == 3
    assert summary["counts"] == {"not_started": 0, "code_only": 1, "tests_only": 0, "code_and_tests": 2, "verified": 0}
    assert summary["pending"] == 4


def test_filters(client, project, setup, db_session_factory):
    codes = lambda q: [r["requirement"]["code"] for r in matrix(client, project, q)["rows"]]  # noqa: E731
    assert codes("?coverage=code_only") == ["REQ-003"]
    assert codes("?coverage=verified") == []
    assert codes("?link_type=verifies") == ["REQ-001", "REQ-002"]
    assert codes("?pending=true") == ["REQ-002", "REQ-003"]
    assert codes("?pending=false") == ["REQ-001"]
    from app.models import Requirement

    with db_session_factory() as db:
        db.get(Requirement, setup["ids"][2]).module_name = "Reports"
        db.commit()
    assert codes("?module=Reports") == ["REQ-003"]
    assert matrix(client, project)["modules"] == ["Reports"]
    # The summary always counts every approved requirement
    assert matrix(client, project, "?coverage=code_only")["summary"]["total"] == 3


def test_requirement_panel(client, project, setup, db_session_factory):
    rid = setup["ids"][1]
    res = call(client, project, "GET", f"/trace/requirements/{rid}")
    assert res.status_code == 200, res.text
    row = res.json()
    assert row["requirement"]["code"] == "REQ-002"
    assert row["coverage"] == "code_and_tests"
    assert len(row["implements"]) == 3 and len(row["verifies"]) == 2
    draft = seed_requirement(db_session_factory, project[2], "The admin shall see the stock level.")
    assert call(client, project, "GET", f"/trace/requirements/{draft}").status_code == 409


# ---------- CSV ----------


def test_csv_export(client, project, setup, db_session_factory):
    rid = seed_approved(client, project, db_session_factory, "=HYPERLINK(1) গ্রাহক পাসওয়ার্ড বদলাবে।")
    res = call(client, project, "GET", "/trace/export.csv")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "traceability" in res.headers["content-disposition"]
    raw = res.content
    assert raw.startswith("﻿".encode())
    text = raw.decode("utf-8")
    assert "Repository,acme/shop" in text
    assert (
        "Requirement,Statement,Module,Coverage,Coverage reason,Code links,Test links,Matched test cases,"
        "Criteria with a linked test" in text
    )
    assert "'=HYPERLINK(1) গ্রাহক পাসওয়ার্ড বদলাবে।" in text
    req3 = next(line for line in text.splitlines() if line.startswith("REQ-003"))
    assert "Code only" in req3
    assert "app/broken.py [ID match, not confirmed, confidence 1.00]" in req3
    assert "app/reports.py [AI suggestion, not confirmed, confidence 0.80]" in req3
    assert rid
