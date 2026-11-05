"""The validation rules, on small examples in English, Bangla and Banglish. No database."""

import unicodedata

import pytest

from app.nlp.validate import (
    Draft,
    RequirementInput,
    best_module,
    check_alignment,
    check_missing_info,
    check_vague,
    has_measurable_value,
    is_singular,
    run_rules,
    score_quality,
)


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
        "The system shall require a one-time password.",
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
        "The system shall give a 30-minute session.",
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


# ---------- modules and scope ----------

MODULES = [
    {"name": "Payments", "description": "bKash, card checkout and refunds পেমেন্ট বিকাশ"},
    {"name": "Order Tracking", "description": "delivery status and courier updates"},
    {"name": "Reports", "description": ""},
]
SCOPE = "An online shop with payments, delivery tracking, customer accounts and sales reports."


@pytest.mark.parametrize(
    "statement, module",
    [
        ("The system shall accept payments by card.", "Payments"),
        ("The system shall export reports.", "Reports"),
        ("The system shall list every order.", "Order Tracking"),  # one word of a two-word name
        ("bKash e payment korte parbe", "Payments"),
        ("reporter PDF download lagbe", "Reports"),  # Banglish ending
        ("বিকাশের পেমেন্টের রসিদ লাগবে।", "Payments"),  # Bangla endings, two words of the description
        ("বিকাশ দিয়ে পেমেন্ট নেওয়া যাবে।", "Payments"),
    ],
)
def test_module_match(statement, module):
    assert best_module(RequirementInput(statement, modules=MODULES))[0] == module


def test_module_name_in_bangla():
    item = RequirementInput("পেমেন্টের রসিদ লাগবে।", modules=[{"name": "পেমেন্ট"}])
    assert best_module(item) == ("পেমেন্ট", 0.6)


def test_module_match_also_reads_the_source_sentence():
    item = RequirementInput("The system shall send a receipt.", sources=["payment er por receipt chai"], modules=MODULES)
    assert best_module(item)[0] == "Payments"


def test_best_module_wins_and_has_a_score():
    item = RequirementInput("The system shall handle refunds and payments by card.", modules=MODULES)
    name, score = best_module(item)

    assert name == "Payments"
    assert 0.6 < score <= 1


def test_one_description_word_is_a_match():
    # The description lists the words clients use
    assert best_module(RequirementInput("The system shall list every courier.", modules=MODULES))[0] == "Order Tracking"


def test_pay_by_bkash_fits_a_module_that_lists_bkash():
    modules = [{"name": "Payments", "description": "bKash card refunds"}]
    assert best_module(RequirementInput("Customer should be able to pay by bKash.", modules=modules))[0] == "Payments"


def test_two_description_words_are_a_match():
    item = RequirementInput("The system shall show the courier for each delivery.", modules=MODULES)
    assert best_module(item)[0] == "Order Tracking"


def test_generic_words_do_not_match():
    modules = [{"name": "User Management", "description": "system features for users"}]
    assert best_module(RequirementInput("The system shall let users see a new page.", modules=modules)) is None


@pytest.mark.parametrize(
    "statement",
    ["The system shall play music.", "gaan bajano hobe", "গান বাজানো হবে।"],
)
def test_no_matching_module(statement):
    item = RequirementInput(statement, modules=MODULES, sources=["The sentence."])
    drafts = check_alignment(item, best_module(item))

    assert rules(drafts) == ["no_matching_module"]
    assert drafts[0].category == "module_alignment"
    assert drafts[0].evidence == "The sentence."


def test_a_project_without_modules_or_scope_is_not_judged():
    item = RequirementInput("The system shall play music.")
    assert best_module(item) is None
    assert check_alignment(item, None) == []


@pytest.mark.parametrize(
    "statement",
    ["The system shall play music.", "gaan bajano hobe", "গান বাজানো হবে।"],
)
def test_possible_out_of_scope(statement):
    item = RequirementInput(statement, scope_text=SCOPE)
    drafts = check_alignment(item, None)

    assert rules(drafts) == ["possible_out_of_scope"]
    assert drafts[0].category == "out_of_scope"


def test_out_of_scope_comes_with_no_matching_module():
    item = RequirementInput("The system shall play music.", modules=MODULES, scope_text=SCOPE)
    assert rules(check_alignment(item, best_module(item))) == ["no_matching_module", "possible_out_of_scope"]


@pytest.mark.parametrize(
    "statement, scope",
    [
        ("The system shall keep delivery tracking accounts.", SCOPE),
        ("delivery tracking lagbe", SCOPE),
        ("ডেলিভারি ট্র্যাকিং লাগবে।", "ডেলিভারি ট্র্যাকিং ও পেমেন্ট সহ অনলাইন দোকান"),
    ],
)
def test_covered_by_the_scope_text(statement, scope):
    assert check_alignment(RequirementInput(statement, scope_text=scope), None) == []


def test_a_module_match_means_no_scope_finding():
    item = RequirementInput("The system shall accept payments.", modules=MODULES, scope_text="A blog.")
    assert check_alignment(item, best_module(item)) == []


# ---------- one need per requirement ----------


