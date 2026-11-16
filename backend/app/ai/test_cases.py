"""Acceptance criteria and Given/When/Then test cases for approved requirements (AR-05), through app/ai/llm.py
(rule 3). They are only suggestions: the lead accepts, edits or rejects them (rule 2).

- One LLM call per requirement, all within one time limit; requirements not reached are reported as skipped.
- Generating again replaces only untouched AI suggestions (`ai` + `suggested`). Accepted, edited, user-written
  and rejected items stay, and so does a suggested criterion that a kept test case links to.
- When the AI answers "not enough information", or too few usable test cases are left, nothing is stored.
"""

from collections.abc import Iterable

from app.ai.llm import LLMClient, LLMError, now
from app.models import (
    AcceptanceCriterion,
    AcceptanceCriterionVersion,
    Requirement,
    TestCase,
    TestCaseVersion,
)
from app.schemas.test_case import (
    MAX_CRITERION_CHARS,
    MAX_STEP_CHARS,
    MAX_TITLE_CHARS,
    GeneratedTestCases,
    GenerateTestCasesItem,
    GenerateTestCasesResult,
)

MAX_TEST_CASES = 5
MIN_TEST_CASES = 2
# Time for all calls of one request; stays below the frontend proxy's 285 s
LLM_SECONDS = 150.0
# No call is started with less time than this left
MIN_CALL_SECONDS = 15.0
MAX_SOURCE_SENTENCES = 3

NOT_ENOUGH = "Not enough information in the requirement to write test cases."
TOO_FEW = "The AI's answer had fewer than 2 usable test cases, so nothing was stored."


def is_replaceable(item: AcceptanceCriterion | TestCase) -> bool:
    """Rule 2: an untouched AI suggestion."""
    return item.origin == "ai" and item.status == "suggested"


def needs_generation(requirement: Requirement) -> bool:
    """For "Generate for all approved": approved, and with no test cases yet or with untouched AI suggestions
    that were written for an older version."""
    if requirement.approval is None:
        return False
    if not requirement.live_test_cases:
        return True
    items = [*requirement.criteria, *requirement.test_cases]
    return any(is_replaceable(item) and item.needs_review for item in items)


def _kept(requirement: Requirement) -> tuple[list[TestCase], list[AcceptanceCriterion]]:
    """What a regeneration keeps: every test case that is not an untouched suggestion, and every criterion that
    is not one either or that any version of a kept test case links to."""
    tests = [t for t in requirement.test_cases if not is_replaceable(t)]
    linked = {v.criterion_id for t in tests for v in t.versions}
    criteria = [c for c in requirement.criteria if not is_replaceable(c) or c.id in linked]
    return tests, criteria


def _payload(requirement: Requirement, tests: list[TestCase], criteria: list[AcceptanceCriterion]) -> dict:
    current = requirement.current
    live_criteria = [c for c in criteria if c.status != "rejected"]
    return {
        "requirement": {
            "statement": current.statement,
            "type": current.type,
            "nfr_category": current.nfr_category,
            "actor": current.actor or "(not given)",
            "client_sentences": [s.source_sentence for s in requirement.sources[:MAX_SOURCE_SENTENCES]],
        },
        "existing_criteria": [{"ref": f"E{c.id}", "text": c.current.text} for c in live_criteria] or "(none)",
        # Rejected titles too, so the AI does not suggest what the lead turned down
        "existing_test_cases": [t.current.title for t in tests] or "(none)",
    }


def _remove_replaceable(requirement: Requirement, tests: list[TestCase], criteria: list[AcceptanceCriterion]) -> None:
    for test in [t for t in requirement.test_cases if t not in tests]:
        requirement.test_cases.remove(test)
    for criterion in [c for c in requirement.criteria if c not in criteria]:
        requirement.criteria.remove(criterion)


def _fits(text: str, limit: int) -> bool:
    return bool(text) and len(text) <= limit


