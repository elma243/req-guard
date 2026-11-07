"""Storing pair findings and embeddings: batching, re-runs, rule 2, closed requirements, unavailable models."""

import pytest
from sqlalchemy import select

from app.models import Project, Requirement, RequirementEmbedding, RequirementVersion, User
from app.nlp.pair_checks import check_similarity
from app.nlp.validate import validate_project, validate_requirement
from tests.fake_models import BASE, vector_at

A = "The customer shall pay by card."
B = "The buyer shall pay with a card."
C = "The admin shall see a monthly sales report."


@pytest.fixture
def db(db_session_factory):
    with db_session_factory() as session:
        yield session


@pytest.fixture
def project(db):
    owner = User(name="Owner", email="owner@example.com", password_hash="x")
    db.add(owner)
    db.flush()
    project = Project(name="Shop", owner_id=owner.id, description="", scope_text="")
    db.add(project)
    db.commit()
    return project


def add(db, project: Project, statement: str, number: int, status: str = "suggested") -> Requirement:
    requirement = Requirement(
        project_id=project.id,
        number=number,
        code=f"REQ-{number:03d}",
        origin="ai",
        status=status,
        versions=[RequirementVersion(version=1, statement=statement, type="functional", actor="", origin="ai")],
    )
    db.add(requirement)
    db.commit()
    return requirement


def pair_findings(requirement: Requirement) -> list[tuple[str, str, str]]:
    """(rule, related code, status) of the requirement's pair findings."""
    return [
        (f.rule, f.related_code, f.status)
        for f in requirement.findings
        if f.category in ("duplicate", "conflict", "change_request")
    ]


@pytest.fixture
def similar(fake_models):
    """A and B are close (0.9), C is unrelated to both."""
    fake_models.embedder.vectors.update({A: BASE, B: vector_at(0.9), C: vector_at(0.0, axis=2)})
    return fake_models


def run(db, project, changed=None) -> str | None:
    reason = check_similarity(db, project, changed)
    db.commit()
    return reason


def test_duplicate_found_on_the_newer_one(db, project, similar):
    old, new, other = add(db, project, A, 1), add(db, project, B, 2), add(db, project, C, 3)
    assert run(db, project) is None
    assert pair_findings(old) == [] and pair_findings(other) == []
    assert pair_findings(new) == [("possible_duplicate", "REQ-001", "suggested")]
    finding = new.findings[0]
    assert (finding.evidence, finding.related_statement, finding.score) == (A, A, 0.9)
    assert (finding.origin, finding.version, finding.severity) == ("ai", 1, "medium")


def test_embeddings_are_made_in_one_batch_once_per_version(db, project, similar):
    add(db, project, A, 1), add(db, project, B, 2), add(db, project, C, 3)
    run(db, project)
    run(db, project)
    assert similar.embedder.calls == [[A, B, C]]
    stored = db.scalars(select(RequirementEmbedding)).all()
    assert len(stored) == 3
    assert {e.model for e in stored} == {"fake-embedder"}
    assert all(len(e.embedding) == 384 for e in stored)  # a JSON list on SQLite


def test_a_new_version_gets_its_own_embedding(db, project, similar):
    first, second = add(db, project, A, 1), add(db, project, C, 2)
    run(db, project)
    second.versions.append(RequirementVersion(version=2, statement=B, type="functional", actor="", origin="user"))
    db.commit()
    run(db, project, [second.id])
    assert similar.embedder.calls == [[A, C], [B]]
    assert pair_findings(second) == [("possible_duplicate", "REQ-001", "suggested")]
    assert second.findings[0].version == 2


def test_conflict_from_nli_in_either_direction(db, project, fake_models):
    fake_models.embedder.vectors.update({A: BASE, C: vector_at(0.55)})
    fake_models.nli.contradict(C, A, 0.8)
    old, new = add(db, project, A, 1), add(db, project, C, 2)
    run(db, project)
    assert pair_findings(new) == [("possible_conflict", "REQ-001", "suggested")]
    assert new.findings[0].score == 0.8
    assert fake_models.nli.calls == [[(A, C), (C, A)]]


def test_nli_is_skipped_for_pairs_far_apart(db, project, fake_models):
    fake_models.embedder.vectors.update({A: BASE, C: vector_at(0.49)})
    add(db, project, A, 1), add(db, project, C, 2)
    run(db, project)
    assert fake_models.nli.calls == []


def test_accepting_the_older_one_turns_the_finding_into_a_change_request(db, project, fake_models):
    fake_models.embedder.vectors.update({A: BASE, C: vector_at(0.8)})
    fake_models.nli.contradict(A, C, 0.9)
    old, new = add(db, project, A, 1), add(db, project, C, 2)
    run(db, project)
    assert pair_findings(new) == [("possible_conflict", "REQ-001", "suggested")]
    old.status = "accepted"
    db.commit()
    run(db, project, [old.id])
    assert pair_findings(new) == [("change_request", "REQ-001", "suggested")]


