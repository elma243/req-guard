from pathlib import Path

import pytest

from app.api import sources as sources_api

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def owner(client, make_user):
    """Headers of a project owner and the base URL of their project."""
    _, headers = make_user()
    project = client.post("/projects", json={"name": "Acme"}, headers=headers).json()
    return headers, f"/projects/{project['id']}"


def upload(client, headers, base, *files: tuple[str, bytes]):
    return client.post(
        f"{base}/uploads",
        files=[("files", (name, data, "application/octet-stream")) for name, data in files],
        headers=headers,
    )


def all_files():
    return [
        ("WhatsApp Chat with Acme Logistics.txt", fixture("whatsapp_android.txt")),
        ("message_1.json", fixture("messenger_message_1.json")),
        ("client_email.eml", fixture("client_email.eml")),
        ("kickoff.md", fixture("meeting_notes.md")),
    ]


# ---------- upload ----------


def test_upload_counts_per_file_and_total(client, owner):
    headers, base = owner

    res = upload(client, headers, base, *all_files())

    assert res.status_code == 200, res.text
    body = res.json()
    per_file = {f["filename"]: (f["type"], f["imported"], f["duplicates"], f["skipped"]) for f in body["files"]}
    assert per_file == {
        "WhatsApp Chat with Acme Logistics.txt": ("whatsapp", 5, 0, 5),
        "message_1.json": ("messenger", 4, 0, 3),
        "client_email.eml": ("email", 1, 0, 0),
        "kickoff.md": ("meeting_notes", 4, 0, 6),
    }
    assert (body["imported"], body["duplicates"], body["skipped"]) == (14, 0, 14)


def test_second_upload_is_all_duplicates(client, owner):
    headers, base = owner
    upload(client, headers, base, *all_files())

    body = upload(client, headers, base, *all_files()).json()

    assert body["imported"] == 0
    assert body["duplicates"] == 14
    assert client.get(f"{base}/messages", headers=headers).json()["total"] == 14


def test_same_file_twice_in_one_upload(client, owner):
    headers, base = owner
    chat = ("chat.txt", fixture("whatsapp_ios.txt"))

    body = upload(client, headers, base, chat, chat).json()

    assert [(f["imported"], f["duplicates"]) for f in body["files"]] == [(5, 0), (0, 5)]


def test_unsupported_file_does_not_block_others(client, owner):
    headers, base = owner

    body = upload(
        client,
        headers,
        base,
        ("contract.pdf", fixture("unsupported.pdf")),
        ("other.json", b'{"hello": "world"}'),
        ("client_email.eml", fixture("client_email.eml")),
    ).json()

    pdf, other_json, email = body["files"]
    assert pdf["type"] is None and "Unsupported file" in pdf["error"]
    assert other_json["type"] is None and other_json["error"]
    assert email["error"] is None and email["imported"] == 1
    assert body["imported"] == 1


def test_too_many_files_is_400(client, owner):
    headers, base = owner
    files = [(f"notes{i}.txt", b"Point.") for i in range(sources_api.MAX_FILES + 1)]
    assert upload(client, headers, base, *files).status_code == 400


def test_too_large_file_is_reported(client, owner, monkeypatch):
    headers, base = owner
    monkeypatch.setattr(sources_api, "MAX_FILE_BYTES", 100)

    body = upload(client, headers, base, ("big.txt", b"x" * 101)).json()

    assert body["files"][0]["error"] == "File is larger than 10 MB."
    assert body["imported"] == 0


def test_upload_without_files_is_422(client, owner):
    headers, base = owner
    assert client.post(f"{base}/uploads", headers=headers).status_code == 422


def test_sources_are_created_once_with_counts(client, owner):
    headers, base = owner
    upload(client, headers, base, *all_files())
    upload(client, headers, base, ("client_email_html.eml", fixture("client_email_html.eml")))

    sources = client.get(f"{base}/sources", headers=headers).json()

    by_type = {s["type"]: s for s in sources}
    assert len(sources) == 4
    assert by_type["whatsapp"]["display_name"] == "Acme Logistics"
    assert by_type["messenger"]["display_name"] == "Acme Logistics – Delivery App"
    assert by_type["email"]["display_name"] == "Email files"
    assert by_type["email"]["message_count"] == 2
    assert by_type["email"]["config"] == {"origin": "upload", "last_file": "client_email_html.eml"}
    assert by_type["meeting_notes"]["message_count"] == 4
    assert all(s["last_synced_at"] for s in sources)


# ---------- list + filters ----------


@pytest.fixture
def loaded(client, owner):
    headers, base = owner
    upload(client, headers, base, *all_files())
    return headers, base


