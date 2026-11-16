"""Shared setup for the Step 8B tests: approved requirements, generation through the fake LLM, and short API calls."""

from app.models import Requirement
from tests.clarify_helpers import CLEAN, seed_requirement


def call(client, project, method: str, path: str, body=None):
    headers, base, _ = project
    return client.request(method, f"{base}{path}", json=body, headers=headers)


def approve(client, project, rid: int):
    res = call(client, project, "POST", f"/requirements/{rid}/approve")
    assert res.status_code == 200, res.text
    return res.json()


def seed_approved(client, project, db_session_factory, statement: str = CLEAN) -> int:
    rid = seed_requirement(db_session_factory, project[2], statement)
    approve(client, project, rid)
    return rid


def generate(client, project, rid: int):
    return call(client, project, "POST", f"/requirements/{rid}/test-cases/generate")


def group(client, project, rid: int) -> dict:
    res = call(client, project, "GET", f"/test-cases?requirement_id={rid}")
    assert res.status_code == 200, res.text
    groups = res.json()
    assert len(groups) == 1
    return groups[0]


def case_body(title: str = "Manual check", criterion_id: int | None = None, type: str = "positive") -> dict:
    return {
        "title": title,
        "type": type,
        "given": "an admin on the orders page",
        "when": "they press Export",
        "then": "a CSV file is downloaded",
        "criterion_id": criterion_id,
    }


def requirement_row(db_session_factory, rid: int) -> Requirement:
    with db_session_factory() as db:
        requirement = db.get(Requirement, rid)
        _ = [t.versions for t in requirement.test_cases], [c.versions for c in requirement.criteria]
        return requirement
