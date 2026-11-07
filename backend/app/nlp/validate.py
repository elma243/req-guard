"""Rule-based requirement validation (BUILD_PLAN Step 7, part A). No LLM, no embeddings.

Each rule reads a RequirementInput and returns Drafts: possible problems, always worded cautiously (CLAUDE.md
rule 7). The word lists are in app/nlp/lexicon.py.

Text is NFC-normalised before matching, because ড় ঢ় য় can be typed in two Unicode forms. Requirement statements
are usually English, but the rule-based extraction fallback stores the client's own Bangla or Banglish sentence
as the statement, so every rule handles all three languages.

validate_requirement stores the result; the rules themselves need no database. Checks that compare two
requirements (duplicates, conflicts, change requests) are in app/nlp/pair_checks.py; validate_project runs both.
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.ai.llm import LLMClient
from app.models import Project, Requirement, ValidationFinding
from app.models.validation import PAIR_CATEGORIES
from app.nlp.extraction import count_requirement_cues, has_requirement_cue
from app.nlp.lexicon import (
    ACTION_VERBS,
    ACTOR_WORDS,
    ACTOR_WORDS_BANGLA,
    COMPARATORS,
    LIMIT_WORDS,
    LIMIT_WORDS_BANGLA,
    MEASURED_TOPIC_WORDS,
    MEASURED_TOPIC_WORDS_BANGLA,
    NUMBER_WORDS,
    STOP_WORDS,
    UNITS,
    UNITS_BANGLA,
    VAGUE_WORDS,
)
from app.nlp.pair_checks import check_similarity
from app.schemas.requirement import ValidateResult

# Word characters. \w alone misses Bengali vowel signs, which is also why \b is unreliable in Bangla text.
_WORD_CHARS = r"\wঀ-৿"


def _normal(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _alternatives(terms: Iterable[str]) -> str:
    """Terms as regex alternatives, longest first so "as soon as possible" wins over "soon"."""
    ordered = sorted({_normal(t) for t in terms}, key=len, reverse=True)
    return "|".join(r"[\s-]+".join(re.escape(part) for part in term.split(" ")) for term in ordered)


def _whole_words(terms: Iterable[str], word_starts: Iterable[str] = ()) -> re.Pattern:
    """`terms` as whole words; `word_starts` may also have an ending (Bangla case endings)."""
    pattern = rf"(?:{_alternatives(terms)})(?![{_WORD_CHARS}])"
    if word_starts:
        pattern = rf"(?:{pattern}|{_alternatives(word_starts)})"
    return re.compile(rf"(?<![{_WORD_CHARS}]){pattern}", re.IGNORECASE)


@dataclass(frozen=True)
class RequirementInput:
    statement: str
    type: str = "functional"  # "functional" or "non_functional"
    nfr_category: str | None = None  # performance, security, usability, reliability, other; None if functional
    actor: str = ""
    version_origin: str = "ai"  # who wrote the current version
    sources: Sequence[str] = ()  # the client's sentences, unchanged
    modules: Sequence[dict] = ()  # the project's modules: {"name", "description"}
    scope_text: str = ""

    @property
    def evidence(self) -> str:
        """The sentence a finding about the whole requirement points at."""
        return self.sources[0] if self.sources else self.statement


@dataclass(frozen=True)
class Draft:
    category: str
    rule: str
    severity: str
    reason: str
    evidence: str

    @property
    def key(self) -> tuple[str, str]:
        """Identifies a finding across re-runs."""
        return (self.rule, self.evidence)


# ---------- vague wording ----------

_VAGUE = _whole_words(VAGUE_WORDS)


def check_vague(item: RequirementInput) -> list[Draft]:
    """Vague words in the statement, and in the client's sentences while the statement is AI-written.

    The client's sentence never changes, so once a person has written the statement that is what counts:
    otherwise the finding could never be cleared.
    """
    drafts: list[Draft] = []
    seen: set[str] = set()

    def add(text: str, severity: str, reason: str) -> None:
        for match in _VAGUE.finditer(_normal(text)):
            word = match.group()
            if word.casefold() not in seen:
                seen.add(word.casefold())
                drafts.append(Draft("vague", "vague_word", severity, reason.format(word=word), word))

    add(
        item.statement,
        "medium",
        '"{word}" may be vague: people may read it differently. A number or an exact list may be clearer.',
    )
    if item.version_origin == "ai":
        for sentence in item.sources:
            add(
                sentence,
                "low",
                'The client\'s sentence says "{word}", which may be vague. It may need a clarifying question.',
            )
    return drafts


# ---------- missing information ----------

_ACTOR = _whole_words(ACTOR_WORDS, ACTOR_WORDS_BANGLA)
_LIMIT = _whole_words(LIMIT_WORDS, LIMIT_WORDS_BANGLA)
_MEASURED_TOPIC = _whole_words(MEASURED_TOPIC_WORDS, MEASURED_TOPIC_WORDS_BANGLA)
# Non-functional categories that are always measured
MEASURED_CATEGORIES = ("performance", "reliability")

_DIGITS = r"\d+(?:[.,]\d+)*"  # \d also matches Bengali digits
_NUMBER = rf"(?:{_DIGITS}|{_alternatives(NUMBER_WORDS)})"
_MEASURABLE = re.compile(
    # A number with a unit: "2 seconds", "2-second", "200ms", "99.9%", "৩ দিনে". Only digits take a hyphen:
    # "one-time password" is no value.
    rf"(?<![{_WORD_CHARS}.])(?:{_DIGITS}\s*-?\s*|(?:{_alternatives(NUMBER_WORDS)})\s*)"
    rf"(?:%|(?:{_alternatives(UNITS)})(?![{_WORD_CHARS}])|{_alternatives(UNITS_BANGLA)})"
    # A comparator with a number: "at most 5", "সর্বোচ্চ ৫"
    rf"|(?<![{_WORD_CHARS}])(?:{_alternatives(COMPARATORS)})\s+(?:of\s+)?{_NUMBER}(?![{_WORD_CHARS}])",
    re.IGNORECASE,
)


def has_measurable_value(text: str) -> bool:
    return _MEASURABLE.search(_normal(text)) is not None


def is_measured_kind(item: RequirementInput) -> bool:
    """A non-functional requirement that is naturally measured: performance or reliability, or one about
    availability, capacity, speed or a limit (e.g. a security timeout). Others, such as "support a dark mode
    theme", are yes / no features."""
    if item.nfr_category in MEASURED_CATEGORIES:
        return True
    statement = _normal(item.statement)
    return _MEASURED_TOPIC.search(statement) is not None or _LIMIT.search(statement) is not None


