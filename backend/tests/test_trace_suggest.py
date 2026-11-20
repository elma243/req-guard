"""Suggested trace links: embedding similarity, the LLM check, and what is never suggested again (Step 10).

Embeddings come from embed_by_topic (a text gets the vector of the first topic word it contains), so similarity is
1.0 within a topic and about 0 across topics. GitHub is faked with respx; the LLM is FakeLLM.
"""

import pytest
import respx

from app.ai.llm import LLMError
from app.core.config import settings
from app.models import TraceLink, TraceTargetEmbedding
from tests.case_helpers import approve, call
from tests.clarify_helpers import make_project, use_llm
from tests.trace_helpers import (
    RESET,
    SALES,
    FakeGitHub,
    analyze,
    connect,
    decide,
    embed_by_topic,
    links,
    requirements,
    rows,
)

SALES_TARGETS = {"file:app/reports.py", "pr:1"}  # plus the "Export sales report as CSV" commit


@pytest.fixture
def github():
    with respx.mock(assert_all_called=False) as router:
        yield FakeGitHub(router)


@pytest.fixture
def project(client, make_user):
    return make_project(client, make_user)


@pytest.fixture
def ready(client, project, github, db_session_factory, fake_models):
    """Three approved requirements and a connected repo, not analysed yet."""
    embed_by_topic(fake_models)
    ids = requirements(client, project, db_session_factory)
    connect(client, project)
    return ids


def ai_links(row: dict) -> dict[str, dict]:
    return {k: v for k, v in links(row).items() if v["origin"] == "ai"}


def recover(client, project) -> dict:
    res = call(client, project, "POST", "/trace/recover")
    assert res.status_code == 200, res.text
    return res.json()


def test_similar_targets_are_checked_by_the_llm_and_stored_with_reason(client, project, ready, github):
    llm = use_llm()
    llm.link_verdicts[(SALES, "app/reports.py")] = {
        "verdict": "link",
        "confidence": 0.9,
        "reason": "The file exports the sales report.",
    }
    analyze(client, project)
    found = ai_links(rows(client, project)["REQ-003"])
    assert set(found) == SALES_TARGETS | {f"commit:{github.c2}"}
    reports = found["file:app/reports.py"]
    assert reports["status"] == "suggested" and not reports["live"]
    assert reports["reason"] == "The file exports the sales report."
    assert reports["confidence"] == 0.9 and reports["similarity"] == 1.0
    assert reports["ai_model"] == "fake-model" and reports["llm_checked"]
    # The LLM saw the requirement and the item's text
    sent = next(p for call_ in llm.verify_link_calls for p in call_ if p["item"]["name"] == "app/reports.py")
    assert sent["requirement"]["statement"] == SALES
    assert sent["item"]["type"] == "code file"
    assert "Monthly sales report export" in sent["item"]["text"]


def test_top_three_per_target_type(client, project, ready, github):
    use_llm()
    # Four sales commits: only the 3 most similar are candidates
    for n in range(4):
        github.push(f"Sales report fix {n}", ["app/reports.py"])
    analyze(client, project)
    commits = [k for k in ai_links(rows(client, project)["REQ-003"]) if k.startswith("commit:")]
    assert len(commits) == 3


def test_below_threshold_is_not_suggested(client, project, ready, monkeypatch):
    use_llm()
    monkeypatch.setattr(settings, "trace_similarity", 1.01)  # nothing is that similar
    result = analyze(client, project)
    assert result["suggested_added"] == 0 and result["explicit_added"] == 8
    assert ai_links(rows(client, project)["REQ-003"]) == {}


@pytest.mark.parametrize("verdict", ["no_link", "not_enough_information"])
def test_llm_no_is_dismissed_and_hidden(client, project, ready, db_session_factory, verdict):
    llm = use_llm()
    llm.link_verdicts[(SALES, "app/reports.py")] = {"verdict": verdict, "confidence": 0.7, "reason": "Other topic."}
    result = analyze(client, project)
    assert result["dismissed"] == 1
    assert "file:app/reports.py" not in links(rows(client, project)["REQ-003"])
    with db_session_factory() as db:
        dismissed = db.query(TraceLink).filter_by(target_key="app/reports.py", status="dismissed").one()
        assert dismissed.reason == "Other topic."
    # Not asked again for the same requirement version
    llm.verify_link_calls.clear()
    recover(client, project)
    assert ("REQ-003", "app/reports.py") not in llm.verified_pairs


def test_dismissed_is_asked_again_after_a_new_requirement_version(client, project, ready):
    llm = use_llm()
    llm.link_verdicts[(SALES, "app/reports.py")] = {"verdict": "no_link", "confidence": 0.6, "reason": "No."}
    analyze(client, project)
    rid = ready[2]
    new = "The admin shall export the monthly sales report as a CSV file."
    assert call(client, project, "PATCH", f"/requirements/{rid}", {"statement": new}).status_code == 200
    approve(client, project, rid)
    llm.verify_link_calls.clear()
    recover(client, project)
    assert ("REQ-003", "app/reports.py") in llm.verified_pairs
    link = links(rows(client, project)["REQ-003"])["file:app/reports.py"]
    assert link["status"] == "suggested"


