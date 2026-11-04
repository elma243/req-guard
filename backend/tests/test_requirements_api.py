"""Requirements API end to end: upload a mixed English/Bangla/Banglish chat, extract with a fake LLM, review."""

from pathlib import Path

import pytest

from app.api.deps import get_optional_llm_client
from app.core.config import settings
from app.main import app
from app.models import Message, Requirement, RequirementSource, RequirementVersion
from tests.fake_llm import FakeLLM

FIXTURES = Path(__file__).parent / "fixtures"
BANGLA_OTP = "লগইন পেজে OTP লাগবে।"
BANGLISH_PDF = "ami chai report ta PDF e download hok."
CSV_SENTENCE = "Admin should be able to export all orders as CSV."


@pytest.fixture
def llm():
    fake = FakeLLM()
    app.dependency_overrides[get_optional_llm_client] = lambda: fake
    return fake


@pytest.fixture
def owner(client, make_user):
    user, headers = make_user()
    project = client.post("/projects", json={"name": "Karim Traders"}, headers=headers).json()
    base = f"/projects/{project['id']}"
    chat = (FIXTURES / "whatsapp_mixed_language.txt").read_bytes()
    res = client.post(f"{base}/uploads", files=[("files", ("WhatsApp Chat with Karim.txt", chat, "text/plain"))], headers=headers)
    assert res.status_code == 200 and res.json()["imported"] == 8, res.text
    return headers, base, user


def extract(client, headers, base, **body):
    return client.post(f"{base}/requirements/extract", json=body or None, headers=headers)


