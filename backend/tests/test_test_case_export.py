"""Export (FR-10): approved requirements with their reviewed criteria and test cases, as CSV and PDF, with the
project name and date. Unreviewed suggestions, rejected items and requirements that are not approved are left out."""

import csv
import io
from datetime import UTC, datetime

import pytest

from app.reports.test_case_export import (
    ExportCriterion,
    ExportRequirement,
    ExportTestCase,
    build_csv,
    build_pdf,
    export_filename,
)
from tests.case_helpers import call, case_body, generate, group, seed_approved
from tests.clarify_helpers import CLEAN, make_project, seed_requirement, use_llm
from tests.fake_llm import FakeLLM

OTHER = "The admin shall print the invoice of an order."
UNAPPROVED = "The customer shall see the delivery date of an order."


@pytest.fixture
def project(client, make_user):
    return make_project(client, make_user, "Acme দোকান")


@pytest.fixture
def reviewed(client, project, db_session_factory):
    """REQ-001 approved with TC-1 accepted, TC-2 edited, TC-3 untouched; REQ-002 approved, nothing reviewed;
    REQ-003 not approved."""
    use_llm(FakeLLM())
    rid = seed_approved(client, project, db_session_factory)
    generate(client, project, rid)
    g = group(client, project, rid)
    tc1, tc2, _ = (t["id"] for t in g["test_cases"])
    call(client, project, "POST", f"/test-cases/{tc1}/accept")
    body = case_body("=HYPERLINK(\"http://x\")", g["criteria"][0]["id"], "negative")
    call(client, project, "PATCH", f"/test-cases/{tc2}", body)
    seed_approved(client, project, db_session_factory, OTHER)
    seed_requirement(db_session_factory, project[2], UNAPPROVED)
    return rid


def _csv(res) -> list[list[str]]:
    assert res.content.startswith("﻿".encode())
    return list(csv.reader(io.StringIO(res.content.decode("utf-8-sig"))))


def test_csv_lists_approved_requirements_with_reviewed_items(client, project, reviewed):
    res = call(client, project, "GET", "/test-cases/export.csv")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    today = f"{datetime.now(UTC):%Y-%m-%d}"
    assert res.headers["content-disposition"] == f'attachment; filename="acme-test-cases-{today}.csv"'

    rows = _csv(res)
    assert rows[0] == ["Project", "Acme দোকান"]  # the Bangla name survives
    assert rows[1][0] == "Exported" and rows[1][1].startswith(today)
    assert rows[2] == [] and rows[3][0] == "Requirement"
    data = rows[4:]
    assert {r[0] for r in data} == {"REQ-001", "REQ-002"}  # REQ-003 is not approved
    first = [r for r in data if r[0] == "REQ-001"]
    # AC-1 (accepted with TC-1) has TC-1 and the edited TC-2; AC-2 and TC-3 are untouched suggestions
    assert [(r[3], r[5], r[11]) for r in first] == [("AC-1", "TC-1", "accepted"), ("AC-1", "TC-2", "edited")]
    assert first[0][1] == CLEAN and first[0][2] == "1"
    assert first[1][6] == "'=HYPERLINK(\"http://x\")"  # not run as a formula
    second = [r for r in data if r[0] == "REQ-002"]
    assert len(second) == 1 and second[0][6] == "No accepted test cases yet"


def test_csv_marks_items_that_need_review(client, project, reviewed):
    from tests.case_helpers import approve

    call(client, project, "PATCH", f"/requirements/{reviewed}", {"statement": CLEAN.replace("CSV", "a CSV file")})
    assert {r[0] for r in _csv(call(client, project, "GET", "/test-cases/export.csv"))[4:]} == {"REQ-002"}
    approve(client, project, reviewed)
    data = [r for r in _csv(call(client, project, "GET", "/test-cases/export.csv"))[4:] if r[0] == "REQ-001"]
    assert data and all(r[12] == "yes" for r in data)
    assert data[0][2] == "2"


def test_pdf_export(client, project, reviewed):
    res = call(client, project, "GET", "/test-cases/export.pdf")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.headers["content-disposition"].endswith('.pdf"')
    assert res.content.startswith(b"%PDF")


def test_export_of_an_empty_project(client, make_user):
    project = make_project(client, make_user, "নতুন")
    rows = _csv(call(client, project, "GET", "/test-cases/export.csv"))
    assert rows[0] == ["Project", "নতুন"] and rows[4:] == []
    res = call(client, project, "GET", "/test-cases/export.pdf")
    assert res.status_code == 200 and res.content.startswith(b"%PDF")
    assert "project-test-cases-" in res.headers["content-disposition"]  # no Latin letters in the name


def _sample() -> list[ExportRequirement]:
    return [
        ExportRequirement(
            code="REQ-001",
            statement="The admin shall export the order list as CSV & PDF <quickly>.",
            version=2,
            criteria=[ExportCriterion(id=1, code="AC-1", text="The file has every order.", needs_review=True)],
            test_cases=[
                ExportTestCase(
                    code="TC-1",
                    title="Export works",
                    type="positive",
                    given="an admin with 3 orders",
                    when="they export — অর্ডার",
                    then="the file has 3 rows",
                    criterion_id=1,
                    criterion_code="AC-1",
                    status="accepted",
                    needs_review=False,
                ),
                ExportTestCase(
                    code="TC-2",
                    title="No criterion",
                    type="edge",
                    given="g",
                    when="w",
                    then="t",
                    criterion_id=None,
                    criterion_code=None,
                    status="edited",
                    needs_review=False,
                ),
            ],
        )
    ]


def test_builders_directly():
    at = datetime(2026, 11, 15, 9, 30, tzinfo=UTC)
    rows = list(csv.reader(io.StringIO(build_csv("Shop", at, _sample()).decode("utf-8-sig"))))
    assert rows[1] == ["Exported", "2026-11-15 09:30 UTC"]
    assert [(r[3], r[5], r[12]) for r in rows[4:]] == [("AC-1", "TC-1", "yes"), ("", "TC-2", "")]
    # Characters the PDF fonts lack, and XML specials, do not break the build
    assert build_pdf("দোকান & <Shop>", at, _sample()).startswith(b"%PDF")
    assert export_filename("Acme Shop / Phase 2", at, "pdf") == "acme-shop-phase-2-test-cases-2026-11-15.pdf"