def test_only_pairs_with_a_changed_requirement_are_checked(db, project, similar):
    add(db, project, A, 1), add(db, project, B, 2)
    third = add(db, project, C, 3)
    run(db, project, [third.id])
    assert similar.nli.calls == []  # A-B (0.9) was not looked at
    assert db.scalars(select(Requirement).where(Requirement.number == 2)).one().findings == []


def test_a_finding_no_longer_found_is_removed(db, project, similar):
    old, new = add(db, project, A, 1), add(db, project, B, 2)
    run(db, project)
    similar.embedder.vectors[B] = vector_at(0.2)  # as if the model now saw them apart
    db.query(RequirementEmbedding).delete()
    db.commit()
    run(db, project)
    assert pair_findings(new) == []


@pytest.mark.parametrize("decision", ["rejected", "accepted"])
def test_a_decided_finding_is_kept_and_not_added_again(db, project, similar, decision):
    old, new = add(db, project, A, 1), add(db, project, B, 2)
    run(db, project)
    new.findings[0].status = decision
    db.commit()
    similar.embedder.vectors[B] = vector_at(0.2)
    db.query(RequirementEmbedding).delete()
    db.commit()
    run(db, project)  # no longer found: kept anyway
    similar.embedder.vectors[B] = vector_at(0.9)
    db.query(RequirementEmbedding).delete()
    db.commit()
    run(db, project)  # found again: not added twice
    assert pair_findings(new) == [("possible_duplicate", "REQ-001", decision)]


@pytest.mark.parametrize("status", ["rejected", "merged"])
def test_closed_requirements_are_not_compared(db, project, similar, status):
    old, new = add(db, project, A, 1), add(db, project, B, 2)
    run(db, project)
    assert pair_findings(new)
    old.status = status
    db.commit()
    run(db, project, [old.id])
    assert pair_findings(new) == []
    add(db, project, A, 3)  # a new copy pairs with the open one only
    run(db, project)
    assert [f[1] for f in pair_findings(db.scalars(select(Requirement).where(Requirement.number == 3)).one())] == [
        "REQ-002"
    ]


def test_closed_requirements_are_cleaned_up_without_the_models(db, project, similar):
    old, new = add(db, project, A, 1), add(db, project, B, 2)
    run(db, project)
    similar.unavailable = "The embedding model could not be loaded: no network"
    new.status = "rejected"
    db.commit()
    assert run(db, project, [new.id]) is None  # nothing open to compare, so the models were not needed
    assert pair_findings(new) == []


def test_unavailable_models_give_the_reason_and_keep_the_rule_findings(db, project, similar):
    add(db, project, "The customer shall be fast.", 1), add(db, project, B, 2)
    similar.unavailable = "Similarity checks are switched off (SIMILARITY_ENABLED=false)."
    result = validate_project(db, project)
    db.commit()
    assert result.similarity_unavailable_reason == "Similarity checks are switched off (SIMILARITY_ENABLED=false)."
    first = db.scalars(select(Requirement).where(Requirement.number == 1)).one()
    assert "vague" in {f.category for f in first.findings}


def test_validate_project_runs_the_pair_checks_and_counts_them(db, project, similar):
    add(db, project, A, 1), add(db, project, B, 2)
    result = validate_project(db, project)
    db.commit()
    assert result.similarity_unavailable_reason is None
    new = db.scalars(select(Requirement).where(Requirement.number == 2)).one()
    assert ("possible_duplicate", "REQ-001", "suggested") in pair_findings(new)
    assert new.has_issues


def test_validate_project_only_new_pairs_new_with_all(db, project, similar):
    add(db, project, A, 1)
    validate_project(db, project)
    db.commit()
    add(db, project, B, 2)
    validate_project(db, project, only_new=True)
    db.commit()
    new = db.scalars(select(Requirement).where(Requirement.number == 2)).one()
    assert ("possible_duplicate", "REQ-001", "suggested") in pair_findings(new)


def test_the_rule_checks_leave_pair_findings_alone(db, project, similar):
    old, new = add(db, project, A, 1), add(db, project, B, 2)
    run(db, project)
    validate_requirement(new, project)
    db.commit()
    assert pair_findings(new) == [("possible_duplicate", "REQ-001", "suggested")]


def test_a_single_requirement_needs_no_model(db, project, similar):
    similar.unavailable = "should not be asked"
    add(db, project, A, 1)
    assert run(db, project) is None
