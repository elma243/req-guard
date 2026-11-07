"""Validation through the API: it runs after extraction and on edit, merge and restore, and on the Validate button."""

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.api.deps import get_optional_llm_client
from app.main import app
from app.models import Requirement, ValidationFinding
from tests.fake_llm import FakeLLM

FIXTURES = Path(__file__).parent / "fixtures"
BANGLA_OTP = "লগইন পেজে OTP লাগবে।"
MODULES = [
    {"name": "Authentication", "description": "login password OTP"},
    {"name": "Orders", "description": "order list and CSV export"},
]
# Every line a client's sentence with a cue word, so the rule-based extraction makes one requirement of each
CHAT = (
    "20/10/26, 10:05 - Karim: Order list lagbe.\n"
    "20/10/26, 10:06 - Karim: Report taratari lagbe.\n"
    "20/10/26, 10:07 - Karim: রিপোর্ট দ্রুত লাগবে।\n"
)


@pytest.fixture
def owner(client, make_user):
    _, headers = make_user()
    project = client.post("/projects", json={"name": "Karim Traders"}, headers=headers).json()
    return headers, f"/projects/{project['id']}"


def upload(client, owner, chat: bytes) -> None:
    headers, base = owner
    res = client.post(f"{base}/uploads", files=[("files", ("WhatsApp Chat with Karim.txt", chat, "text/plain"))], headers=headers)
    assert res.status_code == 200, res.text


@pytest.fixture
def extracted(client, owner):
    """The mixed-language chat, extracted by the fake LLM."""
    app.dependency_overrides[get_optional_llm_client] = lambda: FakeLLM()
    upload(client, owner, (FIXTURES / "whatsapp_mixed_language.txt").read_bytes())
    assert extract(client, owner).status_code == 200
    return owner


@pytest.fixture
def rule_based(client, owner):
    """A small chat extracted without an AI model: each statement is the client's own sentence."""
    app.dependency_overrides[get_optional_llm_client] = lambda: None
    upload(client, owner, CHAT.encode("utf-8"))
    assert extract(client, owner).status_code == 200
    return owner


def extract(client, owner, **body):
    headers, base = owner
    return client.post(f"{base}/requirements/extract", json=body or None, headers=headers)