def listed(client, headers, base, **params) -> list[dict]:
    res = client.get(f"{base}/requirements", params=params, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


@pytest.fixture
def extracted(client, owner, llm):
    headers, base, _ = owner
    assert extract(client, headers, base).status_code == 200
    return owner


def by_code(client, headers, base) -> dict[str, dict]:
    return {r["code"]: r for r in listed(client, headers, base)}


# ---------- extract ----------


def test_extract_mixed_language_chat(client, owner, llm):
    headers, base, _ = owner

    res = extract(client, headers, base)

    assert res.status_code == 200, res.text
    assert res.json() == {
        "messages_processed": 8,
        "messages_small_talk": 4,  # salam / bhalo achi + thanks / ok / ধন্যবাদ 👍
        "messages_remaining": 0,
        "requirements_created": 5,
        "llm_calls": 1,
        "rule_based_batches": 0,
        "notice": None,
        "ai_error": None,
    }
    sent = llm.sentences_sent
    assert BANGLA_OTP in sent and BANGLISH_PDF in sent and "Kalke meeting ta 3 tay hobe?" in sent
    assert not any(s in sent for s in ("ok", "ধন্যবাদ 👍", "Thanks!"))

    items = listed(client, headers, base)
    assert [r["code"] for r in items] == ["REQ-001", "REQ-002", "REQ-003", "REQ-004", "REQ-005"]
    assert all((r["origin"], r["status"], r["version_count"]) == ("ai", "suggested", 1) for r in items)

    otp, pdf, load = items[0], items[1], items[2]
    assert otp["current"]["statement"].startswith("The system shall")
    assert (otp["current"]["type"], otp["current"]["nfr_category"], otp["current"]["confidence"]) == (
        "non_functional",
        "security",
        0.92,
    )
    assert otp["current"]["ai_model"] == "fake-model"  # the model that answered is shown with each requirement
    assert (pdf["current"]["type"], pdf["current"]["nfr_category"]) == ("functional", None)
    assert load["current"]["nfr_category"] == "performance"

    # The source sentence is the client's original text, unchanged, with its message
    otp_source = otp["sources"][0]
    assert otp_source["source_sentence"] == BANGLA_OTP
    assert pdf["sources"][0]["source_sentence"] == BANGLISH_PDF
    assert (otp_source["author"], otp_source["source"]) == ("Karim Traders", "whatsapp")
    messages = client.get(f"{base}/messages", headers=headers).json()["items"]
    original = next(m for m in messages if m["id"] == otp_source["message_id"])
    assert original["text"] == f"{BANGLA_OTP} {BANGLISH_PDF}"
    assert pdf["sources"][0]["message_id"] == otp_source["message_id"]


def test_second_extract_reads_nothing_again(client, extracted, llm):
    headers, base, _ = extracted

    body = extract(client, headers, base).json()

    assert (body["messages_processed"], body["requirements_created"], body["llm_calls"]) == (0, 0, 0)
    assert len(llm.calls) == 1
    assert len(listed(client, headers, base)) == 5


def test_force_replaces_only_untouched_ai_suggestions(client, extracted, llm, db_session_factory):
    headers, base, _ = extracted
    reqs = by_code(client, headers, base)
    assert client.post(f"{base}/requirements/{reqs['REQ-001']['id']}/accept", headers=headers).status_code == 200
    assert client.post(f"{base}/requirements/{reqs['REQ-002']['id']}/reject", headers=headers).status_code == 200
    patch = {"statement": "The system shall load the home screen within 2 seconds."}
    assert client.patch(f"{base}/requirements/{reqs['REQ-003']['id']}", json=patch, headers=headers).status_code == 200

    # A requirement written by a user for the CSV sentence (origin="user" is never replaced)
    csv_message_id = reqs["REQ-004"]["sources"][0]["message_id"]
    db = db_session_factory()
    db.add(
        Requirement(
            project_id=db.get(Message, csv_message_id).project_id,
            number=99,
            code="REQ-099",
            origin="user",
            status="suggested",
            versions=[RequirementVersion(version=1, statement="The system shall export orders.", type="functional", actor="admin", origin="user")],
            sources=[RequirementSource(message_id=csv_message_id, source_sentence=CSV_SENTENCE)],
        )
    )
    db.commit()
    db.close()

    body = extract(client, headers, base, force=True).json()

    assert (body["messages_processed"], body["llm_calls"]) == (8, 1)
    # OTP, PDF and load are covered by kept requirements; of the two CSV items one is covered by REQ-099
    assert body["requirements_created"] == 1
    after = by_code(client, headers, base)
    assert sorted(after) == ["REQ-001", "REQ-002", "REQ-003", "REQ-006", "REQ-099"]  # 004/005 replaced, not reused
    assert (after["REQ-001"]["status"], after["REQ-002"]["status"], after["REQ-003"]["status"]) == (
        "accepted",
        "rejected",
        "edited",
    )
    assert after["REQ-003"]["current"]["statement"] == patch["statement"]
    assert after["REQ-099"]["origin"] == "user"
    assert after["REQ-006"]["sources"][0]["source_sentence"] == CSV_SENTENCE


def set_status(db_session_factory, requirement_id: int, status: str) -> None:
    db = db_session_factory()
    db.get(Requirement, requirement_id).status = status
    db.commit()
    db.close()


def test_force_keeps_suggested_item_with_a_user_version(client, extracted, llm, db_session_factory):
    headers, base, _ = extracted
    req = by_code(client, headers, base)["REQ-001"]
    text = "The system shall ask for an OTP on the login page."
    client.patch(f"{base}/requirements/{req['id']}", json={"statement": text}, headers=headers)
    set_status(db_session_factory, req["id"], "suggested")  # what restore and undo can do

    extract(client, headers, base, force=True)

    after = by_code(client, headers, base)["REQ-001"]
    assert (after["status"], after["current"]["statement"], after["version_count"]) == ("suggested", text, 2)


def test_force_keeps_the_target_of_a_merge(client, extracted, llm):
    headers, base, _ = extracted
    reqs = by_code(client, headers, base)
    target, other = reqs["REQ-004"]["id"], reqs["REQ-005"]["id"]
    assert client.post(f"{base}/requirements/{target}/merge", json={"other_ids": [other]}, headers=headers).status_code == 200

    extract(client, headers, base, force=True)

    after = by_code(client, headers, base)
    assert after["REQ-004"]["status"] == "suggested"
    assert (after["REQ-005"]["status"], after["REQ-005"]["merged_into_id"]) == ("merged", target)


def test_force_still_replaces_a_plain_ai_suggestion(client, extracted, llm, db_session_factory):
    headers, base, _ = extracted
    req = by_code(client, headers, base)["REQ-002"]
    client.post(f"{base}/requirements/{req['id']}/accept", headers=headers)
    set_status(db_session_factory, req["id"], "suggested")  # AI text only: nothing of a user's is lost

    extract(client, headers, base, force=True)

    assert "REQ-002" not in by_code(client, headers, base)


def test_extract_only_chosen_messages(client, extracted, llm):
    headers, base, _ = extracted
    otp_message = by_code(client, headers, base)["REQ-001"]["sources"][0]["message_id"]

    body = extract(client, headers, base, message_ids=[otp_message]).json()

    assert (body["messages_processed"], body["llm_calls"]) == (1, 1)
    assert llm.calls[-1]["batch"][0]["message_id"] == otp_message
    # Both suggestions of that message were replaced by new ones
    assert sorted(by_code(client, headers, base)) == ["REQ-003", "REQ-004", "REQ-005", "REQ-006", "REQ-007"]


def test_llm_not_configured_uses_rules(client, owner, monkeypatch):
    headers, base, _ = owner
    monkeypatch.setattr(settings, "llm_chain", "groq,gemini")
    monkeypatch.setattr(settings, "groq_api_key", None)
    monkeypatch.setattr(settings, "llm_model", None)
    monkeypatch.setattr(settings, "llm_api_key", None)

    res = extract(client, headers, base)

    assert res.status_code == 200
    body = res.json()
    assert (body["notice"], body["llm_calls"], body["messages_remaining"]) == (
        "AI unavailable — used rule-based extraction.",
        0,
        0,
    )
    assert "GROQ_API_KEY" in body["ai_error"]
    items = listed(client, headers, base)
    assert body["requirements_created"] == len(items) > 0
    assert {r["current"]["ai_model"] for r in items} == {"rule-based"}
    # Each one is the client's own sentence, Bangla and Banglish included
    assert all(r["current"]["statement"] == r["sources"][0]["source_sentence"] for r in items)
    assert {BANGLA_OTP, BANGLISH_PDF, CSV_SENTENCE} <= {r["current"]["statement"] for r in items}


def test_llm_error_falls_back_to_rules(client, owner):
    headers, base, _ = owner
    app.dependency_overrides[get_optional_llm_client] = lambda: FakeLLM(fail_on_call=1)

    body = extract(client, headers, base).json()

    assert (body["messages_processed"], body["messages_remaining"], body["rule_based_batches"]) == (8, 0, 1)
    assert body["notice"] == "AI unavailable — used rule-based extraction."
    assert "invalid answer" in body["ai_error"]
    items = listed(client, headers, base)
    assert body["requirements_created"] == len(items) > 0
    assert all((r["status"], r["current"]["ai_model"], r["current"]["confidence"]) == ("suggested", "rule-based", 0.4) for r in items)


# ---------- edit ----------


def test_edit_creates_new_version_and_keeps_old(client, extracted):
    headers, base, user = extracted
    req = by_code(client, headers, base)["REQ-001"]

    res = client.patch(
        f"{base}/requirements/{req['id']}",
        json={"statement": "The system shall send a 6-digit OTP by SMS at login.", "type": "functional"},
        headers=headers,
    )

    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["status"], body["version_count"]) == ("edited", 2)
    assert body["current"] | {"created_at": None} == {
        "version": 2,
        "statement": "The system shall send a 6-digit OTP by SMS at login.",
        "type": "functional",
        "nfr_category": None,
        "actor": "user",
        "confidence": None,
        "ai_model": None,
        "origin": "user",
        "created_by_id": user["id"],
        "created_at": None,
    }
    first = body["versions"][0]
    assert (first["version"], first["origin"], first["statement"]) == (1, "ai", req["current"]["statement"])
    assert (first["confidence"], first["ai_model"]) == (0.92, "fake-model")
    assert body["origin"] == "ai"  # the requirement itself stays AI-found
    assert body["sources"] == req["sources"]

    detail = client.get(f"{base}/requirements/{req['id']}", headers=headers).json()
    assert [v["version"] for v in detail["versions"]] == [1, 2]


