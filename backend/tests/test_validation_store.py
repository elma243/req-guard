"""Storing validation results: re-runs follow CLAUDE.md rule 2 and never touch a human decision."""

import pytest

from app.models import Project, Requirement, RequirementVersion, User, ValidationFinding
from app.nlp.validate import validate_project, validate_requirement

VAGUE_STATEMENT = "The customer shall be fast."
CLEAN_STATEMENT = "The customer shall get a report."


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


def add_requirement(db, project: Project, statement: str, number: int = 1) -> Requirement:
    requirement = Requirement(
        project_id=project.id,
        number=number,
        code=f"REQ-{number:03d}",
        origin="ai",
        status="suggested",
        versions=[RequirementVersion(version=1, statement=statement, type="functional", actor="", origin="ai")],
    )
    db.add(requirement)
    db.commit()
    return requirement


def new_version(requirement: Requirement, statement: str, origin: str = "user") -> None:
    current = requirement.current
    requirement.versions.append(
        RequirementVersion(
            version=current.version + 1, statement=statement, type=current.type, actor=current.actor, origin=origin
        )
    )


def test_findings_are_stored_as_ai_suggestions(db, project):
    requirement = add_requirement(db, project, VAGUE_STATEMENT)

    validate_requirement(requirement, project)
    db.commit()

    [finding] = requirement.findings
    assert finding.id is not None
    assert (finding.category, finding.rule, finding.severity) == ("vague", "vague_word", "medium")
    assert (finding.origin, finding.status, finding.version) == ("ai", "suggested", 1)
    assert finding.evidence == "fast"
    assert "may" in finding.reason
    assert requirement.quality_score == round(100 * 65 / 90)
    assert [f["criterion"] for f in requirement.quality_failed] == ["unambiguous"]


def test_module_is_stored_and_cleared(db, project):
    project.modules = [{"name": "Reports", "description": ""}]
    requirement = add_requirement(db, project, CLEAN_STATEMENT)

    validate_requirement(requirement, project)
    assert (requirement.module_name, requirement.module_score) == ("Reports", 0.6)

    project.modules = [{"name": "Payments", "description": ""}]
    validate_requirement(requirement, project)
    db.commit()
    assert (requirement.module_name, requirement.module_score) == (None, None)
    assert [f.rule for f in requirement.findings] == ["no_matching_module"]


def test_a_rerun_keeps_a_valid_finding_and_does_not_add_it_again(db, project):
    requirement = add_requirement(db, project, VAGUE_STATEMENT)
    validate_requirement(requirement, project)
    db.commit()
    finding_id = requirement.findings[0].id

    validate_requirement(requirement, project)
    db.commit()

    assert [f.id for f in requirement.findings] == [finding_id]


def test_a_rerun_removes_a_suggestion_that_no_longer_applies(db, project):
    requirement = add_requirement(db, project, VAGUE_STATEMENT)
    validate_requirement(requirement, project)
    db.commit()

    new_version(requirement, CLEAN_STATEMENT)
    validate_requirement(requirement, project)
    db.commit()

    assert requirement.findings == []
    assert db.query(ValidationFinding).count() == 0
    assert requirement.quality_score == 100


def test_a_finding_that_still_applies_moves_to_the_new_version(db, project):
    requirement = add_requirement(db, project, VAGUE_STATEMENT)
    validate_requirement(requirement, project)
    db.commit()

    new_version(requirement, "The customer shall be fast at login.")
    validate_requirement(requirement, project)

    assert [(f.evidence, f.version) for f in requirement.findings] == [("fast", 2)]


@pytest.mark.parametrize("status", ["accepted", "edited", "rejected"])
def test_a_decided_finding_is_never_changed_or_repeated(db, project, status):
    requirement = add_requirement(db, project, VAGUE_STATEMENT)
    validate_requirement(requirement, project)
    finding = requirement.findings[0]
    finding.status = status
    finding.reason = "Decided by a person."
    db.commit()

    new_version(requirement, "The customer shall be fast at login.")
    validate_requirement(requirement, project)
    db.commit()

    assert requirement.findings == [finding]
    assert (finding.status, finding.reason, finding.version) == (status, "Decided by a person.", 1)


@pytest.mark.parametrize("status", ["accepted", "edited", "rejected"])
def test_a_decided_finding_stays_after_the_problem_is_gone(db, project, status):
    requirement = add_requirement(db, project, VAGUE_STATEMENT)
    validate_requirement(requirement, project)
    requirement.findings[0].status = status
    db.commit()

    new_version(requirement, CLEAN_STATEMENT)
    validate_requirement(requirement, project)
    db.commit()

    assert [f.status for f in requirement.findings] == [status]


def test_a_finding_written_by_a_person_is_never_changed(db, project):
    requirement = add_requirement(db, project, CLEAN_STATEMENT)
    written = ValidationFinding(
        version=1,
        category="vague",
        rule="vague_word",
        severity="high",
        reason="A person's note.",
        evidence="report",
        origin="user",
        status="suggested",
    )
    requirement.findings.append(written)
    db.commit()

    validate_requirement(requirement, project)
    db.commit()

    assert requirement.findings == [written]
    assert (written.severity, written.reason) == ("high", "A person's note.")


def test_a_rejected_finding_no_longer_lowers_the_score(db, project):
    requirement = add_requirement(db, project, VAGUE_STATEMENT)
    validate_requirement(requirement, project)
    assert requirement.quality_score < 100
    requirement.findings[0].status = "rejected"

    validate_requirement(requirement, project)
    db.commit()

    assert requirement.quality_score == 100
    assert requirement.quality_failed == []
    assert requirement.open_findings == 0
    assert not requirement.has_issues


def test_an_accepted_finding_still_lowers_the_score(db, project):
    requirement = add_requirement(db, project, VAGUE_STATEMENT)
    validate_requirement(requirement, project)
    requirement.findings[0].status = "accepted"

    validate_requirement(requirement, project)

    assert requirement.quality_score < 100
    assert requirement.open_findings == 1


def test_validating_never_changes_the_requirement_itself(db, project):
    requirement = add_requirement(db, project, VAGUE_STATEMENT)
    requirement.status = "accepted"

    validate_requirement(requirement, project)
    db.commit()

    assert requirement.status == "accepted"
    assert requirement.current.statement == VAGUE_STATEMENT
    assert len(requirement.versions) == 1


def test_project_run_counts_and_only_new(db, project):
    first = add_requirement(db, project, VAGUE_STATEMENT, number=1)
    add_requirement(db, project, CLEAN_STATEMENT, number=2)

    result = validate_project(db, project)
    db.commit()
    assert (result.validated, result.with_issues, result.findings) == (2, 1, 1)

    # Not validated again: a changed text would show if it were
    new_version(first, CLEAN_STATEMENT)
    db.commit()
    third = add_requirement(db, project, VAGUE_STATEMENT, number=3)

    result = validate_project(db, project, only_new=True)
    db.commit()

    assert (result.validated, result.with_issues, result.findings) == (1, 1, 1)
    assert third.quality_score is not None
    assert [f.rule for f in first.findings] == ["vague_word"]


def test_closed_requirements_are_not_counted_as_issues(db, project):
    rejected = add_requirement(db, project, VAGUE_STATEMENT, number=1)
    merged = add_requirement(db, project, VAGUE_STATEMENT, number=2)
    rejected.status, merged.status = "rejected", "merged"

    result = validate_project(db, project)

    assert (result.validated, result.with_issues) == (2, 0)
