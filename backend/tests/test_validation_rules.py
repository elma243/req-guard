"""The validation rules, on small examples in English, Bangla and Banglish. No database."""

import unicodedata

import pytest

from app.nlp.validate import Draft, RequirementInput, check_vague


def words(drafts: list[Draft]) -> list[str]:
    return [d.evidence for d in drafts]


# ---------- vague wording ----------


@pytest.mark.parametrize(
    "statement, expected",
    [
        ("The system shall be fast.", ["fast"]),
        ("The system shall be user-friendly and easy.", ["user-friendly", "easy"]),
        ("Reports shall cover orders, invoices, etc.", ["etc"]),
        ("The page shall load as soon as possible.", ["as soon as possible"]),
        ("The system shall be FLEXIBLE.", ["FLEXIBLE"]),
        ("app ta taratari load hobe", ["taratari"]),
        ("login page ta sohoj hote hobe", ["sohoj"]),
        ("onek report lagbe", ["onek"]),
        ("অ্যাপ দ্রুত লোড হবে।", ["দ্রুত"]),
        ("লগইন সহজে করা যাবে।", ["সহজে"]),
        ("কিছু রিপোর্ট লাগবে।", ["কিছু"]),
    ],
)
def test_vague_word_in_the_statement(statement, expected):
    drafts = check_vague(RequirementInput(statement))

    assert words(drafts) == expected
    assert {(d.category, d.rule, d.severity) for d in drafts} == {("vague", "vague_word", "medium")}


@pytest.mark.parametrize(
    "statement",
    [
        "The system shall serve breakfast orders.",  # contains "fast"
        "The system shall store the data somewhere safe.",  # contains "some"
        "The system shall be available in Manyata.",  # contains "many"
        "The system shall log every login within 2 seconds.",
        "কমপক্ষে ৮ অক্ষরের পাসওয়ার্ড লাগবে।",  # contains "কম"
        "কমেন্ট লেখা যাবে।",  # contains "কম"
        "komiti report lagbe",  # contains "kom"
    ],
)
def test_no_vague_word_inside_a_longer_word(statement):
    assert check_vague(RequirementInput(statement)) == []


def test_both_unicode_forms_of_bangla_are_found():
    composed = "রিপোর্ট তাড়াতাড়ি লাগবে।"
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed

    assert len(check_vague(RequirementInput(composed))) == 1
    assert len(check_vague(RequirementInput(decomposed))) == 1


@pytest.mark.parametrize(
    "sentence, word",
    [
        ("The app must be fast.", "fast"),
        ("app ta taratari load hobe", "taratari"),
        ("অ্যাপ দ্রুত লোড হবে।", "দ্রুত"),
    ],
)
def test_vague_word_in_the_source_sentence(sentence, word):
    drafts = check_vague(RequirementInput("The system shall load the app.", sources=[sentence]))

    assert words(drafts) == [word]
    assert drafts[0].severity == "low"
    assert "client" in drafts[0].reason


def test_source_sentence_is_not_judged_after_a_person_wrote_the_statement():
    item = RequirementInput("The system shall load the app.", version_origin="user", sources=["taratari load hobe"])
    assert check_vague(item) == []


def test_a_person_is_still_told_about_their_own_vague_statement():
    item = RequirementInput("The system shall be fast.", version_origin="user", sources=["taratari load hobe"])
    assert words(check_vague(item)) == ["fast"]


def test_same_word_is_reported_once():
    item = RequirementInput("Fast login shall be fast.", sources=["We need fast login."])
    drafts = check_vague(item)

    assert words(drafts) == ["Fast"]
    assert drafts[0].severity == "medium"
