"""Extraction pipeline: sentence splitting, small-talk filter, batching, id → exact sentence, storing. Fake LLM."""

from datetime import datetime, timedelta, timezone

import pytest

from app.ai import llm as llm_module
from app.ai.llm import load_prompt
from app.core.config import settings
from app.models import Message, Project, Requirement, User
from app.nlp import extraction
from app.nlp.extraction import (
    MessageInput,
    Sentence,
    extract_requirements,
    is_small_talk,
    make_batches,
    resolve_sentences,
    split_sentences,
)
from app.schemas.extraction import ExtractedRequirement, ExtractionAnswer
from tests.fake_llm import FakeLLM

BANGLA_OTP = "লগইন পেজে OTP লাগবে।"
BANGLISH_PDF = "ami chai report ta PDF e download hok."

# ---------- sentences ----------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("The app must load in 2 seconds. Also add dark mode", ["The app must load in 2 seconds.", "Also add dark mode"]),
        (f"{BANGLA_OTP} {BANGLISH_PDF}", [BANGLA_OTP, BANGLISH_PDF]),
        ("খুব জরুরি।রিপোর্ট দরকার", ["খুব জরুরি।", "রিপোর্ট দরকার"]),  # danda without a space
        ("A few things:\n- Drivers see the route\n\n- SMS to customers  ", ["A few things:", "- Drivers see the route", "- SMS to customers"]),
        ("Kalke meeting ta 3 tay hobe? Ji", ["Kalke meeting ta 3 tay hobe?", "Ji"]),
        ("", []),
        ("  \n ", []),
    ],
)
def test_split_sentences(text, expected):
    assert split_sentences(text) == expected


def test_sentences_are_exact_substrings():
    text = "Hi!  লগইন পেজে OTP লাগবে।ami chai report 📄 ta PDF e download hok\nThanks, dhonnobad"
    for sentence in split_sentences(text):
        assert sentence in text


# ---------- small talk ----------


@pytest.mark.parametrize(
    "sentence",
    ["ok", "Okay!!", "Thanks a lot", "Thank you so much 🙏", "Hi team,", "kemon acho?", "Bhalo achi, apni?",
     "Dhonnobad bhai", "ধন্যবাদ", "ঠিক আছে", "জি, ধন্যবাদ।", "👍", "...", "Good morning everyone", "Assalamualaikum"],
)  # fmt: skip
def test_small_talk(sentence):
    assert is_small_talk(sentence)


@pytest.mark.parametrize(
    "sentence",
    [BANGLA_OTP, BANGLISH_PDF, "Ok, add dark mode", "Thanks, and the app must be fast", "Kalke meeting ta 3 tay hobe?",
     "No SMS at night", "Login lagbe"],
)  # fmt: skip
def test_not_small_talk(sentence):
    assert not is_small_talk(sentence)


# ---------- batches ----------


def _item(message_id: int, chars: int) -> MessageInput:
    message = Message(id=message_id, text="x", author="", channel="", sent_at=datetime.now(timezone.utc))
    return MessageInput(message=message, sentences=[Sentence(id=f"m{message_id}.s1", message_id=message_id, text="x" * chars)])


def test_batches_respect_message_limit():
    batches = make_batches([_item(i, 10) for i in range(1, 8)], max_messages=3, max_chars=10_000)
    assert [len(b) for b in batches] == [3, 3, 1]


def test_batches_respect_char_limit_and_keep_long_messages():
    batches = make_batches([_item(1, 40), _item(2, 40), _item(3, 500), _item(4, 10)], max_messages=10, max_chars=100)
    assert [[i.message.id for i in b] for b in batches] == [[1, 2], [3], [4]]


def test_no_batches_for_nothing():
    assert make_batches([]) == []


def test_default_batch_is_8_messages():
    assert [len(b) for b in make_batches([_item(i, 10) for i in range(1, 21)])] == [8, 8, 4]


# ---------- ids → exact sentences ----------


def test_resolve_maps_ids_to_exact_text_and_drops_unknown(caplog):
    sentences = {
        "m5.s1": Sentence("m5.s1", 5, BANGLA_OTP),
        "m5.s2": Sentence("m5.s2", 5, BANGLISH_PDF),
    }
    common = {"type": "functional", "confidence": 0.9}
    answer = ExtractionAnswer(
        requirements=[
            ExtractedRequirement(statement="The system shall use OTP.", sentence_ids=["m5.s1"], **common),
            ExtractedRequirement(statement="Both.", sentence_ids=["m5.s2", " m5.s1", "m5.s2"], **common),
            ExtractedRequirement(statement="Invented.", sentence_ids=["m99.s1"], **common),
        ]
    )

    found = resolve_sentences(answer, sentences)

    assert [f.sources for f in found] == [[(5, BANGLA_OTP)], [(5, BANGLISH_PDF), (5, BANGLA_OTP)]]
    assert "dropped 1 unknown sentence id" in caplog.text