def _store(requirement: Requirement, answer: GeneratedTestCases, model: str | None) -> GenerateTestCasesItem:
    """Keep the AI's usable items: known criterion refs, no empty or over-long fields, no repeated titles."""

    def item(tests: int = 0, criteria: int = 0, not_enough: bool = False, reason: str = "") -> GenerateTestCasesItem:
        return GenerateTestCasesItem(
            requirement_id=requirement.id,
            code=requirement.code,
            created_test_cases=tests,
            created_criteria=criteria,
            not_enough_information=not_enough,
            reason=reason,
        )

    if answer.not_enough_information:
        return item(not_enough=True, reason=answer.reason or NOT_ENOUGH)

    existing = {f"E{c.id}": c for c in requirement.criteria if c.status != "rejected"}
    known_texts = {c.current.text.casefold() for c in requirement.criteria}
    new_texts: dict[str, str] = {}
    for criterion in answer.criteria:
        ref, text = criterion.ref, criterion.text
        if not ref or ref in existing or ref in new_texts or not _fits(text, MAX_CRITERION_CHARS):
            continue
        if text.casefold() in known_texts:
            continue
        new_texts[ref] = text
        known_texts.add(text.casefold())

    titles = {t.current.title.casefold() for t in requirement.test_cases}
    usable = []
    for test in answer.test_cases:
        if len(usable) == MAX_TEST_CASES:
            break
        steps_fit = all(_fits(s, MAX_STEP_CHARS) for s in (test.given, test.when, test.then))
        if not _fits(test.title, MAX_TITLE_CHARS) or not steps_fit or test.title.casefold() in titles:
            continue
        if test.criterion_ref not in existing and test.criterion_ref not in new_texts:
            continue
        usable.append(test)
        titles.add(test.title.casefold())

    has_kept = bool(requirement.live_test_cases)
    if not usable and has_kept:
        return item(reason="The AI found nothing new to add to the kept test cases.")
    if len(usable) < MIN_TEST_CASES and not has_kept:
        return item(not_enough=True, reason=answer.reason or TOO_FEW)

    version = requirement.current.version
    created: dict[str, AcceptanceCriterion] = {}
    for ref, text in new_texts.items():
        if not any(t.criterion_ref == ref for t in usable):
            continue  # a criterion no test case checks is left out
        requirement.criterion_counter = (requirement.criterion_counter or 0) + 1
        criterion = AcceptanceCriterion(
            number=requirement.criterion_counter,
            origin="ai",
            status="suggested",
            requirement_version=version,
            ai_model=model,
            versions=[AcceptanceCriterionVersion(version=1, text=text, origin="ai")],
        )
        requirement.criteria.append(criterion)
        created[ref] = criterion

    for test in usable:
        requirement.test_case_counter = (requirement.test_case_counter or 0) + 1
        requirement.test_cases.append(
            TestCase(
                number=requirement.test_case_counter,
                origin="ai",
                status="suggested",
                requirement_version=version,
                ai_model=model,
                versions=[
                    TestCaseVersion(
                        version=1,
                        title=test.title,
                        type=test.type,
                        given=test.given,
                        when=test.when,
                        then=test.then,
                        criterion=created.get(test.criterion_ref) or existing[test.criterion_ref],
                        origin="ai",
                    )
                ],
            )
        )
    return item(tests=len(usable), criteria=len(created))


def generate_test_cases(requirements: Iterable[Requirement], llm: LLMClient) -> GenerateTestCasesResult:
    """Write criteria and test cases for the approved requirements given. The caller commits.

    Untouched AI suggestions of a requirement are replaced only once its answer arrived, so a failed call leaves
    them as they were. An LLM error on the first call is raised (nothing is stored); on a later call the earlier
    results are kept and the result names the error.
    """
    work = [r for r in requirements if r.approval is not None]
    items: list[GenerateTestCasesItem] = []
    skipped: list[str] = []
    error: str | None = None
    deadline = now() + LLM_SECONDS
    for index, requirement in enumerate(work):
        if now() + MIN_CALL_SECONDS > deadline:
            skipped = [r.code for r in work[index:]]
            break
        tests, criteria = _kept(requirement)
        try:
            answer = llm.complete_json(
                "test_cases", _payload(requirement, tests, criteria), GeneratedTestCases, deadline=deadline
            )
        except LLMError as exc:
            if not items:
                raise
            error = str(exc)
            skipped = [r.code for r in work[index:]]
            break
        _remove_replaceable(requirement, tests, criteria)
        items.append(_store(requirement, answer, llm.last_model))
    return GenerateTestCasesResult(
        created_test_cases=sum(i.created_test_cases for i in items),
        created_criteria=sum(i.created_criteria for i in items),
        items=items,
        skipped=skipped,
        error=error,
    )


def mark_for_review(requirement: Requirement) -> None:
    """The requirement got a new version: its criteria and test cases were written for the old text."""
    for item in [*requirement.criteria, *requirement.test_cases]:
        if item.status != "rejected":
            item.needs_review = True
