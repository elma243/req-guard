"""Clarification questions from validation findings (AR-04) and updated statements from the client's answers
(FR-09). Both go through app/ai/llm.py (rule 3) and are only suggestions: the lead edits, shares and accepts.

- `generate_questions`: 1 to 3 short, polite questions per requirement, each linked to the open finding it
  resolves (vague, missing information, possible conflict, possible change request). Requirements are sent in
  batches. Rule 2: only questions that are still AI suggestions, in no link and without answers, are replaced.
- `propose_update`: an updated statement from the answers no accepted proposal has used yet, or "not enough
  information".
"""

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from app.ai.llm import LLMClient, LLMError, now
from app.connectors.base import as_utc
from app.models import (
    ClarificationAnswer,
    ClarificationLink,
    ClarificationProposal,
    ClarificationQuestion,
    Requirement,
    ValidationFinding,
)
from app.schemas.clarification import (
    MAX_QUESTION_CHARS,
    GeneratedQuestions,
    GenerateItem,
    GenerateResult,
    ProposalAnswer,
    RequirementQuestions,
)

# Findings a client can resolve by answering a question
QUESTION_CATEGORIES = ("vague", "missing_info", "conflict", "change_request")
MAX_QUESTIONS = 3
# Requirements per LLM call
BATCH_SIZE = 8
# Time for all question batches of one request; stays below the frontend proxy's 285 s
LLM_SECONDS = 150.0
# The client sentences sent with a requirement
MAX_SOURCE_SENTENCES = 3


def utcnow() -> datetime:
    return datetime.now(UTC)


# ---------- states ----------


def link_state(link: ClarificationLink, at: datetime) -> str:
    if link.submitted_at is not None:
        return "answered"
    if link.revoked_at is not None:
        return "revoked"
    if as_utc(link.expires_at) <= at:
        return "expired"
    return "active"


def question_state(question: ClarificationQuestion, at: datetime) -> str:
    if question.answers:
        return "answered"
    if any(link_state(link, at) == "active" for link in question.links):
        return "shared"
    return "draft"


def is_closed(requirement: Requirement) -> bool:
    """Rejected, merged or approved: no new questions are written for it."""
    return requirement.status in ("rejected", "merged") or requirement.approval is not None


def open_question_findings(requirement: Requirement) -> list[ValidationFinding]:
    """Open findings of the kinds a question can resolve and that no question covers yet."""
    covered = {q.finding_id for q in requirement.questions}
    return [
        f
        for f in requirement.findings
        if f.category in QUESTION_CATEGORIES and f.status != "rejected" and f.id not in covered
    ]


def is_replaceable(question: ClarificationQuestion) -> bool:
    """Rule 2: an untouched AI suggestion that was never shared or answered."""
    return question.origin == "ai" and question.status == "suggested" and not question.links and not question.answers


# ---------- questions ----------


def _finding_payload(f: ValidationFinding) -> dict:
    item = {"finding_id": f.id, "category": f.category, "reason": f.reason, "evidence": f.evidence}
    if f.related_requirement_id is not None:
        item["related_statement"] = f.related_statement
        if f.explanation:
            item["explanation"] = f.explanation
    return item


def _payload(requirement: Requirement, findings: Sequence[ValidationFinding]) -> dict:
    current = requirement.current
    return {
        "requirement_id": requirement.id,
        "statement": current.statement,
        "actor": current.actor,
        "client_sentences": [s.source_sentence for s in requirement.sources[:MAX_SOURCE_SENTENCES]],
        "findings": [_finding_payload(f) for f in findings],
    }


def _store_questions(
    requirement: Requirement,
    findings: Sequence[ValidationFinding],
    answer: RequirementQuestions | None,
    model: str | None,
) -> int:
    """Keep the AI's usable questions: a known finding id, one question per finding, not too long, no repeats."""
    if answer is None or answer.not_enough_information:
        return 0
    valid = {f.id for f in findings}
    used_findings: set[int] = set()
    seen = {q.text.casefold() for q in requirement.questions}
    created = 0
    for item in answer.questions:
        text = item.text.strip()
        if (
            created == MAX_QUESTIONS
            or item.finding_id not in valid
            or item.finding_id in used_findings
            or not text
            or len(text) > MAX_QUESTION_CHARS
            or text.casefold() in seen
        ):
            continue
        requirement.questions.append(
            ClarificationQuestion(
                finding_id=item.finding_id, text=text, origin="ai", status="suggested", ai_model=model
            )
        )
        used_findings.add(item.finding_id)
        seen.add(text.casefold())
        created += 1
    return created


