"""Requirement extraction from client messages (BUILD_PLAN Step 5).

1. Split each message into sentences (spaCy's rule-based sentencizer; works for English, Bangla, Banglish).
2. Drop sentences that are only greetings / small talk (simple word rules).
3. Send the remaining sentences in batches, with the previous message as context, to the LLM prompt
   "extract_requirements". The LLM answers with sentence ids, which are mapped back to the exact original
   text here, so the source sentence is never retyped by the LLM (CLAUDE.md rule 10).
4. Store each requirement as an AI suggestion (origin="ai", status="suggested") with version 1 and its
   source links, and mark the messages as processed.

Messages are read once. A re-run (force=True or chosen message ids) only replaces AI suggestions that are
still "suggested"; accepted, edited, rejected, merged and user-created requirements are never touched (rule 2).
"""

import logging
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.ai.llm import LLMClient, LLMError, now, thinking_level
from app.core.config import settings
from app.models import Message, Project, Requirement, RequirementSource, RequirementVersion
from app.models.requirement import requirement_code
from app.schemas.extraction import ExtractedRequirement, ExtractionAnswer, ExtractResult

logger = logging.getLogger(__name__)

PROMPT = "extract_requirements"
# Limits for one run (one click on "Extract requirements"); the result says how many messages are left
MAX_MESSAGES_PER_RUN = 100
# One run stops starting new batches after this many seconds, and every LLM call (with its retries) gets
# this deadline, so the request always ends before the frontend proxy gives up (BACKEND_TIMEOUT_MS = 285 s
# in frontend/app/api/backend/[...path]/route.ts). Keep the two in step.
RUN_TIME_BUDGET_SECONDS = 240.0
# A new batch is only started with at least this much of the budget left
MIN_BATCH_SECONDS = 30.0
MAX_BATCH_MESSAGES = 8
MAX_BATCH_CHARS = 6000
CONTEXT_CHARS = 300

# ---------- 1. sentences ----------

# Line breaks always end a sentence; so does the Bangla danda (।), even without a space after it
_HARD_BREAK = re.compile(r"[^\n।]+।?|।")


@lru_cache(maxsize=1)
def _nlp():
    import spacy  # imported here: loading spaCy takes a few seconds and most requests never need it

    nlp = spacy.blank("xx")  # multi-language tokenizer, no model download
    nlp.add_pipe("sentencizer")  # its default punctuation includes . ! ? and ।
    return nlp


def split_sentences(text: str) -> list[str]:
    """Sentences of a message, each an exact substring of `text` (only trimmed)."""
    sentences: list[str] = []
    for part in _HARD_BREAK.findall(text):
        if not part.strip():
            continue
        for sent in _nlp()(part).sents:
            if sent.text.strip():
                sentences.append(sent.text.strip())
    return sentences


# ---------- 2. small talk ----------

# A sentence made only of these words is small talk. English, Banglish and Bangla.
SMALL_TALK_WORDS = {
    # English
    "ok", "okay", "okk", "okey", "k", "kk", "hi", "hello", "hey", "dear", "thanks", "thank", "you", "thx", "ty",
    "tnx", "welcome", "bye", "goodbye", "yes", "yeah", "yep", "yup", "no", "nope", "sure", "great", "nice", "cool",
    "good", "fine", "noted", "lot", "lots", "got", "it", "alright", "right", "perfect", "awesome", "morning", "evening", "night",
    "afternoon", "team", "all", "everyone", "guys", "sir", "madam", "maam", "there", "so", "much", "very", "too",
    "again", "and", "a", "the", "lol", "haha", "hehe", "hmm", "hm", "wow", "cheers", "regards", "best", "brother",
    "bro", "see", "later", "soon", "talk", "will", "do", "done", "how", "are", "u", "r", "doing", "am", "i", "we",
    # Banglish
    "accha", "acha", "achha", "thik", "ache", "ase", "ji", "jee", "jii", "hae", "ha", "haan", "na", "bhai", "vai",
    "bhaiya", "apa", "apu", "kemon", "acho", "achen", "achhen", "asen", "aso", "bhalo", "valo", "achi", "asi",
    "dhonnobad", "dhonyobad", "dhanyabad", "onek", "assalamualaikum", "salam", "walaikum", "khoda", "hafez",
    "allah", "shuvo", "sokal", "shokal", "ratri", "apnake", "apni", "tumi", "ami", "o", "sobai", "shobai",
    # Bangla
    "ধন্যবাদ", "ঠিক", "আছে", "আচ্ছা", "জি", "জ্বি", "হ্যাঁ", "হ্যা", "না", "ভালো", "আছি", "কেমন", "আছেন", "আছো",
    "হাই", "হ্যালো", "ওকে", "ভাই", "আপা", "অনেক", "আসসালামু", "আলাইকুম", "সালাম", "শুভ", "সকাল", "আপনি", "আমি",
}  # fmt: skip


