"""Coverage levels and test case matching (Step 10): pure functions, then Verified through the API."""

from dataclasses import dataclass

import pytest
import respx

from tests.case_helpers import call
from tests.clarify_helpers import make_project
from app.trace.coverage import CaseRef, CriterionRef, coverage, matches_case, name_words
from tests.trace_helpers import FakeGitHub, analyze, connect, decide, links, requirements, rows, seed_criteria


@dataclass
class Link:
    id: int
    link_type: str
    target_type: str
    target_key: str
    target_detail: str | None = None
    origin: str = "explicit"
    status: str = "suggested"

    @property
    def live(self) -> bool:
        return self.status == "accepted" or (self.origin == "explicit" and self.status == "suggested")


CODE = Link(1, "implements", "file", "app/auth.py")
TEST = Link(2, "verifies", "test_function", "tests/test_auth.py::test_login_with_valid_password")
C1 = CriterionRef(10, "AC-1", "A valid login works")
C2 = CriterionRef(11, "AC-2", "A wrong password is refused")
CASE1 = CaseRef(20, 1, "TC-1", "Login with valid password", 10)
CASE2 = CaseRef(21, 2, "TC-2", "Wrong password is refused", 11)


def level(links, criteria=(), cases=()):
    return coverage(links, list(criteria), list(cases)).level


def test_levels():
    assert level([]) == "not_started"
    assert level([CODE]) == "code_only"
    assert level([TEST]) == "tests_only"
    assert level([CODE, TEST]) == "code_and_tests"
    assert level([CODE, TEST], [C1], [CASE1]) == "verified"
    assert level([CODE, TEST], [C1, C2], [CASE1, CASE2]) == "code_and_tests"


def test_reasons_use_cautious_wording():
    assert "possible test gap" in coverage([CODE], [], []).reason
    assert "possible traceability gap" in coverage([TEST], [], []).reason
    partial = coverage([CODE, TEST], [C1, C2], [CASE1, CASE2])
    assert partial.reason == "1 of 2 acceptance criteria have a linked test: potential test gap."
    assert [(r.criterion.code, r.covered, r.test_cases) for r in partial.criteria] == [
        ("AC-1", True, ["TC-1"]),
        ("AC-2", False, []),
    ]
    assert "safe" not in coverage([CODE, TEST], [C1], [CASE1]).reason.lower()


def test_no_accepted_criteria_is_never_verified():
    result = coverage([CODE, TEST], [], [CASE1])
    assert result.level == "code_and_tests"
    assert "no accepted acceptance criteria" in result.reason


def test_only_live_links_count():
    pending_ai = Link(3, "implements", "file", "app/x.py", origin="ai", status="suggested")
    rejected_explicit = Link(4, "verifies", "test_function", TEST.target_key, status="rejected")
    confirmed_ai = Link(5, "verifies", "test_function", TEST.target_key, origin="ai", status="accepted")
    assert level([pending_ai]) == "not_started"
    assert level([CODE, rejected_explicit], [C1], [CASE1]) == "code_only"
    assert level([CODE, confirmed_ai], [C1], [CASE1]) == "verified"
    # A pending AI test link does not cover a criterion either
    pending_test = Link(6, "verifies", "test_function", TEST.target_key, origin="ai", status="suggested")
    assert level([CODE, pending_test], [C1], [CASE1]) == "code_only"


def test_matches_are_reported_for_every_shown_link():
    pending_test = Link(6, "verifies", "test_function", TEST.target_key, origin="ai", status="suggested")
    assert coverage([pending_test], [C1], [CASE1]).matches == {6: ["TC-1"]}


@pytest.mark.parametrize(
    ("key", "detail", "matched"),
    [
        ("t.py::test_login_with_valid_password", None, True),  # same words
        ("t.py::test_req_004_login_with_valid_password", None, True),  # REQ ID ignored
        ("t.py::TestLogin.test_login_with_valid_password", None, True),  # method
        ("t.py::test_valid_password_login_with", None, True),  # same word set
        ("t.py::test_something_else", "TC-1: checks it", True),  # TC-1 in the docstring
        ("t.py::test_tc_1_login", None, True),  # TC-1 in the name
        ("t.py::test_login", None, False),  # too few shared words
        ("t.py::test_something_else", "TC-2: other", False),
    ],
)
def test_test_case_matching(key, detail, matched):
    assert matches_case(key, detail, CASE1) is matched


def test_name_words():
    assert name_words("a.py::TestX.test_req_001_TC_2_login_works") == ["login", "works"]


def test_test_files_never_match_cases():
    file_link = Link(7, "verifies", "test_file", "tests/test_login_with_valid_password.py")
    assert coverage([CODE, file_link], [C1], [CASE1]).level == "code_and_tests"


# ---------- through the API ----------


@pytest.fixture
def github():
    with respx.mock(assert_all_called=False) as router:
        yield FakeGitHub(router)


def test_verified_through_matched_step_8b_test_cases(client, make_user, github, db_session_factory, fake_models):
    fake_models.unavailable = "off"
    project = make_project(client, make_user)
    ids = requirements(client, project, db_session_factory)
    seed_criteria(
        db_session_factory,
        ids[0],
        [
            ("A customer can log in with a valid password.", ["Login with valid password"]),
            ("A valid request works.", ["A valid request is accepted"]),
        ],
    )
    seed_criteria(db_session_factory, ids[0], [("Rejected by the lead.", [])], status="rejected")
    connect(client, project)
    analyze(client, project)

    row = rows(client, project)["REQ-001"]
    assert row["coverage"] == "code_and_tests"
    assert [(c["code"], c["covered"], c["test_cases"]) for c in row["criteria"]] == [
        ("AC-1", True, ["TC-1"]),
        ("AC-2", False, []),
    ]
    named = links(row)["test_function:tests/test_auth.py::test_req_001_login_with_valid_password"]
    assert named["test_cases"] == ["TC-1"]

    # The second criterion's case is TC-2; the test whose docstring says "TC-1" does not cover it...
    body = {
        "requirement_id": ids[0],
        "target_type": "test_function",
        "target_key": "tests/test_auth.py::test_valid_request_works",
    }
    added = call(client, project, "POST", "/trace/links", body).json()
    assert added["test_cases"] == ["TC-1"]
    assert rows(client, project)["REQ-001"]["coverage"] == "code_and_tests"

    # ...but a test named like TC-2's title does
    github.push(
        "Add a test",
        ["tests/test_more.py"],
        extra={"tests/test_more.py": "def test_req_001_a_valid_request_is_accepted():\n    assert True\n"},
    )
    analyze(client, project)
    row = rows(client, project)["REQ-001"]
    assert row["coverage"] == "verified"
    assert row["coverage_reason"] == "Every acceptance criterion (2) has a linked test."
    assert call(client, project, "GET", "/trace").json()["summary"]["counts"]["verified"] == 1

    # Rejecting the matching test takes Verified away again
    link = links(row)["test_function:tests/test_more.py::test_req_001_a_valid_request_is_accepted"]
    decide(client, project, link["id"], "reject")
    assert rows(client, project)["REQ-001"]["coverage"] == "code_and_tests"