def test_edit_to_non_functional_without_category_uses_other(client, extracted):
    headers, base, _ = extracted
    req = by_code(client, headers, base)["REQ-002"]

    body = client.patch(f"{base}/requirements/{req['id']}", json={"type": "non_functional"}, headers=headers).json()

    assert (body["current"]["type"], body["current"]["nfr_category"]) == ("non_functional", "other")
    assert body["current"]["statement"] == req["current"]["statement"]


def test_edit_validation(client, extracted):
    headers, base, _ = extracted
    rid = by_code(client, headers, base)["REQ-001"]["id"]
    url = f"{base}/requirements/{rid}"

    assert client.patch(url, json={}, headers=headers).status_code == 422
    assert client.patch(url, json={"statement": "   "}, headers=headers).status_code == 422
    assert client.patch(url, json={"nfr_category": "speed"}, headers=headers).status_code == 422
    assert client.patch(f"{base}/requirements/999999", json={"actor": "x"}, headers=headers).status_code == 404


def test_rejected_cannot_be_edited_but_can_be_accepted(client, extracted):
    headers, base, _ = extracted
    rid = by_code(client, headers, base)["REQ-001"]["id"]
    client.post(f"{base}/requirements/{rid}/reject", headers=headers)

    assert client.patch(f"{base}/requirements/{rid}", json={"actor": "x"}, headers=headers).status_code == 409
    assert client.post(f"{base}/requirements/{rid}/accept", headers=headers).json()["status"] == "accepted"


