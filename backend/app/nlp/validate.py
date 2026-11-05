"""Rule-based requirement validation (BUILD_PLAN Step 7, part A). No LLM, no embeddings.

Each rule reads a RequirementInput and returns Drafts: possible problems, always worded cautiously (CLAUDE.md
rule 7). The word lists are in app/nlp/lexicon.py.

Text is NFC-normalised before matching, because ড় ঢ় য় can be typed in two Unicode forms. Requirement statements
are usually English, but the rule-based extraction fallback stores the client's own Bangla or Banglish sentence
as the statement, so every rule handles all three languages.
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.nlp.lexicon import (
    ACTOR_WORDS,
    ACTOR_WORDS_BANGLA,
    COMPARATORS,
    LIMIT_WORDS,
    LIMIT_WORDS_BANGLA,
    NUMBER_WORDS,
    UNITS,
    UNITS_BANGLA,
    VAGUE_WORDS,
)

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

_NUMBER = rf"(?:\d+(?:[.,]\d+)*|{_alternatives(NUMBER_WORDS)})"  # \d also matches Bengali digits
_MEASURABLE = re.compile(
    # A number with a unit: "2 seconds", "200ms", "99.9%", "৩ দিনে"
    rf"(?<![{_WORD_CHARS}.]){_NUMBER}\s*-?\s*"
    rf"(?:%|(?:{_alternatives(UNITS)})(?![{_WORD_CHARS}])|{_alternatives(UNITS_BANGLA)})"
    # A comparator with a number: "at most 5", "সর্বোচ্চ ৫"
    rf"|(?<![{_WORD_CHARS}])(?:{_alternatives(COMPARATORS)})\s+(?:of\s+)?{_NUMBER}(?![{_WORD_CHARS}])",
    re.IGNORECASE,
)


def has_measurable_value(text: str) -> bool:
    return _MEASURABLE.search(_normal(text)) is not None


def check_missing_info(item: RequirementInput) -> list[Draft]:
    """No actor (functional), no measurable value (non-functional), or no value where a limit is expected."""
    statement = _normal(item.statement)
    if item.type == "non_functional":
        if has_measurable_value(statement):
            return []
        reason = "No measurable value was found, so this may be hard to test."
        return [Draft("missing_info", "no_measurable_value", "high", reason, item.evidence)]

    drafts: list[Draft] = []
    if not item.actor.strip() and _ACTOR.search(statement) is None:
        reason = "No actor was found, so it may be unclear who needs this."
        drafts.append(Draft("missing_info", "no_actor", "medium", reason, item.evidence))
    limit = _LIMIT.search(statement)
    if limit and not has_measurable_value(statement):
        reason = f'"{limit.group()}" may need a time or a limit, but no value was found.'
        drafts.append(Draft("missing_info", "missing_limit", "medium", reason, limit.group()))
    return drafts