def check_missing_info(item: RequirementInput) -> list[Draft]:
    """No actor (functional), no measurable value (measured non-functional), or no value where a limit is
    expected. A yes / no non-functional feature only gets a low note to add acceptance criteria."""
    statement = _normal(item.statement)
    if item.type == "non_functional":
        if has_measurable_value(statement):
            return []
        if is_measured_kind(item):
            reason = "No measurable value was found, so this may be hard to test."
            return [Draft("missing_info", "no_measurable_value", "high", reason, item.evidence)]
        reason = "This may be a yes / no feature. Consider adding acceptance criteria that say how to check it."
        return [Draft("missing_info", "add_acceptance_criteria", "low", reason, item.evidence)]

    drafts: list[Draft] = []
    if not item.actor.strip() and _ACTOR.search(statement) is None:
        reason = "No actor was found, so it may be unclear who needs this."
        drafts.append(Draft("missing_info", "no_actor", "medium", reason, item.evidence))
    limit = _LIMIT.search(statement)
    if limit and not has_measurable_value(statement):
        reason = f'"{limit.group()}" may need a time or a limit, but no value was found.'
        drafts.append(Draft("missing_info", "missing_limit", "medium", reason, limit.group()))
    return drafts


# ---------- modules and scope ----------

# Weights of a module's name and description in its score, and how many description words give the full weight
NAME_WEIGHT = 0.6
DESCRIPTION_WEIGHT = 0.4
DESCRIPTION_WORDS = 3
# One word of the name or of the description is a match (0.13 at least): the description is a list of the words
# clients use, so "pay by bKash" fits a module that lists bKash
MODULE_MATCH = 0.1
# Share of the statement's words the scope text must contain
SCOPE_COVERAGE = 0.3

_WORD = re.compile(rf"[{_WORD_CHARS}]+")