@pytest.mark.parametrize(
    "statement",
    [
        "The system shall export orders as CSV.",
        "The system shall export orders and invoices.",  # one action on two things
        "The system shall accept bKash, e.g. for orders.",
        "Admin should be able to export orders.",
        "report ta PDF e download hobe",
        "রিপোর্ট PDF এ ডাউনলোড করা যাবে।",
    ],
)
def test_singular(statement):
    assert is_singular(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "The system shall export orders and shall send invoices.",
        "The system shall export orders and send invoices.",
        "The system shall export orders. It must also print them.",
        "The system shall export orders; admins print them.",
        "The system shall export orders as well as print them.",
        "login korte parbe ar report download korte parbe",
        "OTP lagbe ebong report chai",
        "লগইন করতে পারবে এবং রিপোর্ট দেখা যাবে।",
        "OTP লাগবে। রিপোর্ট চাই।",
    ],
)
def test_more_than_one_need(statement):
    assert not is_singular(statement)


# ---------- quality score ----------


def score(item: RequirementInput) -> tuple[int, list[str]]:
    points, failed = score_quality(item, run_rules(item)[0])
    return points, [f["criterion"] for f in failed]


def test_full_score():
    item = RequirementInput(
        "The system shall accept payments from customers within 2 seconds.", modules=MODULES, scope_text=SCOPE
    )
    assert score(item) == (100, [])


@pytest.mark.parametrize(
    "statement, expected",
    [
        ("The system shall let the customer pay fast.", "unambiguous"),
        ("গ্রাহক দ্রুত অর্ডার দিতে পারবে এবং দেখা হবে।", "unambiguous"),
        ("The customer shall upload files.", "verifiable"),
        ("গ্রাহক ফাইল আপলোড করা যাবে।", "verifiable"),
        ("The system shall export orders.", "has_actor"),
        ("orders export hobe", "has_actor"),
        ("অর্ডার এক্সপোর্ট হবে।", "has_actor"),
        ("The customer shall export orders and send invoices.", "singular"),
        ("customer login korte parbe ar report download korte parbe", "singular"),
        ("গ্রাহক লগইন করতে পারবে এবং রিপোর্ট দেখা যাবে।", "singular"),
        ("Customers export orders.", "conforming"),
        ("customer order dekhe", "conforming"),
        ("গ্রাহক অর্ডার দেখে।", "conforming"),
    ],
)
def test_each_criterion_fails_alone_or_with_others(statement, expected):
    assert expected in score(RequirementInput(statement))[1]


def test_points_of_each_criterion():
    # Applicable: all but "aligned" (no modules or scope), 90 points
    assert score(RequirementInput("The customer shall be fast."))[0] == round(100 * 65 / 90)
    assert score(RequirementInput("The system shall export orders."))[0] == round(100 * 75 / 90)
    assert score(RequirementInput("Customers export orders."))[0] == round(100 * 80 / 90)
    assert score(RequirementInput("The customer shall export orders and send invoices."))[0] == round(100 * 75 / 90)


def test_failed_criteria_have_points_and_a_reason():
    _, failed = score_quality(RequirementInput("The customer shall be fast."), run_rules(RequirementInput("fast"))[0])
    unambiguous = next(f for f in failed if f["criterion"] == "unambiguous")

    assert unambiguous["points"] == 25
    assert unambiguous["reason"]


def test_actor_does_not_apply_to_non_functional():
    item = RequirementInput("The system shall respond within 2 seconds.", type="non_functional")
    assert score(item) == (100, [])


def test_alignment_applies_when_there_are_modules_or_scope():
    statement = "The customer shall play music."
    assert score(RequirementInput(statement)) == (100, [])
    assert score(RequirementInput(statement, modules=MODULES)) == (90, ["aligned"])
    assert score(RequirementInput(statement, scope_text=SCOPE)) == (90, ["aligned"])


def test_a_finding_left_out_does_not_fail_its_criterion():
    item = RequirementInput("The customer shall be fast.")
    assert score_quality(item, [])[0] == 100


def test_worst_case_is_not_negative():
    assert score(RequirementInput("gaan", modules=MODULES))[0] >= 0


# ---------- wording (CLAUDE.md rule 7) ----------


def test_every_reason_is_cautious():
    item = RequirementInput("The system shall upload some files. Then expire them.", sources=["onek file"])
    other = RequirementInput("Backups", type="non_functional", modules=MODULES, scope_text=SCOPE)
    drafts = [*run_rules(item)[0], *run_rules(other)[0]]
    failed = [*score_quality(item, drafts)[1], *score_quality(other, drafts)[1]]

    assert {d.rule for d in drafts} == {
        "vague_word",
        "no_actor",
        "no_measurable_value",
        "missing_limit",
        "no_matching_module",
        "possible_out_of_scope",
    }
    assert {f["criterion"] for f in failed} == {
        "unambiguous",
        "verifiable",
        "has_actor",
        "singular",
        "conforming",
        "aligned",
    }
    for reason in [*(d.reason for d in drafts), *(f["reason"] for f in failed)]:
        assert "may" in reason.lower() or "possible" in reason.lower(), reason
        assert "safe" not in reason.lower()
