"""Auth input rules: each failure gives a clear message on the field it belongs to."""

import pytest

GOOD = {
    "name": "Alice",
    "email": "alice@example.com",
    "password": "password123",
    "confirm_password": "password123",
}


def field_errors(response) -> dict[str, str]:
    assert response.status_code == 422
    return {error["loc"][-1]: error["msg"] for error in response.json()["detail"]}


def register(client, **changes):
    return client.post("/auth/register", json={**GOOD, **changes})


def test_good_input_is_accepted(client):
    assert register(client).status_code == 201


@pytest.mark.parametrize(
    "name, message",
    [
        ("", "Name is required."),
        ("   ", "Name is required."),
        ("A", "Name must be 2 to 60 characters."),
        ("A" * 61, "Name must be 2 to 60 characters."),
    ],
)
def test_bad_names(client, name, message):
    assert field_errors(register(client, name=name)) == {"name": message}


@pytest.mark.parametrize("name", ["Al", "A" * 60, "জিনাইন"])
def test_good_names(client, name):
    assert register(client, name=name).status_code == 201


def test_name_is_trimmed(client):
    assert register(client, name="  Alice  ").json()["name"] == "Alice"


@pytest.mark.parametrize(
    "email, message",
    [
        ("", "Email is required."),
        ("   ", "Email is required."),
        ("not-an-email", "Enter a valid email address."),
        ("a@b", "Enter a valid email address."),
        ("a b@example.com", "Enter a valid email address."),
    ],
)
def test_bad_emails(client, email, message):
    assert field_errors(register(client, email=email)) == {"email": message}


def test_email_is_trimmed_and_lowercased(client):
    assert register(client, email="  Alice@EXAMPLE.com ").json()["email"] == "alice@example.com"


@pytest.mark.parametrize(
    "password, message",
    [
        ("abc1", "Password must be at least 8 characters."),
        ("a1" * 65, "Password must be at most 128 characters."),
        ("12345678", "Password must contain a letter."),
        ("abcdefgh", "Password must contain a number."),
    ],
)
def test_bad_passwords(client, password, message):
    assert field_errors(register(client, password=password, confirm_password=password)) == {"password": message}


def test_password_at_the_length_limits_is_accepted(client):
    short, long = "abcdefg1", "a" * 127 + "1"

    assert register(client, password=short, confirm_password=short).status_code == 201
    assert register(client, email="b@example.com", password=long, confirm_password=long).status_code == 201


@pytest.mark.parametrize("password", ["user1@example.com", "USER1@Example.com"])
def test_password_equal_to_email_is_refused_in_any_case(client, password):
    response = register(client, email="user1@example.com", password=password, confirm_password=password)

    assert field_errors(response) == {"password": "Password must not be the same as your email."}


def test_confirmation_must_match(client):
    assert field_errors(register(client, confirm_password="password124")) == {
        "confirm_password": "Passwords do not match."
    }


def test_weak_password_is_not_reported_twice(client):
    errors = field_errors(register(client, password="short", confirm_password="other"))

    assert list(errors) == ["password"]


def test_several_fields_are_reported_together(client):
    errors = field_errors(register(client, name="A", email="nope", password="short"))

    assert set(errors) >= {"name", "email", "password"}


def test_messages_have_no_pydantic_prefix(client):
    errors = field_errors(register(client, name="", email="", password=""))

    assert not any(message.startswith("Value error") for message in errors.values())


def test_login_does_not_apply_the_new_password_rules(client):
    response = client.post("/auth/login", json={"email": "old@example.com", "password": "short"})

    assert response.status_code == 401  # refused as wrong, not as invalid


def test_login_still_checks_the_email_format(client):
    response = client.post("/auth/login", json={"email": "nope", "password": "x"})

    assert field_errors(response) == {"email": "Enter a valid email address."}