def _content_words(text: str) -> set[str]:
    words = {w.lower() for w in _WORD.findall(_normal(text))}
    return {w for w in words if len(w) >= 3 and w not in STOP_WORDS and not w.isdigit()}


def _same_word(a: str, b: str) -> bool:
    """The same word, maybe with an ending: payment, payments, paymenter; পেমেন্ট, পেমেন্টের."""
    short, long = sorted((a.removesuffix("s"), b.removesuffix("s")), key=len)
    return short == long or (len(short) >= 4 and long.startswith(short) and len(long) - len(short) <= 3)


def _hits(terms: set[str], words: set[str]) -> int:
    return sum(1 for term in terms if any(_same_word(term, word) for word in words))


def best_module(item: RequirementInput) -> tuple[str, float] | None:
    """The best-matching module and its score (0-1), or None when no module reaches MODULE_MATCH."""
    words = _content_words(" ".join([item.statement, *item.sources]))
    best: tuple[str, float] | None = None
    for module in item.modules:
        name = _content_words(module["name"])
        description = _content_words(module.get("description", "")) - name
        score = DESCRIPTION_WEIGHT * min(_hits(description, words), DESCRIPTION_WORDS) / DESCRIPTION_WORDS
        if name:
            score += NAME_WEIGHT * _hits(name, words) / len(name)
        if score >= MODULE_MATCH and (best is None or score > best[1]):
            best = (module["name"], round(score, 2))
    return best


def _covered_by_scope(item: RequirementInput) -> bool:
    # Who wants it says nothing about scope: "customer" in the scope text must not cover "the customer plays music"
    words = _content_words(_ACTOR.sub(" ", _normal(item.statement)))
    return not words or _hits(words, _content_words(item.scope_text)) / len(words) >= SCOPE_COVERAGE


def check_alignment(item: RequirementInput, module: tuple[str, float] | None) -> list[Draft]:
    """`module` is the answer of best_module. Nothing is judged for a project without modules and scope."""
    if module is not None:
        return []
    drafts: list[Draft] = []
    if item.modules:
        reason = "No matching module: this may not belong to any module of the project."
        drafts.append(Draft("module_alignment", "no_matching_module", "medium", reason, item.evidence))
    if item.scope_text.strip() and not _covered_by_scope(item):
        reason = "Possible extra work: this may be outside the agreed project scope."
        drafts.append(Draft("out_of_scope", "possible_out_of_scope", "medium", reason, item.evidence))
    return drafts


def run_rules(item: RequirementInput) -> tuple[list[Draft], tuple[str, float] | None]:
    """All findings of a requirement and its best-matching module."""
    module = best_module(item)
    return [*check_vague(item), *check_missing_info(item), *check_alignment(item, module)], module


# ---------- one need per requirement ----------

_ABBREVIATION = re.compile(r"(?<![\w.])(?:e\.g|i\.e|etc|vs)\.", re.IGNORECASE)
_NEXT_SENTENCE = re.compile(r"[.!?।;]\s+\S")
_SECOND_ACTION = re.compile(
    rf"(?<![{_WORD_CHARS}])(?:and(?:\s+then|\s+also)?|then|as\s+well\s+as)\s+"
    rf"(?:{_alternatives(ACTION_VERBS)})(?![{_WORD_CHARS}])",
    re.IGNORECASE,
)


def is_singular(statement: str) -> bool:
    """False when the statement may hold more than one need."""
    text = _normal(statement)
    return (
        count_requirement_cues(text) < 2
        and _NEXT_SENTENCE.search(_ABBREVIATION.sub("", text)) is None
        and _SECOND_ACTION.search(text) is None
    )


# ---------- quality score ----------


