"""The validation rules, on small examples in English, Bangla and Banglish. No database."""

import unicodedata

import pytest

from app.nlp.validate import Draft, RequirementInput, check_missing_info, check_vague, has_measurable_value


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


# ---------- missing information ----------


def rules(drafts: list[Draft]) -> list[str]:
    return [d.rule for d in drafts]


@pytest.mark.parametrize(
    "statement",
    [
        "The system shall export orders as CSV.",
        "report ta PDF e download hok lagbe",
        "রিপোর্ট PDF এ ডাউনলোড করা যাবে।",
    ],
)
def test_no_actor(statement):
    drafts = check_missing_info(RequirementInput(statement, sources=["The client's sentence."]))

    assert rules(drafts) == ["no_actor"]
    assert drafts[0].category == "missing_info"
    assert drafts[0].severity == "medium"
    assert drafts[0].evidence == "The client's sentence."


def test_evidence_is_the_statement_when_the_source_was_deleted():
    drafts = check_missing_info(RequirementInput("The system shall export orders."))
    assert drafts[0].evidence == "The system shall export orders."


@pytest.mark.parametrize(
    "statement",
    [
        "Admin should be able to export all orders.",
        "The system shall let customers pay online.",
        "grahok order dite parbe",
        "customer login korte parbe",
        "গ্রাহক অর্ডার দিতে পারবে।",
        "অ্যাডমিনকে রিপোর্ট দেখাতে হবে।",  # with a case ending
    ],
)
def test_actor_named_in_the_statement(statement):
    assert check_missing_info(RequirementInput(statement)) == []


def test_actor_field_counts():
    assert check_missing_info(RequirementInput("The system shall export orders.", actor="delivery driver")) == []
    assert rules(check_missing_info(RequirementInput("The system shall export orders.", actor="   "))) == ["no_actor"]


def test_no_actor_is_not_checked_for_non_functional():
    item = RequirementInput("The system shall respond within 2 seconds.", type="non_functional")
    assert check_missing_info(item) == []


def nfr(statement: str) -> RequirementInput:
    return RequirementInput(statement, type="non_functional", sources=["Source sentence."])


@pytest.mark.parametrize(
    "statement",
    [
        "The system shall respond quickly.",
        "The system shall use OAuth 2.",  # a version, not a value
        "site ta nirapod hote hobe",
        "সাইট নিরাপদ হতে হবে।",
    ],
)
def test_no_measurable_value(statement):
    drafts = check_missing_info(nfr(statement))

    assert rules(drafts) == ["no_measurable_value"]
    assert drafts[0].severity == "high"
    assert drafts[0].evidence == "Source sentence."


@pytest.mark.parametrize(
    "statement",
    [
        "The page shall load in 2 seconds.",
        "The page shall load in 200ms.",
        "Uptime shall be 99.9%.",
        "The system shall support 1,000 concurrent users.",
        "The system shall allow at most 5 logins.",
        "The system shall respond within two seconds.",
        "2 second er moddhe load hobe",
        "dui minute e report ashbe",
        "5 jon eksathe login korte parbe",
        "sorbochcho 5 ta file",
        "২ সেকেন্ডের মধ্যে লোড হবে।",
        "৯৯.৯% সময় চালু থাকবে।",
        "৩ দিনের মধ্যে রিপোর্ট আসবে।",
        "দুই মিনিটে রিপোর্ট আসবে।",
        "সর্বোচ্চ ৫ বার চেষ্টা করা যাবে।",
        "৫টি ফাইল লাগবে।",
    ],
)
def test_measurable_value_found(statement):
    assert has_measurable_value(statement)
    assert check_missing_info(nfr(statement)) == []


@pytest.mark.parametrize(
    "statement, word",
    [
        ("Admin can upload files.", "upload"),
        ("The session of a user shall expire.", "expire"),
        ("The admin shall get a reminder.", "reminder"),
        ("user er session expire hobe", "expire"),
        ("admin file upload korte parbe", "upload"),
        ("user er jonno meyad thakbe", "meyad"),
        ("ব্যবহারকারী ফাইল আপলোড করতে পারবে।", "আপলোড"),
        ("গ্রাহকের সেশনের মেয়াদ শেষ হবে।", "মেয়াদ"),
        ("অ্যাডমিন ডাউনলোডের সীমা ঠিক করবে।", "সীমা"),
    ],
)
def test_missing_limit(statement, word):
    drafts = check_missing_info(RequirementInput(statement))

    assert rules(drafts) == ["missing_limit"]
    assert drafts[0].severity == "medium"
    assert unicodedata.normalize("NFC", word) == drafts[0].evidence


@pytest.mark.parametrize(
    "statement",
    [
        "Admin can upload files up to 5 MB.",
        "The session of a user shall expire after 30 minutes.",
        "admin 2 mb porjonto file upload korte parbe",
        "ব্যবহারকারী ৫ এমবি পর্যন্ত ফাইল আপলোড করতে পারবে।",
    ],
)
def test_limit_with_a_value_is_fine(statement):
    assert check_missing_info(RequirementInput(statement)) == []


def test_missing_limit_is_only_for_functional():
    # A non-functional requirement already gets no_measurable_value; one finding is enough
    assert rules(check_missing_info(nfr("Backups shall be kept."))) == ["no_measurable_value"]