# ---------- accept / reject / merge / filters ----------


def test_accept_and_reject_change_status_only(client, extracted):
    headers, base, _ = extracted
    reqs = by_code(client, headers, base)

    accepted = client.post(f"{base}/requirements/{reqs['REQ-001']['id']}/accept", headers=headers).json()
    rejected = client.post(f"{base}/requirements/{reqs['REQ-002']['id']}/reject", headers=headers).json()

    assert (accepted["status"], accepted["version_count"]) == ("accepted", 1)
    assert rejected["status"] == "rejected"
    assert [r["code"] for r in listed(client, headers, base, status="accepted")] == ["REQ-001"]
    assert [r["code"] for r in listed(client, headers, base, type="non_functional")] == ["REQ-001", "REQ-003"]
    assert client.get(f"{base}/requirements", params={"status": "maybe"}, headers=headers).status_code == 422


def test_merge(client, extracted):
    headers, base, _ = extracted
    reqs = by_code(client, headers, base)
    target, a, b = reqs["REQ-004"]["id"], reqs["REQ-005"]["id"], reqs["REQ-001"]["id"]

    res = client.post(f"{base}/requirements/{target}/merge", json={"other_ids": [a, b, a]}, headers=headers)

    assert res.status_code == 200, res.text
    # REQ-005 has the same sentence as REQ-004 (kept once); REQ-001 adds the Bangla sentence
    assert [s["source_sentence"] for s in res.json()["sources"]] == [CSV_SENTENCE, BANGLA_OTP]
    after = by_code(client, headers, base)
    assert (after["REQ-005"]["status"], after["REQ-005"]["merged_into_id"]) == ("merged", target)
    assert (after["REQ-001"]["status"], after["REQ-001"]["merged_into_id"]) == ("merged", target)

    # A merged requirement is closed
    assert client.post(f"{base}/requirements/{a}/accept", headers=headers).status_code == 409
    assert client.patch(f"{base}/requirements/{a}", json={"actor": "x"}, headers=headers).status_code == 409
    assert client.post(f"{base}/requirements/{target}/merge", json={"other_ids": [a]}, headers=headers).status_code == 409