def test_no_llm_keeps_suggestions_unchecked_then_checks_them(client, project, ready):
    result = analyze(client, project)  # tests have no LLM by default
    assert result["unchecked"] == 4
    assert "Suggestions were not checked by AI: no AI model is configured." in result["notices"]
    reports = links(rows(client, project)["REQ-003"])["file:app/reports.py"]
    assert reports["status"] == "suggested" and not reports["llm_checked"]
    assert reports["reason"] == "Similarity 1.00; not checked by AI (no AI model is configured)."
    assert reports["confidence"] is None and reports["ai_model"] is None

    llm = use_llm()
    llm.link_verdicts[(SALES, "#1 Sales report export")] = {"verdict": "no_link", "confidence": 0.8, "reason": "x"}
    again = recover(client, project)
    assert again["unchecked"] == 0 and again["dismissed"] == 1
    assert ("REQ-003", "app/reports.py") in llm.verified_pairs
    checked = links(rows(client, project)["REQ-003"])["file:app/reports.py"]
    assert checked["llm_checked"] and checked["reason"] == "Fake: the names match."
    assert "pr:1" not in links(rows(client, project)["REQ-003"])


def test_llm_error_keeps_suggestions_unchecked(client, project, ready):
    llm = use_llm()
    llm.verify_links_fail = LLMError("All AI models are busy or out of free quota. Try again later.")
    result = analyze(client, project)
    assert result["unchecked"] == 4 and result["suggested_added"] == 4
    assert any("All AI models are busy" in n for n in result["notices"])
    assert len(llm.verify_link_calls) == 1  # after one failure the other batches are not sent
    reports = links(rows(client, project)["REQ-003"])["file:app/reports.py"]
    assert "not checked by AI (All AI models are busy" in reports["reason"]


def test_people_decisions_and_explicit_links_are_never_changed(client, project, ready, db_session_factory):
    llm = use_llm()
    analyze(client, project)
    req3 = links(rows(client, project)["REQ-003"])
    decide(client, project, req3["file:app/reports.py"]["id"], "confirm")
    decide(client, project, req3["pr:1"]["id"], "reject")
    with db_session_factory() as db:
        before = {(l.target_key, l.status, l.origin, l.reason) for l in db.query(TraceLink).all()}  # noqa: E741
    llm.verify_link_calls.clear()
    llm.default_link_verdict = {"verdict": "no_link", "confidence": 0.9, "reason": "changed mind"}
    recover(client, project)
    assert llm.verify_link_calls == []
    with db_session_factory() as db:
        after = {(l.target_key, l.status, l.origin, l.reason) for l in db.query(TraceLink).all()}  # noqa: E741
    assert after == before


def test_explicit_id_upgrades_an_open_ai_suggestion(client, project, ready, github):
    use_llm()
    analyze(client, project)
    assert links(rows(client, project)["REQ-003"])["file:app/reports.py"]["origin"] == "ai"
    github.push(
        "Mark the report module",
        ["app/reports.py"],
        extra={"app/reports.py": '"""Monthly sales report export (REQ-003)."""\n\n\ndef export():\n    pass\n'},
    )
    analyze(client, project)
    link = links(rows(client, project)["REQ-003"])["file:app/reports.py"]
    assert (link["origin"], link["confidence"], link["live"]) == ("explicit", 1.0, True)
    assert link["evidence"] == "REQ-003 in a docstring (line 1)"


def test_unavailable_embedder_gives_explicit_links_only(client, project, ready, fake_models):
    llm = use_llm()
    fake_models.unavailable = "The embedding model could not be loaded: no network"
    result = analyze(client, project)
    assert result["explicit_added"] == 8 and result["suggested_added"] == 0
    assert "Only explicit links were found: The embedding model could not be loaded: no network" in result["notices"]
    assert llm.verify_link_calls == []


def test_target_embeddings_are_cached(client, project, ready, fake_models, db_session_factory):
    use_llm()
    analyze(client, project)
    embedded = sum(len(c) for c in fake_models.embedder.calls)
    with db_session_factory() as db:
        stored = db.query(TraceTargetEmbedding).count()
    assert stored > 0
    recover(client, project)
    assert sum(len(c) for c in fake_models.embedder.calls) == embedded  # nothing embedded again


def test_wrong_ai_suggestion_for_another_requirement_can_be_dismissed(client, project, ready):
    """REQ-002 (password reset) is similar to a login test whose name has "password"; the LLM says no."""
    llm = use_llm()
    name = "tests/test_auth.py::test_req_001_login_with_valid_password"
    llm.link_verdicts[(RESET, name)] = {"verdict": "no_link", "confidence": 0.85, "reason": "It tests login."}
    analyze(client, project)
    assert f"test_function:{name}" not in links(rows(client, project)["REQ-002"])