def _words(text: str) -> list[str]:
    """Lower-case words with punctuation, symbols and emoji removed (Bangla vowel signs kept)."""
    kept = "".join(ch if unicodedata.category(ch)[0] in "LMN" else " " for ch in text.lower())
    return kept.split()


def is_small_talk(sentence: str) -> bool:
    words = _words(sentence)
    return all(w in SMALL_TALK_WORDS for w in words)  # also True for emoji/punctuation only


# ---------- 3. batches ----------


@dataclass
class Sentence:
    id: str
    message_id: int
    text: str


@dataclass
class MessageInput:
    message: Message
    sentences: list[Sentence]
    context_before: str = ""

    @property
    def size(self) -> int:
        return len(self.context_before) + sum(len(s.text) for s in self.sentences)

    def payload(self) -> dict:
        m = self.message
        return {
            "message_id": m.id,
            "author": m.author,
            "channel": m.channel,
            "sent_at": m.sent_at.strftime("%Y-%m-%d %H:%M"),
            "context_before": self.context_before,
            "sentences": [{"id": s.id, "text": s.text} for s in self.sentences],
        }


def prepare(message: Message) -> list[Sentence]:
    """Candidate sentences of a message (small talk removed), with ids like "m12.s3"."""
    sentences = [s for s in split_sentences(message.text) if not is_small_talk(s)]
    return [Sentence(id=f"m{message.id}.s{n}", message_id=message.id, text=s) for n, s in enumerate(sentences, 1)]


def make_batches(
    items: list[MessageInput], max_messages: int | None = None, max_chars: int | None = None
) -> list[list[MessageInput]]:
    """Groups of messages for one LLM call each. A single very long message gets a batch of its own."""
    max_messages = max_messages or MAX_BATCH_MESSAGES
    max_chars = max_chars or MAX_BATCH_CHARS
    batches: list[list[MessageInput]] = []
    current: list[MessageInput] = []
    size = 0
    for item in items:
        if current and (len(current) >= max_messages or size + item.size > max_chars):
            batches.append(current)
            current, size = [], 0
        current.append(item)
        size += item.size
    if current:
        batches.append(current)
    return batches


def _context_before(db: Session, message: Message) -> str:
    """The previous message in the same chat, shortened, so the LLM understands replies like "yes, do that"."""
    previous = db.scalars(
        select(Message)
        .where(
            Message.project_id == message.project_id,
            Message.source == message.source,
            Message.channel == message.channel,
            (Message.sent_at < message.sent_at) | ((Message.sent_at == message.sent_at) & (Message.id < message.id)),
        )
        .order_by(Message.sent_at.desc(), Message.id.desc())
        .limit(1)
    ).first()
    if previous is None:
        return ""
    text = previous.text if len(previous.text) <= CONTEXT_CHARS else previous.text[:CONTEXT_CHARS] + "..."
    return f"{previous.author}: {text}" if previous.author else text


# ---------- 4. storing ----------


@dataclass
class _Found:
    item: ExtractedRequirement
    sources: list[tuple[int, str]] = field(default_factory=list)  # (message_id, exact sentence)


def resolve_sentences(answer: ExtractionAnswer, sentences: dict[str, Sentence]) -> list[_Found]:
    """Map the LLM's sentence ids back to exact sentences. Ids not in the batch are dropped."""
    found: list[_Found] = []
    unknown = 0
    for item in answer.requirements:
        sources: list[tuple[int, str]] = []
        for sid in item.sentence_ids:
            sentence = sentences.get(sid.strip())
            if sentence is None:
                unknown += 1
            elif (sentence.message_id, sentence.text) not in sources:
                sources.append((sentence.message_id, sentence.text))
        if sources:
            found.append(_Found(item=item, sources=sources))
    if unknown:
        logger.warning("extraction: dropped %d unknown sentence id(s) from the LLM answer", unknown)
    return found


def _source_key(sources) -> frozenset[tuple[int, str]]:
    return frozenset(sources)


def _delete_replaceable(db: Session, project_id: int, message_ids: set[int]) -> None:
    """Delete AI suggestions (still "suggested") that come only from these messages, before re-extracting them."""
    if not message_ids:
        return
    candidates = db.scalars(
        select(Requirement)
        .join(RequirementSource)
        .where(
            Requirement.project_id == project_id,
            Requirement.origin == "ai",
            Requirement.status == "suggested",
            RequirementSource.message_id.in_(message_ids),
        )
        .distinct()
    ).all()
    for requirement in candidates:
        if all(s.message_id in message_ids for s in requirement.sources):
            db.delete(requirement)
    db.flush()