def test_merge_errors(client, extracted):
    headers, base, _ = extracted
    rid = by_code(client, headers, base)["REQ-001"]["id"]
    url = f"{base}/requirements/{rid}/merge"

    assert client.post(url, json={"other_ids": [rid]}, headers=headers).status_code == 400
    assert client.post(url, json={"other_ids": []}, headers=headers).status_code == 422
    assert client.post(url, json={"other_ids": [999999]}, headers=headers).status_code == 404


# ---------- reject reason ----------


def act(client, headers, base, rid, action, **body):
    return client.post(f"{base}/requirements/{rid}/{action}", json=body or None, headers=headers)


def test_reject_reason_is_saved_and_optional(client, extracted):
    headers, base, _ = extracted
    reqs = by_code(client, headers, base)

    with_reason = act(client, headers, base, reqs["REQ-001"]["id"], "reject", reason="  Out of scope for phase 1. ")
    without = act(client, headers, base, reqs["REQ-002"]["id"], "reject")
    blank = act(client, headers, base, reqs["REQ-003"]["id"], "reject", reason="   ")

    assert with_reason.json()["reject_reason"] == "Out of scope for phase 1."
    assert without.status_code == 200 and without.json()["reject_reason"] is None
    assert blank.json()["reject_reason"] is None
    assert by_code(client, headers, base)["REQ-001"]["reject_reason"] == "Out of scope for phase 1."


def test_reject_reason_too_long(client, extracted):
    headers, base, _ = extracted
    rid = by_code(client, headers, base)["REQ-001"]["id"]

    assert act(client, headers, base, rid, "reject", reason="x" * 1001).status_code == 422
    assert by_code(client, headers, base)["REQ-001"]["status"] == "suggested"


def test_reject_reason_is_shown_only_while_rejected(client, extracted):
    headers, base, _ = extracted
    rid = by_code(client, headers, base)["REQ-001"]["id"]
    act(client, headers, base, rid, "reject", reason="Not needed")

    assert act(client, headers, base, rid, "accept").json()["reject_reason"] is None
    assert act(client, headers, base, rid, "undo").json()["reject_reason"] == "Not needed"
    assert act(client, headers, base, rid, "restore").json()["reject_reason"] is None


# ---------- undo ----------


def test_undo_accept_returns_to_suggested(client, extracted):
    headers, base, _ = extracted
    rid = by_code(client, headers, base)["REQ-001"]["id"]
    act(client, headers, base, rid, "accept")

    res = act(client, headers, base, rid, "undo")

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "suggested"


def test_undo_returns_an_edited_requirement_to_edited(client, extracted):
    headers, base, _ = extracted
    rid = by_code(client, headers, base)["REQ-001"]["id"]
    client.patch(f"{base}/requirements/{rid}", json={"actor": "client"}, headers=headers)
    act(client, headers, base, rid, "accept")

    body = act(client, headers, base, rid, "undo").json()

    assert (body["status"], body["version_count"]) == ("edited", 2)


def test_undo_reject_returns_to_accepted(client, extracted):
    headers, base, _ = extracted
    rid = by_code(client, headers, base)["REQ-001"]["id"]
    act(client, headers, base, rid, "accept")
    act(client, headers, base, rid, "reject", reason="Changed our mind")

    assert act(client, headers, base, rid, "undo").json()["status"] == "accepted"


