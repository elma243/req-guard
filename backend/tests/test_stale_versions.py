"""Embeddings and pair verdicts are never written for requirement versions that no longer exist.

Regression for a 500 on Supabase: re-transcribe extracted the project's other unread messages too, while auto-extract
replaced the same suggestions in another session; validation then inserted embeddings for the deleted versions
(foreign key violation). Here a second session deletes a requirement after the first one loaded it.
"""

import pytest
from sqlalchemy import delete, select

from app.models import PairVerdict, Project, Requirement, RequirementEmbedding, RequirementVersion, User
from app.nlp.pair_checks import check_similarity, judge_pairs
from app.nlp.similarity import ensure_embeddings
from tests.fake_llm import FakeLLM
from tests.fake_models import FakeEmbedder


def _requirement(project: Project, number: int, statement: str) -> Requirement:
    return Requirement(
        project_id=project.id,
        number=number,
        code=f"REQ-{number:03d}",
        origin="ai",
        status="suggested",
        versions=[RequirementVersion(version=1, statement=statement, type="functional", actor="", origin="ai")],
    )


@pytest.fixture
def stale(db_session_factory):
    """(session, project, kept version, deleted version): the deleted one is still loaded in `session`."""
    db = db_session_factory()
    user = User(name="Owner", email="owner@example.com", password_hash="x")
    project = Project(name="Acme", owner=user)
    db.add_all([user, project])
    db.flush()
    kept = _requirement(project, 1, "The admin dashboard must show the daily order report.")
    gone = _requirement(project, 2, "The admin dashboard must show the weekly order report.")
    similar = _requirement(project, 3, "The admin dashboard must show the daily order report as PDF.")
    db.add_all([kept, gone, similar])
    db.commit()
    kept_version, gone_version = kept.current, gone.current

    with db_session_factory() as other:  # another extraction replaces the suggestion
        other.execute(delete(Requirement).where(Requirement.id == gone.id))
        other.commit()
    yield db, project, kept_version, gone_version
    db.close()


def test_embeddings_skip_deleted_versions(stale):
    db, _, kept, gone = stale
    vectors = ensure_embeddings(db, FakeEmbedder(), [kept, gone])
    db.flush()  # used to fail: insert on requirement_embeddings violates the foreign key
    assert set(vectors) == {kept.id}
    assert db.scalars(select(RequirementEmbedding.version_id)).all() == [kept.id]


def test_pair_verdicts_skip_deleted_versions(stale):
    db, _, kept, gone = stale
    llm = FakeLLM()
    llm.default_relation = "duplicate"
    answers = judge_pairs(db, llm, [(kept, gone)])
    db.commit()
    assert answers[(kept.id, gone.id)][0] == "duplicate"  # the answer is still used in this run
    assert db.scalar(select(PairVerdict.id)) is None


def test_similarity_check_reloads_requirements(stale):
    db, project, kept, _ = stale
    assert check_similarity(db, project) is None
    db.commit()
    stored = set(db.scalars(select(RequirementEmbedding.version_id)))
    versions = set(db.scalars(select(RequirementVersion.id)))
    assert kept.id in stored and stored <= versions