def test_nfr_category_follows_type():
    base = {"statement": "s", "sentence_ids": ["m1.s1"], "confidence": 0.5}
    assert ExtractedRequirement(type="functional", nfr_category="security", **base).nfr_category is None
    assert ExtractedRequirement(type="non_functional", **base).nfr_category == "other"
    with pytest.raises(ValueError):
        ExtractedRequirement(type="non_functional", nfr_category="speed", **base)
    with pytest.raises(ValueError):
        ExtractedRequirement(type="functional", **{**base, "confidence": 1.5})


def test_prompt_has_batch_placeholder_and_language_note():
    prompt = load_prompt("extract_requirements")
    assert "{{batch}}" in prompt
    assert "Banglish" in prompt
    assert "Do not guess" in prompt


# ---------- the run ----------


@pytest.fixture
def db(db_session_factory):
    session = db_session_factory()
    yield session
    session.close()


@pytest.fixture
def project(db):
    project = Project(name="Karim Traders", owner=User(name="Owner", email="o@example.com", password_hash="x"))
    db.add(project)
    db.commit()
    return project


def add_messages(db, project, *texts: str, channel: str = "client") -> list[Message]:
    start = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
    messages = [
        Message(
            project_id=project.id,
            source="whatsapp",
            external_id=f"{channel}-{i}-{text[:10]}",
            channel=channel,
            author="Karim",
            sent_at=start + timedelta(minutes=i),
            text=text,
            raw={},
        )
        for i, text in enumerate(texts)
    ]
    db.add_all(messages)
    db.commit()
    return messages


def test_run_stores_exact_sentences_and_marks_all_processed(db, project):
    otp, small, csv = add_messages(db, project, f"{BANGLA_OTP} {BANGLISH_PDF}", "ok, thanks!", "Admin should be able to export all orders as CSV.")
    llm = FakeLLM()

    result = extract_requirements(db, project, llm)

    assert (result.messages_processed, result.messages_small_talk, result.messages_remaining) == (3, 1, 0)
    assert (result.requirements_created, result.llm_calls, result.error) == (4, 1, None)
    assert "ok, thanks!" not in llm.sentences_sent

    reqs = db.query(Requirement).order_by(Requirement.number).all()
    assert [r.code for r in reqs] == ["REQ-001", "REQ-002", "REQ-003", "REQ-004"]
    assert all((r.origin, r.status) == ("ai", "suggested") for r in reqs)
    first = reqs[0]
    assert [(s.message_id, s.source_sentence) for s in first.sources] == [(otp.id, BANGLA_OTP)]
    assert (first.current.version, first.current.origin, first.current.nfr_category) == (1, "ai", "security")
    assert all(r.current.ai_model == "fake-model" for r in reqs)
    assert reqs[1].sources[0].source_sentence == BANGLISH_PDF
    assert reqs[1].current.nfr_category is None
    assert all(m.requirements_extracted_at for m in (otp, small, csv))
    assert db.get(Project, project.id).requirement_counter == 4


def test_context_before_is_the_previous_message_of_the_same_chat(db, project):
    add_messages(db, project, "Do you need anything else?", "Admin should be able to export all orders as CSV.")
    add_messages(db, project, "Unrelated chat", channel="other")
    llm = FakeLLM()

    extract_requirements(db, project, llm)

    payload = {m["sentences"][0]["text"]: m for m in llm.calls[0]["batch"]}
    assert payload["Admin should be able to export all orders as CSV."]["context_before"] == "Karim: Do you need anything else?"
    assert payload["Do you need anything else?"]["context_before"] == ""


def test_zero_requirement_message_is_marked_processed(db, project):
    (meeting,) = add_messages(db, project, "Kalke meeting ta 3 tay hobe?")

    result = extract_requirements(db, project, FakeLLM())

    assert (result.messages_processed, result.requirements_created, result.llm_calls) == (1, 0, 1)
    assert meeting.requirements_extracted_at is not None
    assert extract_requirements(db, project, FakeLLM()).llm_calls == 0


def test_only_small_talk_needs_no_llm_call(db, project):
    add_messages(db, project, "Assalamualaikum, kemon acho sobai?", "ধন্যবাদ 👍")
    llm = FakeLLM()

    result = extract_requirements(db, project, llm)

    assert (result.messages_processed, result.messages_small_talk, result.llm_calls) == (2, 2, 0)
    assert llm.calls == []


