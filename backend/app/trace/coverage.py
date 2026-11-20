"""Coverage of one requirement by its trace links (Step 10). Pure functions, no database.

A link counts ("live") when a person accepted it, or when it is explicit (the ID was found) and not rejected. AI
suggestions count only once confirmed.

Levels:
- not_started: no live link
- code_only: live code links (files, commits, PRs), no live test link
- tests_only: live test links, no live code link
- code_and_tests: both
- verified: both, and every accepted acceptance criterion has a Step 8B test case that a live test function link
  matches. A requirement without accepted criteria cannot reach it.

A test function matches a test case when its name or docstring mentions the case (TC-2), or when its name without
"test_" and without requirement IDs equals the case title as words, or shares at least 80 % of the words.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from app.trace.ids import REQ_ID, TC_ID, find_tc_numbers

LEVELS = ("not_started", "code_only", "tests_only", "code_and_tests", "verified")
WORD_OVERLAP = 0.8
_WORD = re.compile(r"[a-z0-9]+")


class LinkLike(Protocol):
    id: int
    link_type: str
    target_type: str
    target_key: str
    target_detail: str | None

    @property
    def live(self) -> bool: ...


@dataclass
class CaseRef:
    id: int
    number: int
    code: str
    title: str
    criterion_id: int | None


@dataclass
class CriterionRef:
    id: int
    code: str
    text: str


@dataclass
class CriterionResult:
    criterion: CriterionRef
    covered: bool
    test_cases: list[str]


@dataclass
class Coverage:
    level: str
    reason: str
    criteria: list[CriterionResult] = field(default_factory=list)
    # link id -> codes of the test cases it matches
    matches: dict[int, list[str]] = field(default_factory=dict)


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def name_words(key: str) -> list[str]:
    """Words of a test function's name, without "test" and requirement or test case IDs."""
    name = key.split("::")[-1].split(".")[-1]
    name = TC_ID.sub(" ", REQ_ID.sub(" ", name))
    words = _words(name.replace("_", " "))
    if words and words[0] == "test":
        words = words[1:]
    return words


def matches_case(key: str, detail: str | None, case: CaseRef) -> bool:
    name = key.split("::")[-1]
    if case.number in find_tc_numbers(name) or case.number in find_tc_numbers(detail):
        return True
    test_words, title_words = name_words(key), _words(case.title)
    if not test_words or not title_words:
        return False
    if test_words == title_words:
        return True
    a, b = set(test_words), set(title_words)
    return len(a & b) / len(a | b) >= WORD_OVERLAP


def match_test_cases(link: LinkLike, cases: Iterable[CaseRef]) -> list[CaseRef]:
    if link.target_type != "test_function":
        return []
    return [c for c in cases if matches_case(link.target_key, link.target_detail, c)]


def coverage(links: Iterable[LinkLike], criteria: list[CriterionRef], cases: list[CaseRef]) -> Coverage:
    """`criteria`: the accepted criteria; `cases`: the test cases nobody rejected."""
    links = list(links)
    matches = {link.id: [c.code for c in match_test_cases(link, cases)] for link in links}
    live = [link for link in links if link.live]
    has_code = any(link.link_type == "implements" for link in live)
    has_tests = any(link.link_type == "verifies" for link in live)

    matched_case_ids = {
        c.id for link in live for c in match_test_cases(link, cases)
    }
    results = []
    for criterion in criteria:
        own = [c for c in cases if c.criterion_id == criterion.id]
        covered = [c.code for c in own if c.id in matched_case_ids]
        results.append(CriterionResult(criterion, bool(covered), covered))

    if not has_code and not has_tests:
        level, reason = "not_started", "No confirmed code or test link yet."
    elif not has_tests:
        level, reason = "code_only", "Code is linked but no test is: possible test gap."
    elif not has_code:
        level, reason = "tests_only", "Tests are linked but no code is: possible traceability gap."
    elif not criteria:
        level, reason = "code_and_tests", "Code and tests are linked; there are no accepted acceptance criteria to check."
    else:
        done = sum(1 for r in results if r.covered)
        if done == len(results):
            level = "verified"
            reason = f"Every acceptance criterion ({done}) has a linked test."
        else:
            level = "code_and_tests"
            reason = f"{done} of {len(results)} acceptance criteria have a linked test: potential test gap."
    return Coverage(level, reason, results, matches)