def test_undo_twice_or_without_a_review_is_refused(client, extracted):
    headers, base, _ = extracted
    rid = by_code(client, headers, base)["REQ-001"]["id"]

    assert act(client, headers, base, rid, "undo").status_code == 409
    act(client, headers, base, rid, "accept")
    act(client, headers, base, rid, "undo")
    res = act(client, headers, base, rid, "undo")
    assert res.status_code == 409 and "nothing to undo" in res.json()["detail"]


def test_undo_cannot_jump_over_a_later_change(client, extracted):
    headers, base, _ = extracted
    reqs = by_code(client, headers, base)
    edited, target, merged = reqs["REQ-001"]["id"], reqs["REQ-002"]["id"], reqs["REQ-003"]["id"]
    act(client, headers, base, edited, "accept")
    client.patch(f"{base}/requirements/{edited}", json={"actor": "client"}, headers=headers)
    act(client, headers, base, target, "accept")
    act(client, headers, base, merged, "accept")
    act(client, headers, base, target, "merge", other_ids=[merged])

    assert act(client, headers, base, edited, "undo").status_code == 409
    assert act(client, headers, base, target, "undo").status_code == 409
    assert act(client, headers, base, merged, "undo").status_code == 409


def test_repeating_a_review_keeps_the_real_previous_status(client, extracted):
    headers, base, _ = extracted
    rid = by_code(client, headers, base)["REQ-001"]["id"]
    act(client, headers, base, rid, "accept")
    assert act(client, headers, base, rid, "accept").status_code == 200

    assert act(client, headers, base, rid, "undo").json()["status"] == "suggested"


# ---------- restore ----------


def test_restore_rejected(client, extracted):
    headers, base, _ = extracted
    rid = by_code(client, headers, base)["REQ-001"]["id"]
    act(client, headers, base, rid, "reject", reason="No")

    res = act(client, headers, base, rid, "restore")

    assert res.status_code == 200, res.text
    assert (res.json()["status"], res.json()["reject_reason"]) == ("suggested", None)
    assert act(client, headers, base, rid, "undo").status_code == 409  # the reject is gone, not undoable


def test_restore_is_only_for_rejected_or_merged(client, extracted):
    headers, base, _ = extracted
    rid = by_code(client, headers, base)["REQ-001"]["id"]

    assert act(client, headers, base, rid, "restore").status_code == 409
    act(client, headers, base, rid, "accept")
    assert act(client, headers, base, rid, "restore").status_code == 409
    assert client.post(f"{base}/requirements/999999/restore", headers=headers).status_code == 404


def test_restore_merged_takes_its_sentence_back_from_the_target(client, extracted):
    headers, base, _ = extracted
    reqs = by_code(client, headers, base)
    target, other = reqs["REQ-004"]["id"], reqs["REQ-001"]["id"]
    act(client, headers, base, target, "merge", other_ids=[other])

    res = act(client, headers, base, other, "restore")

    assert res.status_code == 200, res.text
    assert (res.json()["status"], res.json()["merged_into_id"]) == ("suggested", None)
    assert [s["source_sentence"] for s in res.json()["sources"]] == [BANGLA_OTP]
    after = client.get(f"{base}/requirements/{target}", headers=headers).json()
    assert [s["source_sentence"] for s in after["sources"]] == [CSV_SENTENCE]  # its own sentence is untouched


def test_restore_merged_keeps_the_targets_own_copy_of_the_same_sentence(client, extracted):
    headers, base, _ = extracted
    reqs = by_code(client, headers, base)
    target, other = reqs["REQ-004"]["id"], reqs["REQ-005"]["id"]  # both from the CSV sentence
    act(client, headers, base, target, "merge", other_ids=[other])

    act(client, headers, base, other, "restore")

    after = client.get(f"{base}/requirements/{target}", headers=headers).json()
    assert [s["source_sentence"] for s in after["sources"]] == [CSV_SENTENCE]


