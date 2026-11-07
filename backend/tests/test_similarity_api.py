"""Pair checks through the API: after extraction, on edit, accept / reject, merge and the Validate button."""

import pytest

from app.api.deps import get_optional_llm_client
from app.main import app
from app.models import RequirementEmbedding
from tests.fake_llm import FakeLLM
from tests.fake_models import BASE, vector_at

# Rule-based extraction keeps each client sentence as the statement. With the bag-of-words fake embedder the
# first two are 0.87 alike (a possible duplicate); the third is unrelated.
CHAT = (
    "20/10/26, 10:05 - Karim: Order list lagbe.\n"
    "20/10/26, 10:06 - Karim: Order list lagbe please.\n"
    "21/10/26, 09:00 - Karim: CSV export lagbe.\n"
)
OLD = "The shop shall deliver orders in 3 days."
NEW = "The shop shall deliver orders in 7 days."


@pytest.fixture
def owner(client, make_user):
    _, headers = make_user()
    project = client.post("/projects", json={"name": "Karim Traders"}, headers=headers).json()
    return headers, f"/projects/{project['id']}"


def extract(client, owner):
    headers, base = owner
    app.dependency_overrides[get_optional_llm_client] = lambda: None
    res = client.post(
        f"{base}/uploads", files=[("files", ("WhatsApp Chat with Karim.txt", CHAT.encode(), "text/plain"))], headers=headers
    )
    assert res.status_code == 200, res.text
    res = client.post(f"{base}/requirements/extract", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def listed(client, owner) -> dict[str, dict]:
    headers, base = owner
    return {r["code"]: r for r in client.get(f"{base}/requirements", headers=headers).json()}


def detail(client, owner, rid: int) -> dict:
    headers, base = owner
    return client.get(f"{base}/requirements/{rid}", headers=headers).json()


def pair_findings(client, owner, rid: int) -> list[tuple[str, str | None, str]]:
    return [
        (f["category"], f["related_code"], f["status"])
        for f in detail(client, owner, rid)["findings"]
        if f["category"] in ("duplicate", "conflict", "change_request")
    ]


def post(client, owner, path: str, body=None):
    headers, base = owner
    res = client.post(f"{base}/requirements{path}", json=body, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def edit(client, owner, rid: int, statement: str) -> dict:
    headers, base = owner
    res = client.patch(f"{base}/requirements/{rid}", json={"statement": statement}, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def test_extraction_finds_a_possible_duplicate(client, owner):
    result = extract(client, owner)
    assert result["requirements_created"] == 3
    assert result["similarity_unavailable_reason"] is None
    items = listed(client, owner)
    assert pair_findings(client, owner, items["REQ-001"]["id"]) == []
    assert pair_findings(client, owner, items["REQ-002"]["id"]) == [("duplicate", "REQ-001", "suggested")]
    finding = detail(client, owner, items["REQ-002"]["id"])["findings"][-1]
    assert finding["related_requirement_id"] == items["REQ-001"]["id"]
    assert finding["related_statement"] == finding["evidence"] == items["REQ-001"]["current"]["statement"]
    assert 0.85 <= finding["score"] < 1
    assert finding["reason"].startswith("Possible duplicate of REQ-001")
    assert items["REQ-002"]["has_issues"]


def test_unavailable_models_still_extract_and_validate(client, owner, fake_models):
    fake_models.unavailable = "The embedding model could not be loaded: no network"
    result = extract(client, owner)
    assert result["requirements_created"] == 3
    assert result["similarity_unavailable_reason"] == "The embedding model could not be loaded: no network"
    validated = post(client, owner, "/validate")
    assert validated["validated"] == 3
    assert validated["similarity_unavailable_reason"] == "The embedding model could not be loaded: no network"
    assert all(r["quality_score"] is not None for r in listed(client, owner).values())


def test_merge_from_a_duplicate_finding(client, owner):
    extract(client, owner)
    items = listed(client, owner)
    old, new = items["REQ-001"]["id"], items["REQ-002"]["id"]
    merged = post(client, owner, f"/{old}/merge", {"other_ids": [new]})
    assert len(merged["sources"]) == 2
    assert pair_findings(client, owner, new) == []  # merged: no longer compared
    assert listed(client, owner)["REQ-002"]["status"] == "merged"


def test_rejecting_the_older_one_clears_the_finding(client, owner):
    extract(client, owner)
    items = listed(client, owner)
    post(client, owner, f"/{items['REQ-001']['id']}/reject")
    assert pair_findings(client, owner, items["REQ-002"]["id"]) == []
    post(client, owner, f"/{items['REQ-001']['id']}/undo")
    assert pair_findings(client, owner, items["REQ-002"]["id"]) == [("duplicate", "REQ-001", "suggested")]


def test_an_edit_is_checked_again(client, owner):
    extract(client, owner)
    items = listed(client, owner)
    edit(client, owner, items["REQ-002"]["id"], "The admin shall see a monthly report.")
    assert pair_findings(client, owner, items["REQ-002"]["id"]) == []
    edit(client, owner, items["REQ-003"]["id"], "Order list lagbe.")
    assert pair_findings(client, owner, items["REQ-003"]["id"]) == [("duplicate", "REQ-001", "suggested")]


def test_accepting_the_older_one_makes_a_change_request(client, owner, fake_models):
    fake_models.embedder.vectors.update({OLD: BASE, NEW: vector_at(0.8)})
    fake_models.nli.contradict(NEW, OLD, 0.9)
    extract(client, owner)
    items = listed(client, owner)
    old, new = items["REQ-001"]["id"], items["REQ-003"]["id"]
    edit(client, owner, old, OLD)
    edit(client, owner, new, NEW)
    assert pair_findings(client, owner, new) == [("conflict", "REQ-001", "suggested")]
    assert listed(client, owner)["REQ-003"]["change_request_for"] is None

    post(client, owner, f"/{old}/accept")
    assert pair_findings(client, owner, new) == [("change_request", "REQ-001", "suggested")]
    assert listed(client, owner)["REQ-003"]["change_request_for"] == {"id": old, "code": "REQ-001"}

    post(client, owner, "/bulk", {"ids": [new], "action": "accept"})  # accepting the change keeps the record
    assert listed(client, owner)["REQ-003"]["change_request_for"] == {"id": old, "code": "REQ-001"}

    post(client, owner, f"/{new}/reject")
    assert listed(client, owner)["REQ-003"]["change_request_for"] is None


def test_validate_button_checks_every_pair(client, owner, fake_models, db_session_factory):
    extract(client, owner)
    items = listed(client, owner)
    statements = [items[c]["current"]["statement"] for c in ("REQ-001", "REQ-003")]
    fake_models.nli.contradict(*statements, 0.95)
    fake_models.embedder.vectors.update({statements[0]: BASE, statements[1]: vector_at(0.6)})
    with db_session_factory() as db:  # embeddings were stored at extraction: make them again with the new vectors
        db.query(RequirementEmbedding).delete()
        db.commit()

    result = post(client, owner, "/validate")
    assert result["similarity_unavailable_reason"] is None
    assert ("conflict", "REQ-001", "suggested") in pair_findings(client, owner, items["REQ-003"]["id"])


def test_the_llm_check_is_in_the_response(client, owner):
    extract(client, owner)
    items = listed(client, owner)
    old, new = (items[c]["current"]["statement"] for c in ("REQ-001", "REQ-002"))
    finding = detail(client, owner, items["REQ-002"]["id"])["findings"][-1]
    assert (finding["confirmation"], finding["explanation"]) == ("unconfirmed", None)  # extracted without an LLM

    llm = FakeLLM()
    llm.relate(old, new, "duplicate")
    app.dependency_overrides[get_optional_llm_client] = lambda: llm
    post(client, owner, "/validate")
    finding = detail(client, owner, items["REQ-002"]["id"])["findings"][-1]
    assert (finding["category"], finding["confirmation"], finding["explanation"]) == (
        "duplicate",
        "confirmed",
        "Fake reason: duplicate.",
    )

    llm.relations.clear()
    llm.default_relation = "unrelated"  # a new answer is only asked for new versions: this one is kept
    post(client, owner, "/validate")
    assert pair_findings(client, owner, items["REQ-002"]["id"]) == [("duplicate", "REQ-001", "suggested")]
