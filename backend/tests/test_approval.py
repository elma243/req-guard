"""Approval: separate from "accepted", only for accepted or edited requirements, blocked by open High findings
unless an override reason is given (and recorded), and ended by any new version."""

import pytest
from sqlalchemy import select

from app.models import RequirementApproval, ValidationFinding
from tests.clarify_helpers import CLEAN, add_conflict, make_project, seed_requirement, seed_slow_report, use_llm


@pytest.fixture
def project(client, make_user):
    return make_project(client, make_user)


def post(client, project, path, body=None):
    headers, base, _ = project
    return client.post(f"{base}{path}", json=body, headers=headers)


def approve(client, project, rid, reason=None):
    return post(client, project, f"/requirements/{rid}/approve", {"override_reason": reason} if reason is not None else None)


def test_approve_a_requirement_without_high_findings(client, project, db_session_factory):
    headers, base, pid = project
    rid = seed_requirement(db_session_factory, pid, CLEAN)
    res = approve(client, project, rid)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "accepted"  # approval is separate from the review status
    approval = body["approval"]
    assert approval["version"] == 1 and approval["approved_by_name"] and approval["override_reason"] is None
    assert client.get(f"{base}/requirements", headers=headers).json()[0]["approval"]["id"] == approval["id"]
    assert approve(client, project, rid).status_code == 409  # already approved


def test_open_high_findings_block_approval(client, project, db_session_factory):
    _, _, pid = project
    older = seed_requirement(db_session_factory, pid, CLEAN)
    rid = seed_requirement(db_session_factory, pid, "The admin shall not export the order list.")
    add_conflict(db_session_factory, rid, older)
    res = approve(client, project, rid)
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert "1 open High finding" in detail and "possible conflict with REQ-001" in detail
    assert approve(client, project, rid, "   ").status_code == 409  # a blank reason is no reason


def test_override_reason_is_recorded_with_the_findings(client, project, db_session_factory):
    _, _, pid = project
    rid = seed_slow_report(db_session_factory, pid)  # no measurable value: high
    with db_session_factory() as db:
        high = [f.id for f in db.scalars(select(ValidationFinding).where(ValidationFinding.severity == "high"))]
    assert high
    res = approve(client, project, rid, "Client confirmed on call: 2 seconds is fine; text follows.")
    assert res.status_code == 200, res.text
    approval = res.json()["approval"]
    assert approval["override_reason"] == "Client confirmed on call: 2 seconds is fine; text follows."
    assert approval["open_high_finding_ids"] == high


def test_a_rejected_high_finding_no_longer_blocks(client, project, db_session_factory):
    _, _, pid = project
    older = seed_requirement(db_session_factory, pid, CLEAN)
    rid = seed_requirement(db_session_factory, pid, "The admin shall not export the order list.")
    finding_id = add_conflict(db_session_factory, rid, older)
    with db_session_factory() as db:
        db.get(ValidationFinding, finding_id).status = "rejected"
        db.commit()
    res = approve(client, project, rid)
    assert res.status_code == 200 and res.json()["approval"]["override_reason"] is None


@pytest.mark.parametrize("review_status", ["suggested", "rejected", "merged"])
def test_only_accepted_or_edited_requirements_can_be_approved(client, project, db_session_factory, review_status):
    _, _, pid = project
    rid = seed_requirement(db_session_factory, pid, CLEAN, status=review_status)
    res = approve(client, project, rid)
    assert res.status_code == 409 and "Only accepted or edited" in res.json()["detail"]


def test_unapprove_keeps_the_record(client, project, db_session_factory):
    _, _, pid = project
    rid = seed_requirement(db_session_factory, pid, CLEAN)
    approve(client, project, rid)
    res = post(client, project, f"/requirements/{rid}/unapprove")
    assert res.status_code == 200 and res.json()["approval"] is None
    assert post(client, project, f"/requirements/{rid}/unapprove").status_code == 409
    with db_session_factory() as db:
        record = db.scalar(select(RequirementApproval))
        assert record.revoked_at is not None and record.revoked_reason == "unapproved"
    assert approve(client, project, rid).status_code == 200  # can be approved again


def test_a_new_version_ends_the_approval(client, project, db_session_factory):
    headers, base, pid = project
    rid = seed_requirement(db_session_factory, pid, CLEAN)
    approve(client, project, rid)
    res = client.patch(f"{base}/requirements/{rid}", json={"statement": "The admin shall export orders as CSV."}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["approval"] is None and res.json()["status"] == "edited"
    with db_session_factory() as db:
        assert db.scalar(select(RequirementApproval)).revoked_reason == "new version"
    assert approve(client, project, rid).json()["approval"]["version"] == 2


def test_an_approved_requirement_is_locked(client, project, db_session_factory):
    headers, base, pid = project
    rid = seed_requirement(db_session_factory, pid, CLEAN)
    other = seed_requirement(db_session_factory, pid, "The admin shall export the order list to CSV.")
    approve(client, project, rid)
    for path, body in (
        (f"/requirements/{rid}/reject", None),
        (f"/requirements/{rid}/undo", None),
        (f"/requirements/{rid}/merge", {"other_ids": [other]}),
        (f"/requirements/{other}/merge", {"other_ids": [rid]}),
    ):
        res = post(client, project, path, body)
        assert res.status_code == 409 and "Remove the approval first" in res.json()["detail"], path
    bulk = post(client, project, "/requirements/bulk", {"ids": [rid, other], "action": "reject"}).json()
    assert [s["id"] for s in bulk["skipped"]] == [rid] and [u["id"] for u in bulk["updated"]] == [other]
    # No new questions for an approved requirement
    use_llm()
    assert post(client, project, f"/requirements/{rid}/questions/generate").status_code == 409


def test_other_owners_cannot_approve(client, project, make_user, db_session_factory):
    _, _, pid = project
    rid = seed_requirement(db_session_factory, pid, CLEAN)
    _, stranger = make_user()
    assert client.post(f"/projects/{pid}/requirements/{rid}/approve", headers=stranger).status_code == 404
