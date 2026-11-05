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

from app.nlp.lexicon import VAGUE_WORDS

# Word characters. \w alone misses Bengali vowel signs, which is also why \b is unreliable in Bangla text.
_WORD_CHARS = r"\wঀ-৿"


def _normal(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _alternatives(terms: Iterable[str]) -> str:
    """Terms as regex alternatives, longest first so "as soon as possible" wins over "soon"."""
    ordered = sorted({_normal(t) for t in terms}, key=len, reverse=True)
    return "|".join(r"[\s-]+".join(re.escape(part) for part in term.split(" ")) for term in ordered)


def _whole_words(terms: Iterable[str]) -> re.Pattern:
    return re.compile(rf"(?<![{_WORD_CHARS}])(?:{_alternatives(terms)})(?![{_WORD_CHARS}])", re.IGNORECASE)


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
