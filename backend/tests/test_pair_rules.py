"""Which finding a pair of requirements gets from its similarity and contradiction scores. No database, no models."""

import pytest

from app.nlp.pair_checks import (
    CHANGE_CANDIDATE,
    CONFLICT_CANDIDATE,
    CONTRADICTION,
    DUPLICATE,
    PairItem,
    classify_pair,
    numbers_differ,
)

OLD = PairItem(id=1, code="REQ-001", number=1, status="suggested", statement="Pages shall load in 2 seconds.")
NEW = PairItem(id=2, code="REQ-002", number=2, status="suggested", statement="Each page shall load in 2 seconds.")
ACCEPTED = PairItem(id=1, code="REQ-001", number=1, status="accepted", statement=OLD.statement)


def category(old: PairItem, s: float, c: float | None = None, new: PairItem = NEW) -> str | None:
    found = classify_pair(old, new, s, c)
    return found.category if found else None


@pytest.mark.parametrize(
    "s, c, expected",
    [
        (DUPLICATE, 0.0, "duplicate"),
        (DUPLICATE - 0.001, 0.0, None),
        (0.99, None, "duplicate"),
        (0.9, CONTRADICTION, "conflict"),  # conflict wins over duplicate
        (0.9, CONTRADICTION - 0.001, "duplicate"),
        (CONFLICT_CANDIDATE, 0.95, "conflict"),
        (CONFLICT_CANDIDATE - 0.001, 0.95, None),  # too far apart: NLI does not count
        (0.3, None, None),
    ],
)
def test_open_pairs(s, c, expected):
    assert category(OLD, s, c) == expected


@pytest.mark.parametrize(
    "s, c, new_statement, expected",
    [
        (CHANGE_CANDIDATE, 0.0, NEW.statement, "change_request"),  # similar but different
        (CHANGE_CANDIDATE - 0.001, 0.0, NEW.statement, None),
        (0.8, 0.0, NEW.statement, "change_request"),
        (0.95, 0.9, NEW.statement, "change_request"),  # contradicts the accepted one
        (0.95, 0.0, "Each page shall load in 5 seconds.", "change_request"),  # other numbers
        (0.95, 0.0, NEW.statement, "duplicate"),  # says the same again: a plain duplicate
        (0.55, 0.9, NEW.statement, "conflict"),  # below the change threshold the conflict stays
    ],
)
def test_pairs_with_an_accepted_requirement(s, c, new_statement, expected):
    new = PairItem(id=2, code="REQ-002", number=2, status="suggested", statement=new_statement)
    assert category(ACCEPTED, s, c, new) == expected


def test_accepting_the_newer_one_keeps_it_a_change_request():
    accepted_new = PairItem(id=2, code="REQ-002", number=2, status="accepted", statement=NEW.statement)
    assert category(ACCEPTED, 0.8, 0.0, accepted_new) == "change_request"


def test_only_the_older_one_being_accepted_makes_a_change_request():
    accepted_new = PairItem(id=2, code="REQ-002", number=2, status="accepted", statement=NEW.statement)
    assert category(OLD, 0.8, 0.0, accepted_new) is None


def test_a_requirement_is_never_paired_with_itself():
    assert classify_pair(OLD, OLD, 1.0, 0.99) is None


def test_the_finding_goes_on_the_newer_one_and_shows_the_older_statement():
    found = classify_pair(OLD, NEW, 0.91, 0.1)
    assert (found.owner_id, found.related_id) == (2, 1)
    assert found.evidence == OLD.statement
    assert found.score == 0.91
    assert found.reason.startswith("Possible duplicate of REQ-001 (similarity 0.91)")


@pytest.mark.parametrize(
    "s, c, start, severity, score",
    [
        (0.9, 0.82, "Possible conflict with REQ-001: the model gives a 0.82 chance", "high", 0.82),
        (0.9, 0.1, "Possible duplicate of REQ-001", "medium", 0.9),
    ],
)
def test_wording_is_cautious(s, c, start, severity, score):
    found = classify_pair(OLD, NEW, s, c)
    assert found.reason.startswith(start)
    assert (found.severity, found.score) == (severity, score)
    assert "safe" not in found.reason.lower()


@pytest.mark.parametrize(
    "c, statement, says",
    [
        (0.86, NEW.statement, "may contradict it (contradiction 0.86)"),
        (0.1, "Each page shall load in 5 seconds.", "gives different numbers"),
        (0.1, "Each page shall show a spinner for 2 seconds.", "says something different"),
    ],
)
def test_change_request_reason_says_how_it_differs(c, statement, says):
    new = PairItem(id=2, code="REQ-002", number=2, status="suggested", statement=statement)
    found = classify_pair(ACCEPTED, new, 0.7, c)
    assert found.rule == "change_request"
    assert found.reason.startswith("Possible change request for REQ-001, which is already accepted")
    assert says in found.reason


@pytest.mark.parametrize(
    "a, b, expected",
    [
        ("load in 2 seconds", "load in 5 seconds", True),
        ("load in 2 seconds", "load within 2 seconds", False),
        ("up to 1,000 users", "up to 1000 users", False),
        ("৩ দিনে ডেলিভারি", "3 days delivery", False),  # Bengali digits are the same numbers
        ("load in 2 seconds", "load quickly", True),  # only one gives a number
        ("show a report", "show a chart", False),
    ],
)
def test_numbers_differ(a, b, expected):
    assert numbers_differ(a, b) is expected
