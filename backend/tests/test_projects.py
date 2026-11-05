import pytest
from sqlalchemy import func, select

from app.models import TeamMember

PROJECT = {"name": "Acme portal", "description": "Client portal", "scope_text": "Login, orders, invoices"}


def create_project(client, headers, **overrides):
    res = client.post("/projects", json={**PROJECT, **overrides}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def test_project_endpoints_require_login(client):
    assert client.get("/projects").status_code == 401
    assert client.post("/projects", json=PROJECT).status_code == 401
    assert client.get("/projects/1").status_code == 401


def test_create_and_get_project(client, make_user):
    user, headers = make_user()

    project = create_project(client, headers)

    assert project["owner_id"] == user["id"]
    assert project["scope_text"] == PROJECT["scope_text"]
    assert client.get(f"/projects/{project['id']}", headers=headers).json() == project


def test_create_project_requires_name(client, make_user):
    _, headers = make_user()
    assert client.post("/projects", json={"name": "  "}, headers=headers).status_code == 422


def test_list_shows_only_own_projects(client, make_user):
    _, alice = make_user()
    _, bob = make_user()
    create_project(client, alice, name="Alice 1")
    create_project(client, alice, name="Alice 2")
    create_project(client, bob, name="Bob 1")

    names = [p["name"] for p in client.get("/projects", headers=alice).json()]

    assert sorted(names) == ["Alice 1", "Alice 2"]


def test_other_user_cannot_read_update_or_delete(client, make_user):
    _, alice = make_user()
    _, bob = make_user()
    project = create_project(client, alice)
    url = f"/projects/{project['id']}"

    assert client.get(url, headers=bob).status_code == 404
    assert client.patch(url, json={"name": "Hacked"}, headers=bob).status_code == 404
    assert client.delete(url, headers=bob).status_code == 404
    # Unchanged for the owner
    assert client.get(url, headers=alice).json()["name"] == PROJECT["name"]


def test_missing_project_is_404(client, make_user):
    _, headers = make_user()
    assert client.get("/projects/999", headers=headers).status_code == 404


def test_owner_can_update_only_sent_fields(client, make_user):
    _, headers = make_user()
    project = create_project(client, headers)

    res = client.patch(f"/projects/{project['id']}", json={"description": "New text"}, headers=headers)

    assert res.status_code == 200
    assert res.json()["description"] == "New text"
    assert res.json()["name"] == PROJECT["name"]


def test_owner_can_delete_and_team_is_deleted(client, make_user, db_session_factory):
    _, headers = make_user()
    project = create_project(client, headers)
    client.post(
        f"/projects/{project['id']}/team",
        json={"name": "Dev", "email": "dev@example.com", "role": "developer"},
        headers=headers,
    )

    res = client.delete(f"/projects/{project['id']}", headers=headers)

    assert res.status_code == 204
    assert client.get(f"/projects/{project['id']}", headers=headers).status_code == 404
    with db_session_factory() as db:
        assert db.scalar(select(func.count()).select_from(TeamMember)) == 0


MODULES = [{"name": "Payments", "description": "bKash, cards, refunds"}, {"name": "Reports", "description": ""}]


def test_new_project_has_no_modules(client, make_user):
    _, headers = make_user()
    assert create_project(client, headers)["modules"] == []


def test_modules_are_saved_and_replaced_as_a_list(client, make_user):
    _, headers = make_user()
    base = f"/projects/{create_project(client, headers)['id']}"

    res = client.patch(base, json={"modules": MODULES}, headers=headers)
    assert res.status_code == 200, res.text
    assert client.get(base, headers=headers).json()["modules"] == MODULES

    # A change to another field leaves them alone; an empty list clears them
    assert client.patch(base, json={"name": "New name"}, headers=headers).json()["modules"] == MODULES
    assert client.patch(base, json={"modules": []}, headers=headers).json()["modules"] == []


def test_module_names_are_trimmed_and_can_be_bangla(client, make_user):
    _, headers = make_user()
    base = f"/projects/{create_project(client, headers)['id']}"

    res = client.patch(base, json={"modules": [{"name": "  পেমেন্ট ", "description": " বিকাশ "}]}, headers=headers)

    assert res.json()["modules"] == [{"name": "পেমেন্ট", "description": "বিকাশ"}]


def test_project_can_be_created_with_modules(client, make_user):
    _, headers = make_user()
    assert create_project(client, headers, modules=MODULES)["modules"] == MODULES


@pytest.mark.parametrize(
    "modules",
    [
        [{"name": "", "description": "no name"}],
        [{"name": "   "}],
        [{"name": "x" * 101}],
        [{"name": "Payments", "description": "x" * 2001}],
        [{"name": "Payments"}, {"name": "payments"}],
        [{"name": f"Module {n}"} for n in range(51)],
    ],
)
def test_invalid_modules_are_refused(client, make_user, modules):
    _, headers = make_user()
    base = f"/projects/{create_project(client, headers)['id']}"

    assert client.patch(base, json={"modules": modules}, headers=headers).status_code == 422
    assert client.get(base, headers=headers).json()["modules"] == []
