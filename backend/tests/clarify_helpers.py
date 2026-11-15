"""Shared setup for the Step 8A tests: a project through the API, requirements and findings straight in the
database (validated by the real rules), and the fake LLM wired into both LLM dependencies."""

from datetime import UTC, datetime

from app.api.deps import get_llm_client, get_optional_llm_client
from app.main import app
from app.models import Message, Project, Requirement, RequirementSource, RequirementVersion, ValidationFinding
from app.nlp.validate import validate_requirement
from tests.fake_llm import FakeLLM

# Non-functional performance with "fast": a vague word (medium) and no measurable value (high)
SLOW_REPORT = "The system shall load the sales report fast."
# A functional requirement with an actor and nothing vague: no findings
CLEAN = "The admin shall export the order list as CSV."


def make_project(client, make_user, name: str = "Acme Shop"):
    _, headers = make_user()
    project = client.post("/projects", json={"name": name}, headers=headers).json()
    return headers, f"/projects/{project['id']}", project["id"]


def use_llm(fake: FakeLLM | None = None) -> FakeLLM:
    fake = fake or FakeLLM()
    app.dependency_overrides[get_llm_client] = lambda: fake
    app.dependency_overrides[get_optional_llm_client] = lambda: fake
    return fake


def seed_requirement(
    db_session_factory,
    project_id: int,
    statement: str,
    *,
    type: str = "functional",
    nfr_category: str | None = None,
    actor: str = "",
    status: str = "accepted",
    sentence: str | None = None,
) -> int:
    """A requirement with one client message as its source, validated by the real rules. Returns its id."""
    with db_session_factory() as db:
        project = db.get(Project, project_id)
        project.requirement_counter += 1
        number = project.requirement_counter
        message = Message(
            project_id=project_id,
            source="whatsapp",
            external_id=f"seed-{number}",
            channel="Acme",
            author="Karim",
            sent_at=datetime(2026, 11, 1, tzinfo=UTC),
            text=sentence or statement,
            raw={},
        )
        db.add(message)
        db.flush()
        requirement = Requirement(
            project_id=project_id,
            number=number,
            code=f"REQ-{number:03d}",
            origin="ai",
            status=status,
            versions=[
                RequirementVersion(
                    version=1,
                    statement=statement,
                    type=type,
                    nfr_category=nfr_category,
                    actor=actor,
                    confidence=0.9,
                    origin="ai",
                )
            ],
            sources=[RequirementSource(message_id=message.id, source_sentence=sentence or statement)],
        )
        db.add(requirement)
        db.flush()
        validate_requirement(requirement, project)
        db.commit()
        return requirement.id


def seed_slow_report(db_session_factory, project_id: int, **kwargs) -> int:
    return seed_requirement(
        db_session_factory,
        project_id,
        SLOW_REPORT,
        type="non_functional",
        nfr_category="performance",
        sentence="Report ta taratari load hote hobe.",
        **kwargs,
    )


def add_conflict(db_session_factory, requirement_id: int, other_id: int, severity: str = "high") -> int:
    """A possible conflict finding (as the pair checks store it). Returns its id."""
    with db_session_factory() as db:
        other = db.get(Requirement, other_id)
        finding = ValidationFinding(
            requirement_id=requirement_id,
            version=1,
            category="conflict",
            rule="possible_conflict",
            severity=severity,
            reason="These two requirements may contradict each other.",
            evidence=other.versions[-1].statement,
            related_requirement_id=other_id,
            score=0.9,
            origin="ai",
            status="suggested",
        )
        db.add(finding)
        db.commit()
        return finding.id


def findings_of(db_session_factory, requirement_id: int) -> list[ValidationFinding]:
    with db_session_factory() as db:
        return list(db.get(Requirement, requirement_id).findings)