def validate(client, owner):
    headers, base = owner
    res = client.post(f"{base}/requirements/validate", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def by_code(client, owner) -> dict[str, dict]:
    headers, base = owner
    return {r["code"]: r for r in client.get(f"{base}/requirements", headers=headers).json()}


def detail(client, owner, code: str) -> dict:
    headers, base = owner
    rid = by_code(client, owner)[code]["id"]
    return client.get(f"{base}/requirements/{rid}", headers=headers).json()


def edit(client, owner, code: str, **changes) -> dict:
    headers, base = owner
    rid = by_code(client, owner)[code]["id"]
    res = client.patch(f"{base}/requirements/{rid}", json=changes, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def set_modules(client, owner, modules: list[dict]) -> None:
    headers, base = owner
    assert client.patch(base, json={"modules": modules}, headers=headers).status_code == 200


# ---------- after extraction ----------


def test_extraction_validates_every_new_requirement(client, extracted):
    items = by_code(client, extracted)

    assert all(r["quality_score"] is not None for r in items.values())
    otp = detail(client, extracted, "REQ-001")
    [finding] = otp["findings"]
    assert (finding["category"], finding["rule"], finding["severity"]) == ("missing_info", "no_measurable_value", "high")
    assert (finding["origin"], finding["status"], finding["version"]) == ("ai", "suggested", 1)
    assert finding["evidence"] == BANGLA_OTP  # the client's sentence, unchanged
    assert otp["has_issues"] and otp["open_findings"] == 1
    assert [f["criterion"] for f in otp["quality_failed"]] == ["verifiable"]
    assert otp["quality_score"] == round(100 * 50 / 75)  # no modules or scope, and no actor check for non-functional

    pdf = items["REQ-002"]
    assert (pdf["quality_score"], pdf["open_findings"], pdf["has_issues"]) == (100, 0, False)


def test_the_list_has_counts_and_the_detail_has_findings(client, extracted):
    assert "findings" not in by_code(client, extracted)["REQ-001"]
    assert len(detail(client, extracted, "REQ-001")["findings"]) == 1


def test_rule_based_extraction_is_validated_in_all_three_languages(client, rule_based):
    assert by_code(client, rule_based).keys() == {"REQ-001", "REQ-002", "REQ-003"}

    assert {f["rule"] for f in detail(client, rule_based, "REQ-001")["findings"]} == {"no_actor"}
    assert {f["evidence"] for f in detail(client, rule_based, "REQ-002")["findings"] if f["rule"] == "vague_word"} == {
        "taratari"
    }
    assert {f["evidence"] for f in detail(client, rule_based, "REQ-003")["findings"] if f["rule"] == "vague_word"} == {
        "দ্রুত"
    }


def test_a_second_extraction_does_not_validate_old_requirements_again(client, extracted):
    set_modules(client, extracted, MODULES)

    assert extract(client, extracted).status_code == 200

    assert [f["rule"] for f in detail(client, extracted, "REQ-002")["findings"]] == []


def test_forced_extraction_replaces_findings_with_their_requirements(client, extracted, db_session_factory):
    before = len(by_code(client, extracted))

    assert extract(client, extracted, force=True).status_code == 200

    items = by_code(client, extracted)
    assert len(items) == before
    assert all(r["quality_score"] is not None for r in items.values())
    with db_session_factory() as db:
        ids = {f.requirement_id for f in db.scalars(select(ValidationFinding))}
        assert ids == {items["REQ-006"]["id"]}  # only the new OTP requirement; nothing left of the old ones


# ---------- on edit ----------


def test_edit_fixing_the_problem_clears_the_finding(client, extracted):
    updated = edit(client, extracted, "REQ-001", statement="The system shall send the OTP within 30 seconds.")

    assert updated["findings"] == []
    assert updated["quality_score"] == 100
    assert not updated["has_issues"]
    assert updated["status"] == "edited"


def test_edit_adding_a_problem_gives_a_finding_on_the_new_version(client, extracted):
    assert by_code(client, extracted)["REQ-002"]["quality_score"] == 100

    updated = edit(client, extracted, "REQ-002", statement="The system shall let the user download the report fast.")

    [finding] = updated["findings"]
    assert (finding["rule"], finding["evidence"], finding["version"]) == ("vague_word", "fast", 2)
    assert updated["quality_score"] < 100


def test_adding_an_actor_clears_no_actor(client, rule_based):
    assert "no_actor" in {f["rule"] for f in detail(client, rule_based, "REQ-001")["findings"]}

    updated = edit(client, rule_based, "REQ-001", actor="manager")

    assert "no_actor" not in {f["rule"] for f in updated["findings"]}


def test_a_person_rewriting_the_statement_clears_the_clients_vague_word(client, rule_based):
    before = detail(client, rule_based, "REQ-002")
    assert "taratari" in {f["evidence"] for f in before["findings"]}

    updated = edit(client, rule_based, "REQ-002", statement="The manager shall get the report in 2 hours.")

    assert updated["findings"] == []
    assert updated["sources"][0]["source_sentence"] == "Report taratari lagbe."  # the source stays as it was


def test_changing_the_type_changes_what_is_checked(client, extracted):
    updated = edit(client, extracted, "REQ-002", type="non_functional")

    assert [f["rule"] for f in updated["findings"]] == ["no_measurable_value"]


def test_a_failed_edit_stores_nothing(client, extracted):
    headers, base = extracted
    rid = by_code(client, extracted)["REQ-001"]["id"]
    assert client.post(f"{base}/requirements/{rid}/reject", headers=headers).status_code == 200

    res = client.patch(f"{base}/requirements/{rid}", json={"statement": "Fast."}, headers=headers)

    assert res.status_code == 409
    assert [f["rule"] for f in detail(client, extracted, "REQ-001")["findings"]] == ["no_measurable_value"]


# ---------- Validate button ----------


def test_validate_applies_new_modules(client, extracted):
    set_modules(client, extracted, MODULES)

    result = validate(client, extracted)

    items = by_code(client, extracted)
    assert result["validated"] == len(items) == 5
    assert (items["REQ-001"]["module_name"], items["REQ-004"]["module_name"]) == ("Authentication", "Orders")
    assert items["REQ-001"]["module_score"] > 0
    # The PDF report and the app speed match neither module
    assert items["REQ-002"]["module_name"] is None
    assert [f["rule"] for f in detail(client, extracted, "REQ-002")["findings"]] == ["no_matching_module"]
    assert [f["criterion"] for f in items["REQ-002"]["quality_failed"]] == ["aligned"]


def test_validate_twice_changes_nothing(client, extracted):
    set_modules(client, extracted, MODULES)
    first = validate(client, extracted)
    findings = detail(client, extracted, "REQ-002")["findings"]

    assert validate(client, extracted) == first
    assert detail(client, extracted, "REQ-002")["findings"] == findings


def test_validate_counts_issues(client, extracted):
    result = validate(client, extracted)

    items = by_code(client, extracted).values()
    assert result["with_issues"] == sum(r["has_issues"] for r in items) == 1
    assert result["findings"] == sum(r["open_findings"] for r in items) == 1


def test_validate_with_no_requirements(client, owner):
    assert validate(client, owner) == {
        "validated": 0,
        "with_issues": 0,
        "findings": 0,
        "similarity_unavailable_reason": None,
    }


def test_validate_gives_a_score_to_requirements_from_before_this_step(client, extracted, db_session_factory):
    with db_session_factory() as db:
        for requirement in db.scalars(select(Requirement)):
            requirement.quality_score = None
        db.commit()
    assert all(r["quality_score"] is None for r in by_code(client, extracted).values())

    validate(client, extracted)

    assert all(r["quality_score"] is not None for r in by_code(client, extracted).values())


# ---------- findings list ----------


def test_findings_list_covers_the_project_by_requirement(client, rule_based):
    headers, base = rule_based

    res = client.get(f"{base}/requirements/findings", headers=headers)

    assert res.status_code == 200, res.text
    numbers = [f["requirement_id"] for f in res.json()]
    assert numbers == sorted(numbers)
    assert {f["rule"] for f in res.json()} >= {"vague_word", "no_actor"}


def test_findings_of_another_project_are_not_listed(client, extracted, make_user):
    headers, _ = extracted
    project = client.post("/projects", json={"name": "Other"}, headers=headers).json()

    assert client.get(f"/projects/{project['id']}/requirements/findings", headers=headers).json() == []


def test_only_the_owner_can_validate_or_read_findings(client, extracted, make_user):
    _, base = extracted
    _, stranger = make_user()

    assert client.post(f"{base}/requirements/validate", headers=stranger).status_code == 404
    assert client.get(f"{base}/requirements/findings", headers=stranger).status_code == 404


def test_validation_needs_a_login(client, extracted):
    _, base = extracted

    assert client.post(f"{base}/requirements/validate").status_code == 401
    assert client.get(f"{base}/requirements/findings").status_code == 401


# ---------- merge and restore ----------


def test_merge_and_restore_keep_the_target_up_to_date(client, rule_based):
    headers, base = rule_based
    items = by_code(client, rule_based)
    target, other = items["REQ-001"], items["REQ-002"]
    assert "taratari" not in {f["evidence"] for f in detail(client, rule_based, "REQ-001")["findings"]}

    res = client.post(f"{base}/requirements/{target['id']}/merge", json={"other_ids": [other["id"]]}, headers=headers)

    assert res.status_code == 200, res.text
    merged = res.json()
    assert "taratari" in {f["evidence"] for f in merged["findings"]}  # the merged-in client sentence
    assert merged["quality_score"] < target["quality_score"]

    assert client.post(f"{base}/requirements/{other['id']}/restore", headers=headers).status_code == 200

    restored = detail(client, rule_based, "REQ-001")
    assert "taratari" not in {f["evidence"] for f in restored["findings"]}
    assert restored["quality_score"] == target["quality_score"]


# ---------- deleting ----------


def test_deleting_the_project_removes_its_findings(client, extracted, db_session_factory):
    headers, base = extracted
    with db_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(ValidationFinding)) > 0

    assert client.delete(base, headers=headers).status_code == 204

    with db_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(ValidationFinding)) == 0