def get(client, headers, base, **params):
    res = client.get(f"{base}/messages", params=params, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def test_list_is_newest_first(client, loaded):
    body = get(client, *loaded)

    assert body["total"] == 14
    times = [m["sent_at"] for m in body["items"]]
    assert times == sorted(times, reverse=True)
    first = body["items"][0]
    assert first["source"] == "meeting_notes"  # dated 2026-10-01, newest
    assert first["sent_at"].endswith(("Z", "+00:00"))
    assert "raw" not in first


def test_filter_by_source(client, loaded):
    body = get(client, *loaded, source="whatsapp")
    assert body["total"] == 5
    assert {m["source"] for m in body["items"]} == {"whatsapp"}


def test_filter_by_author_is_case_insensitive_contains(client, loaded):
    body = get(client, *loaded, author="sarah")
    # 3 WhatsApp + 2 Messenger + 1 email from Sarah Khan
    assert body["total"] == 6
    assert {m["author"] for m in body["items"]} == {"Sarah Khan"}


def test_text_search(client, loaded):
    body = get(client, *loaded, q="DISPATCHER")
    # 1 WhatsApp, 1 email, 2 meeting-note paragraphs
    assert body["total"] == 4
    assert all("dispatcher" in m["text"].lower() for m in body["items"])


def test_search_escapes_like_wildcards(client, loaded):
    assert get(client, *loaded, q="%")["total"] == 0
    assert get(client, *loaded, q="_")["total"] == 0


def test_date_range_is_inclusive(client, loaded):
    # WhatsApp 12-13 March, Messenger 18 March, email 19 March 2024
    assert get(client, *loaded, date_from="2024-03-13", date_to="2024-03-18")["total"] == 2 + 4
    assert get(client, *loaded, date_from="2024-03-19", date_to="2024-03-19")["total"] == 1
    assert get(client, *loaded, date_from="2026-01-01")["total"] == 4


def test_empty_filters_list_all_messages(client, loaded):
    # Regression: the Messages page sent every filter key, empty ones as "", and date_from="" was a 422,
    # so already imported messages never showed
    body = get(client, *loaded, source="", author="", q="", date_from="", date_to="", page=1, page_size=50)
    assert body["total"] == 14


@pytest.mark.parametrize("value", ["", "  ", "0002-", "2024-13-45", "2024-02-30", "dd-mm-yyyy", "not a date"])
def test_empty_or_invalid_date_is_no_filter(client, loaded, value):
    # Regression: a half-typed date box must not fail the whole list
    assert get(client, *loaded, date_from=value)["total"] == 14
    assert get(client, *loaded, date_to=value)["total"] == 14


def test_valid_date_still_filters_next_to_an_invalid_one(client, loaded):
    assert get(client, *loaded, date_from="2026-01-01", date_to="2024-")["total"] == 4
    only_to = get(client, *loaded, date_to="2024-03-13")["total"]
    assert 0 < only_to < 14
    assert get(client, *loaded, date_from="", date_to="2024-03-13")["total"] == only_to


def test_last_possible_date_does_not_overflow(client, loaded):
    assert get(client, *loaded, date_to="9999-12-31")["total"] == 14


def test_filters_combine(client, loaded):
    body = get(client, *loaded, source="whatsapp", author="rahim", q="delivery")
    assert body["total"] == 1
    assert body["items"][0]["text"].startswith("Got it.")


def test_pagination(client, loaded):
    first = get(client, *loaded, page=1, page_size=5)
    third = get(client, *loaded, page=3, page_size=5)
    beyond = get(client, *loaded, page=4, page_size=5)

    assert (first["total"], len(first["items"]), first["page"], first["page_size"]) == (14, 5, 1, 5)
    assert len(third["items"]) == 4
    assert beyond["items"] == []
    ids = [m["id"] for p in (1, 2, 3) for m in get(client, *loaded, page=p, page_size=5)["items"]]
    assert len(set(ids)) == 14


def test_invalid_page_size_is_422(client, loaded):
    headers, base = loaded
    assert client.get(f"{base}/messages", params={"page_size": 101}, headers=headers).status_code == 422
    assert client.get(f"{base}/messages", params={"page": 0}, headers=headers).status_code == 422


# ---------- access ----------


def test_messages_are_per_project(client, loaded):
    headers, _ = loaded
    other = client.post("/projects", json={"name": "Other"}, headers=headers).json()
    assert get(client, headers, f"/projects/{other['id']}")["total"] == 0


def test_non_owner_gets_404(client, loaded, make_user):
    _, base = loaded
    _, stranger = make_user()

    assert client.get(f"{base}/messages", headers=stranger).status_code == 404
    assert client.get(f"{base}/sources", headers=stranger).status_code == 404
    assert upload(client, stranger, base, ("notes.txt", b"Point.")).status_code == 404


def test_login_required(client, loaded):
    _, base = loaded
    assert client.get(f"{base}/messages").status_code == 401
    assert client.get(f"{base}/sources").status_code == 401
    assert upload(client, {}, base, ("notes.txt", b"Point.")).status_code == 401


def test_deleting_project_deletes_messages_and_sources(client, loaded, db_session_factory):
    from app.models import Message, Source

    headers, base = loaded
    assert client.delete(base, headers=headers).status_code == 204

    db = db_session_factory()
    assert db.query(Message).count() == 0
    assert db.query(Source).count() == 0
    db.close()