def score_quality(item: RequirementInput, findings: Iterable[Draft]) -> tuple[int, list[dict]]:
    """Score 0-100 and the failed criteria, from the ISO/IEC/IEEE 29148 characteristics rules can check.

    Necessary, feasible and correct need a person, so they are not scored. A criterion that does not apply
    (passed is None) leaves the total: the score is passed points / applicable points.
    """
    rules = {f.rule for f in findings}
    aligned_applies = bool(item.modules or item.scope_text.strip())
    criteria = (
        ("unambiguous", 25, "vague_word" not in rules, "Some wording may be vague."),
        (
            "verifiable",
            25,
            not rules & {"no_measurable_value", "missing_limit"},
            "This may be hard to verify: a value or a limit may be missing.",
        ),
        (
            "has_actor",
            15,
            "no_actor" not in rules if item.type == "functional" else None,
            "It may be unclear who needs this.",
        ),
        ("singular", 15, is_singular(item.statement), "This may hold more than one need."),
        (
            "conforming",
            10,
            has_requirement_cue(_normal(item.statement)),
            'This may not use requirement wording such as "shall".',
        ),
        (
            "aligned",
            10,
            not rules & {"no_matching_module", "possible_out_of_scope"} if aligned_applies else None,
            "This may not fit the modules or the scope of the project.",
        ),
    )
    applicable = [c for c in criteria if c[2] is not None]
    passed = sum(points for _, points, ok, _ in applicable if ok)
    total = sum(points for _, points, _, _ in applicable)
    failed = [
        {"criterion": name, "points": points, "reason": reason} for name, points, ok, reason in applicable if not ok
    ]
    return round(100 * passed / total), failed


# ---------- storing ----------


def validate_requirement(requirement: Requirement, project: Project) -> None:
    """Check the current version and store the findings, score and module. The caller commits.

    Rule 2: only findings that are still origin "ai" and status "suggested" are updated or removed. A finding a
    person decided on stays as it is and is not added again, and a rejected one no longer fails its criterion.
    Pair findings belong to app/nlp/pair_checks.py and are left alone here.
    """
    current = requirement.current
    item = RequirementInput(
        statement=current.statement,
        type=current.type,
        nfr_category=current.nfr_category,
        actor=current.actor,
        version_origin=current.origin,
        sources=[s.source_sentence for s in requirement.sources],
        modules=project.modules,
        scope_text=project.scope_text,
    )
    drafts, module = run_rules(item)
    wanted = {d.key: d for d in drafts}

    for finding in list(requirement.findings):
        if finding.origin != "ai" or finding.status != "suggested" or finding.category in PAIR_CATEGORIES:
            continue
        draft = wanted.get((finding.rule, finding.evidence))
        if draft is None:
            requirement.findings.remove(finding)
        else:
            finding.version = current.version
            finding.category, finding.severity, finding.reason = draft.category, draft.severity, draft.reason

    stored = {(f.rule, f.evidence) for f in requirement.findings}
    for draft in drafts:
        if draft.key not in stored:
            requirement.findings.append(
                ValidationFinding(
                    version=current.version,
                    category=draft.category,
                    rule=draft.rule,
                    severity=draft.severity,
                    reason=draft.reason,
                    evidence=draft.evidence,
                    origin="ai",
                    status="suggested",
                )
            )

    rejected = {(f.rule, f.evidence) for f in requirement.findings if f.status == "rejected"}
    requirement.quality_score, requirement.quality_failed = score_quality(
        item, [d for d in drafts if d.key not in rejected]
    )
    requirement.module_name, requirement.module_score = module or (None, None)


def validate_project(
    db: Session, project: Project, *, only_new: bool = False, llm: LLMClient | None = None
) -> ValidateResult:
    """Validate every requirement of the project, or with `only_new` those never validated. The caller commits.

    The pair checks run too: on every pair, or with `only_new` on the pairs with a new requirement. When their
    models are unavailable the rule-based findings are still stored and the result says why. `llm` checks the
    pairs the models flag (see app/nlp/pair_checks.py).
    """
    query = (
        select(Requirement)
        .where(Requirement.project_id == project.id)
        .options(
            selectinload(Requirement.versions),
            selectinload(Requirement.sources),
            selectinload(Requirement.findings),
        )
    )
    if only_new:
        query = query.where(Requirement.quality_score.is_(None))
    requirements = db.scalars(query).all()
    for requirement in requirements:
        validate_requirement(requirement, project)
    unavailable = check_similarity(db, project, [r.id for r in requirements] if only_new else None, llm)
    return ValidateResult(
        validated=len(requirements),
        with_issues=sum(1 for r in requirements if r.has_issues),
        findings=sum(r.open_findings for r in requirements),
        similarity_unavailable_reason=unavailable,
    )
