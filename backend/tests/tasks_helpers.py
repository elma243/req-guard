"""Shared setup for the Step 9 tests: modules, team members, approved requirements with set embeddings, and short
API calls for the task board."""

from tests.case_helpers import call, seed_approved
from tests.fake_models import unit, vector_at

PAY = "The customer shall pay for an order by bKash."
REFUND = "The customer shall get a refund by bKash."
SALES_PDF = "The admin shall export the sales report as PDF."
STOCK_PDF = "The admin shall export the stock report as PDF."
RESET = "The customer shall reset the password by email."

MODULES = [{"name": "Payments", "description": "bKash checkout refund"}]


def set_modules(client, project, modules=MODULES) -> None:
    res = call(client, project, "PATCH", "", {"modules": modules})
    assert res.status_code == 200, res.text


def add_member(client, project, name: str, skills: list[str], role: str = "developer") -> int:
    headers, base, _ = project
    email = f"{name.lower().replace(' ', '.')}@example.com"
    res = client.post(f"{base}/team", json={"name": name, "email": email, "skills": skills, "role": role}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


def embed_reports_apart(fake_models) -> None:
    """The two report exports are close to each other; the password reset is far from both."""
    fake_models.embedder.vectors[SALES_PDF] = unit(0)
    fake_models.embedder.vectors[STOCK_PDF] = vector_at(0.9, axis=1)
    fake_models.embedder.vectors[RESET] = unit(5)


def approved(client, project, db_session_factory, *statements: str) -> list[int]:
    return [seed_approved(client, project, db_session_factory, s) for s in statements]


def run_grouping(client, project) -> dict:
    res = call(client, project, "POST", "/tasks/group")
    assert res.status_code == 200, res.text
    return res.json()


def board(client, project, query: str = "") -> dict:
    res = call(client, project, "GET", f"/tasks{query}")
    assert res.status_code == 200, res.text
    return res.json()


def tasks_by_requirement(client, project) -> dict[str, dict]:
    """Every task on the board by its requirement code, with its group's name added as "group"."""
    return {
        t["requirement"]["code"]: {**t, "group": g["name"], "group_kind": g["kind"]}
        for g in board(client, project)["groups"]
        for t in g["tasks"]
    }