def _kept_sources(db: Session, project_id: int, message_ids: set[int]) -> Counter:
    """How many kept requirements have each exact set of sources, for the given messages."""
    requirements = db.scalars(
        select(Requirement)
        .join(RequirementSource)
        .where(Requirement.project_id == project_id, RequirementSource.message_id.in_(message_ids))
        .distinct()
    ).all()
    return Counter(_source_key((s.message_id, s.source_sentence) for s in r.sources) for r in requirements)


def _next_numbers(db: Session, project_id: int, count: int) -> range:
    """Reserve `count` REQ-numbers. The row lock makes parallel runs wait (PostgreSQL; SQLite locks the file)."""
    last = db.scalar(select(Project.requirement_counter).where(Project.id == project_id).with_for_update()) or 0
    db.execute(update(Project).where(Project.id == project_id).values(requirement_counter=last + count))
    return range(last + 1, last + count + 1)


def _store(db: Session, project_id: int, found: list[_Found], ai_model: str | None = None) -> int:
    message_ids = {mid for f in found for mid, _ in f.sources}
    kept = _kept_sources(db, project_id, message_ids) if message_ids else Counter()
    new: list[_Found] = []
    for f in found:
        key = _source_key(f.sources)
        if kept[key] > 0:  # a kept requirement already covers these sentences
            kept[key] -= 1
            continue
        new.append(f)
    if not new:
        return 0

    for number, f in zip(_next_numbers(db, project_id, len(new)), new):
        item = f.item
        db.add(
            Requirement(
                project_id=project_id,
                number=number,
                code=requirement_code(number),
                origin="ai",
                status="suggested",
                versions=[
                    RequirementVersion(
                        version=1,
                        statement=item.statement,
                        type=item.type,
                        nfr_category=item.nfr_category,
                        actor=item.actor,
                        confidence=item.confidence,
                        ai_model=ai_model,
                        origin="ai",
                    )
                ],
                sources=[RequirementSource(message_id=mid, source_sentence=text) for mid, text in f.sources],
            )
        )
    return len(new)


def _mark_processed(db: Session, messages: list[Message]) -> None:
    now = datetime.now(timezone.utc)
    for m in messages:
        m.requirements_extracted_at = now


# ---------- the run ----------


def extract_requirements(
    db: Session,
    project: Project,
    llm: LLMClient,
    *,
    force: bool = False,
    message_ids: list[int] | None = None,
) -> ExtractResult:
    """Extract requirements from the project's messages that were not read yet.

    force=True reads all messages again; `message_ids` reads only those (again). Each batch is committed on
    its own, so an LLM error keeps the work already done and the rest can be continued with another run.
    A run ends after RUN_TIME_BUDGET_SECONDS at the latest.
    """
    if force or message_ids is not None:
        # Mark the chosen messages as unread, so a run stopped by the per-run limit continues with a normal run
        reset = update(Message).where(Message.project_id == project.id)
        if message_ids is not None:
            reset = reset.where(Message.id.in_(message_ids))
        db.execute(reset.values(requirements_extracted_at=None))
        db.commit()

    pending = [Message.project_id == project.id, Message.requirements_extracted_at.is_(None)]
    total = db.scalar(select(func.count()).select_from(Message).where(*pending)) or 0
    messages = db.scalars(
        select(Message).where(*pending).order_by(Message.sent_at, Message.id).limit(MAX_MESSAGES_PER_RUN)
    ).all()

    result = ExtractResult()
    items: list[MessageInput] = []
    small_talk: list[Message] = []
    for m in messages:
        sentences = prepare(m)
        if sentences:
            items.append(MessageInput(message=m, sentences=sentences, context_before=_context_before(db, m)))
        else:
            small_talk.append(m)

    if small_talk:
        _delete_replaceable(db, project.id, {m.id for m in small_talk})
        _mark_processed(db, small_talk)
        db.commit()
        result.messages_small_talk = result.messages_processed = len(small_talk)

    deadline = now() + RUN_TIME_BUDGET_SECONDS
    thinking = thinking_level(settings.llm_extraction_thinking)
    for batch in make_batches(items):
        if now() + MIN_BATCH_SECONDS > deadline:
            break  # out of time for this run; the rest stays waiting for the next one
        sentences = {s.id: s for item in batch for s in item.sentences}
        try:
            result.llm_calls += 1
            answer = llm.complete_json(
                PROMPT,
                {"batch": [item.payload() for item in batch]},
                ExtractionAnswer,
                thinking=thinking,
                deadline=deadline,
            )
        except LLMError as exc:
            db.rollback()
            result.error = str(exc)
            break
        _delete_replaceable(db, project.id, {item.message.id for item in batch})
        found = resolve_sentences(answer, sentences)
        result.requirements_created += _store(db, project.id, found, ai_model=llm.last_model)
        _mark_processed(db, [item.message for item in batch])
        db.commit()
        result.messages_processed += len(batch)

    result.messages_remaining = total - result.messages_processed
    return result