def test_restore_keeps_a_sentence_another_merged_item_still_needs(client, extracted):
    headers, base, _ = extracted
    reqs = by_code(client, headers, base)
    target, first, second = reqs["REQ-002"]["id"], reqs["REQ-004"]["id"], reqs["REQ-005"]["id"]
    act(client, headers, base, target, "merge", other_ids=[first, second])  # same CSV sentence, copied once

    def sentences() -> list[str]:
        detail = client.get(f"{base}/requirements/{target}", headers=headers).json()
        return [s["source_sentence"] for s in detail["sources"]]

    assert sentences() == [BANGLISH_PDF, CSV_SENTENCE]
    act(client, headers, base, first, "restore")
    assert sentences() == [BANGLISH_PDF, CSV_SENTENCE]  # REQ-005 is still merged here
    act(client, headers, base, second, "restore")
    assert sentences() == [BANGLISH_PDF]


def test_restored_edit_survives_force_extract(client, extracted, llm):
    headers, base, _ = extracted
    rid = by_code(client, headers, base)["REQ-001"]["id"]
    text = "The system shall ask for an OTP on the login page."
    client.patch(f"{base}/requirements/{rid}", json={"statement": text}, headers=headers)
    act(client, headers, base, rid, "reject")
    act(client, headers, base, rid, "restore")

    extract(client, headers, base, force=True)

    after = by_code(client, headers, base)["REQ-001"]
    assert (after["status"], after["current"]["statement"]) == ("suggested", text)


# ---------- bulk ----------


def bulk(client, headers, base, ids, action, **extra):
    return client.post(f"{base}/requirements/bulk", json={"ids": ids, "action": action, **extra}, headers=headers)


def test_bulk_accept(client, extracted):
    headers, base, _ = extracted
    reqs = by_code(client, headers, base)
    ids = [reqs["REQ-001"]["id"], reqs["REQ-002"]["id"], reqs["REQ-001"]["id"]]  # a repeated id counts once

    res = bulk(client, headers, base, ids, "accept")

    assert res.status_code == 200, res.text
    assert [r["code"] for r in res.json()["updated"]] == ["REQ-001", "REQ-002"]
    assert res.json()["skipped"] == []
    assert [r["code"] for r in listed(client, headers, base, status="accepted")] == ["REQ-001", "REQ-002"]


def test_bulk_reject_uses_one_reason(client, extracted):
    headers, base, _ = extracted
    reqs = by_code(client, headers, base)
    ids = [reqs["REQ-001"]["id"], reqs["REQ-002"]["id"]]

    body = bulk(client, headers, base, ids, "reject", reason="Phase 2").json()

    assert [(r["status"], r["reject_reason"]) for r in body["updated"]] == [("rejected", "Phase 2")] * 2


def test_bulk_undo_returns_each_to_its_own_status(client, extracted):
    headers, base, _ = extracted
    reqs = by_code(client, headers, base)
    plain, edited = reqs["REQ-001"]["id"], reqs["REQ-002"]["id"]
    client.patch(f"{base}/requirements/{edited}", json={"actor": "client"}, headers=headers)
    bulk(client, headers, base, [plain, edited], "accept")

    body = bulk(client, headers, base, [plain, edited], "undo").json()

    assert [r["status"] for r in body["updated"]] == ["suggested", "edited"]


def test_bulk_skips_and_reports_what_it_cannot_change(client, extracted):
    headers, base, _ = extracted
    reqs = by_code(client, headers, base)
    fresh, done, merged = reqs["REQ-001"]["id"], reqs["REQ-002"]["id"], reqs["REQ-005"]["id"]
    act(client, headers, base, done, "accept")
    act(client, headers, base, reqs["REQ-004"]["id"], "merge", other_ids=[merged])

    body = bulk(client, headers, base, [fresh, done, merged], "accept").json()

    assert [r["code"] for r in body["updated"]] == ["REQ-001"]
    assert [(s["code"], s["reason"]) for s in body["skipped"]] == [
        ("REQ-002", "Already accepted"),
        ("REQ-005", "REQ-005 was merged into another requirement"),
    ]

    undone = bulk(client, headers, base, [reqs["REQ-003"]["id"]], "undo").json()
    assert undone["updated"] == [] and undone["skipped"][0]["reason"] == "REQ-003 has nothing to undo"