def test_llm_error_keeps_finished_batches(db, project, monkeypatch):
    monkeypatch.setattr(extraction, "MAX_BATCH_MESSAGES", 1)
    first, second = add_messages(db, project, f"{BANGLA_OTP}", "Admin should be able to export all orders as CSV.")

    result = extract_requirements(db, project, FakeLLM(fail_on_call=2))

    assert (result.messages_processed, result.messages_remaining, result.requirements_created) == (1, 1, 1)
    assert "invalid answer twice" in result.error
    db.refresh(first)
    db.refresh(second)
    assert first.requirements_extracted_at is not None
    assert second.requirements_extracted_at is None

    again = extract_requirements(db, project, FakeLLM())
    assert (again.messages_processed, again.requirements_created, again.error) == (1, 2, None)


class _Busy:
    name, model = "gemini", "main-model"

    def generate(self, prompt, **options):
        raise llm_module.LLMUnavailableError("Gemini is overloaded (HTTP 503). Try again in a few minutes.")


class _Answers:
    name, model = "gemini", "fallback-model"

    def __init__(self, answer: str | None):
        self.answer = answer

    def generate(self, prompt, **options):
        if self.answer is None:
            raise llm_module.LLMUnavailableError("Gemini daily free quota used up. Try again tomorrow.")
        return llm_module.LLMResponse(text=self.answer)


def test_requirements_store_the_fallback_model_that_answered(db, project):
    (message,) = add_messages(db, project, "Admin should be able to export all orders as CSV.")
    answer = (
        '{"requirements": [{"statement": "The system shall export orders as CSV.", "type": "functional", '
        f'"sentence_ids": ["m{message.id}.s1"], "confidence": 0.9}}]}}'
    )
    llm = llm_module.LLMClient(_Busy(), _Answers(answer))

    result = extract_requirements(db, project, llm)

    assert (result.requirements_created, result.error) == (1, None)
    assert db.query(Requirement).one().current.ai_model == "fallback-model"


def test_both_models_unavailable_is_the_run_error(db, project):
    (message,) = add_messages(db, project, "Admin should be able to export all orders as CSV.")

    result = extract_requirements(db, project, llm_module.LLMClient(_Busy(), _Answers(None)))

    assert result.error == "Both Gemini models are busy or out of free quota. Try again later."
    assert (result.requirements_created, result.messages_remaining) == (0, 1)
    db.refresh(message)
    assert message.requirements_extracted_at is None


def test_llm_gets_thinking_level_and_run_deadline(db, project, monkeypatch):
    monkeypatch.setattr(llm_module, "clock", lambda: 500.0)
    monkeypatch.setattr(settings, "llm_extraction_thinking", "low")
    add_messages(db, project, BANGLA_OTP)
    llm = FakeLLM()

    extract_requirements(db, project, llm)

    assert llm.options == [{"thinking": "low", "deadline": 500.0 + extraction.RUN_TIME_BUDGET_SECONDS}]


def test_thinking_default_sends_no_level(db, project, monkeypatch):
    monkeypatch.setattr(settings, "llm_extraction_thinking", "default")
    add_messages(db, project, BANGLA_OTP)
    llm = FakeLLM()

    extract_requirements(db, project, llm)

    assert llm.options[0]["thinking"] is None


def test_run_stops_starting_batches_when_time_is_up(db, project, monkeypatch):
    times = iter([0.0, 5.0, 230.0])  # deadline set at 0; batch 1 starts at 5; batch 2 would start at 230
    monkeypatch.setattr(llm_module, "clock", lambda: next(times))
    monkeypatch.setattr(extraction, "MAX_BATCH_MESSAGES", 1)
    add_messages(db, project, "ok", BANGLA_OTP, "Admin should be able to export all orders as CSV.")
    llm = FakeLLM()

    result = extract_requirements(db, project, llm)

    assert len(llm.calls) == 1
    assert (result.messages_processed, result.messages_remaining, result.error) == (2, 1, None)


def test_per_run_limit(db, project, monkeypatch):
    monkeypatch.setattr(extraction, "MAX_MESSAGES_PER_RUN", 2)
    add_messages(db, project, "Login lagbe 1", "Login lagbe 2", "Login lagbe 3")

    first = extract_requirements(db, project, FakeLLM())
    second = extract_requirements(db, project, FakeLLM())

    assert (first.messages_processed, first.messages_remaining) == (2, 1)
    assert (second.messages_processed, second.messages_remaining) == (1, 0)