def generate_questions(requirements: Iterable[Requirement], llm: LLMClient, *, replace: bool) -> GenerateResult:
    """Write questions for the requirements that need them. The caller commits.

    `replace` first removes the requirement's replaceable questions (rule 2), so "Generate" on one requirement
    writes them again. An LLM error on the first batch is raised (nothing is stored); on a later batch the
    earlier batches are kept and the result names the error.
    """
    work: list[tuple[Requirement, list[ValidationFinding]]] = []
    for requirement in requirements:
        if is_closed(requirement):
            continue
        if replace:
            for question in [q for q in requirement.questions if is_replaceable(q)]:
                requirement.questions.remove(question)
        findings = open_question_findings(requirement)
        if findings:
            work.append((requirement, findings))

    items: list[GenerateItem] = []
    error: str | None = None
    deadline = now() + LLM_SECONDS
    for start in range(0, len(work), BATCH_SIZE):
        batch = work[start : start + BATCH_SIZE]
        try:
            answer = llm.complete_json(
                "clarification_questions",
                {"requirements": [_payload(r, findings) for r, findings in batch]},
                GeneratedQuestions,
                deadline=deadline,
            )
        except LLMError as exc:
            if not items:
                raise
            error = str(exc)
            break
        by_id = {a.requirement_id: a for a in answer.requirements}
        for requirement, findings in batch:
            created = _store_questions(requirement, findings, by_id.get(requirement.id), llm.last_model)
            items.append(
                GenerateItem(
                    requirement_id=requirement.id,
                    code=requirement.code,
                    created=created,
                    not_enough_information=created == 0,
                )
            )
    return GenerateResult(created=sum(i.created for i in items), items=items, error=error)


# ---------- proposals ----------


class NoNewAnswers(Exception):
    """Every answer of the requirement was already used by an accepted proposal."""


def unused_answers(requirement: Requirement) -> list[ClarificationAnswer]:
    used = {
        answer_id
        for p in requirement.proposals
        if p.status in ("accepted", "edited")
        for answer_id in p.answer_ids
    }
    return [a for a in requirement.answers if a.id not in used]


def propose_update(requirement: Requirement, llm: LLMClient) -> ClarificationProposal:
    """Ask the AI for an updated statement from the unused answers; store it as a suggestion. The caller commits.

    Older proposals that are still suggestions are replaced (rule 2). LLMError is raised to the caller.
    """
    answers = unused_answers(requirement)
    if not answers:
        raise NoNewAnswers
    current = requirement.current
    findings = [
        {"category": f.category, "reason": f.reason, "evidence": f.evidence}
        for f in requirement.findings
        if f.status != "rejected"
    ]
    result = llm.complete_json(
        "clarification_proposal",
        {
            "statement": current.statement,
            "actor": current.actor or "(not given)",
            "findings": findings or "(none)",
            "answers": [{"question": a.question.text, "answer": a.text} for a in answers],
        },
        ProposalAnswer,
        deadline=now() + LLM_SECONDS,
    )
    statement = result.statement.strip()
    not_enough = result.not_enough_information or not statement
    reason = result.reason
    if not not_enough and statement == current.statement.strip():
        not_enough, reason = True, reason or "The answers do not seem to change the statement."
    if not_enough:
        statement, reason = "", reason or "Not enough information in the answers to update the statement."

    for old in [p for p in requirement.proposals if p.status == "suggested"]:
        requirement.proposals.remove(old)
    proposal = ClarificationProposal(
        based_on_version=current.version,
        answer_ids=[a.id for a in answers],
        statement=statement or None,
        reason=reason,
        not_enough_information=not_enough,
        ai_model=llm.last_model,
        origin="ai",
        status="suggested",
    )
    requirement.proposals.append(proposal)
    return proposal