def test_bulk_with_an_unknown_id_changes_nothing(client, extracted):
    headers, base, _ = extracted
    rid = by_code(client, headers, base)["REQ-001"]["id"]
    other = client.post("/projects", json={"name": "Other"}, headers=headers).json()

    assert bulk(client, headers, base, [rid, 999999], "accept").status_code == 404
    assert bulk(client, headers, f"/projects/{other['id']}", [rid], "accept").status_code == 404
    assert by_code(client, headers, base)["REQ-001"]["status"] == "suggested"


def test_bulk_validation(client, extracted):
    headers, base, _ = extracted
    rid = by_code(client, headers, base)["REQ-001"]["id"]

    assert bulk(client, headers, base, [], "accept").status_code == 422
    assert bulk(client, headers, base, list(range(1, 202)), "accept").status_code == 422
    assert bulk(client, headers, base, [rid], "merge").status_code == 422
    assert bulk(client, headers, base, [rid], "reject", reason="x" * 1001).status_code == 422


# ---------- data and access ----------


def test_deleting_a_message_removes_its_source_links(client, extracted, db_session_factory):
    headers, base, _ = extracted
    req = by_code(client, headers, base)["REQ-004"]

    assert client.delete(f"{base}/messages/{req['sources'][0]['message_id']}", headers=headers).status_code == 204

    db = db_session_factory()
    assert db.query(RequirementSource).filter_by(requirement_id=req["id"]).count() == 0
    db.close()


def test_deleting_project_deletes_requirements(client, extracted, db_session_factory):
    headers, base, _ = extracted
    assert client.delete(base, headers=headers).status_code == 204

    db = db_session_factory()
    assert (db.query(Requirement).count(), db.query(RequirementVersion).count(), db.query(RequirementSource).count()) == (0, 0, 0)
    db.close()


def test_requirements_are_per_project_and_owner_only(client, extracted, make_user):
    headers, base, _ = extracted
    rid = listed(client, headers, base)[0]["id"]
    other = client.post("/projects", json={"name": "Other"}, headers=headers).json()
    _, stranger = make_user()

    assert listed(client, headers, f"/projects/{other['id']}") == []
    assert client.get(f"/projects/{other['id']}/requirements/{rid}", headers=headers).status_code == 404
    assert client.get(f"{base}/requirements", headers=stranger).status_code == 404
    assert extract(client, stranger, base).status_code == 404
    assert client.patch(f"{base}/requirements/{rid}", json={"actor": "x"}, headers=stranger).status_code == 404
    assert client.post(f"{base}/requirements/{rid}/accept", headers=stranger).status_code == 404
    for action in ("reject", "undo", "restore"):
        assert client.post(f"{base}/requirements/{rid}/{action}", headers=stranger).status_code == 404
    # The owner cannot reach it through another of their projects either
    assert client.post(f"/projects/{other['id']}/requirements/{rid}/restore", headers=headers).status_code == 404
    assert bulk(client, stranger, base, [rid], "accept").status_code == 404
    assert listed(client, headers, base)[0]["status"] == "suggested"


def test_login_required(client, extracted):
    _, base, _ = extracted
    assert client.get(f"{base}/requirements").status_code == 401
    assert client.post(f"{base}/requirements/extract").status_code == 401
    assert client.post(f"{base}/requirements/1/undo").status_code == 401
    assert client.post(f"{base}/requirements/1/restore").status_code == 401
    assert client.post(f"{base}/requirements/bulk", json={"ids": [1], "action": "accept"}).status_code == 401
