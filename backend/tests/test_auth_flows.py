"""Whole auth flows through the API: email verification, password reset, rate limits and lockout."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.api import auth as auth_api
from app.core import ratelimit
from app.core.security import verify_password
from app.models import EmailOtp, User

EMAIL = "alice@example.com"
PASSWORD = "password123"
REGISTER = {"name": "Alice", "email": EMAIL, "password": PASSWORD, "confirm_password": PASSWORD}
LOGIN = {"email": EMAIL, "password": PASSWORD}


def register(client):
    res = client.post("/auth/register", json=REGISTER)
    assert res.status_code == 201, res.text
    return res


def verify(client, code, email=EMAIL):
    return client.post("/auth/verify-email", json={"email": email, "code": code})


def resend(client, email=EMAIL):
    return client.post("/auth/resend-code", json={"email": email})


def wrong_code(code: str) -> str:
    return "000000" if code != "000000" else "111111"


@pytest.fixture
def clock(monkeypatch):
    """Controls the rate limiter's time."""
    now = [1000.0]
    monkeypatch.setattr(ratelimit, "monotonic", lambda: now[0])
    return now


@pytest.fixture
def otp_row(db_session_factory):
    def _get():
        with db_session_factory() as db:
            return db.scalar(select(EmailOtp))

    return _get


# ---------- Email verification ----------


def test_register_leaves_account_unverified_and_emails_a_code(client, outbox, db_session_factory):
    register(client)

    with db_session_factory() as db:
        assert db.scalar(select(User)).email_verified_at is None
    assert [to for to, _ in outbox] == [EMAIL]
    assert len(outbox.code_for(EMAIL)) == 6


def test_only_a_hash_of_the_code_is_stored(client, outbox, otp_row):
    register(client)

    assert outbox.code_for(EMAIL) not in otp_row().code_hash
    assert len(otp_row().code_hash) == 64


def test_code_expires_after_ten_minutes(client, otp_row):
    before = datetime.now(UTC)
    register(client)

    expires_at = otp_row().expires_at.replace(tzinfo=UTC)
    assert timedelta(minutes=9, seconds=59) < expires_at - before < timedelta(minutes=10, seconds=5)


def test_login_is_blocked_until_verified(client, outbox):
    register(client)

    blocked = client.post("/auth/login", json=LOGIN)
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "Please verify your email"

    assert verify(client, outbox.code_for(EMAIL)).status_code == 200
    assert client.post("/auth/login", json=LOGIN).status_code == 200


def test_unverified_account_with_wrong_password_gets_the_generic_error(client):
    register(client)

    res = client.post("/auth/login", json={**LOGIN, "password": "wrong-pass1"})

    assert res.status_code == 401
    assert res.json()["detail"] == "Email or password is incorrect"


def test_verifying_removes_the_code_so_it_works_once(client, outbox, otp_row):
    register(client)
    code = outbox.code_for(EMAIL)
    verify(client, code)

    assert otp_row() is None
    assert verify(client, code).status_code == 400


def test_expired_code_is_refused(client, outbox, db_session_factory):
    register(client)
    with db_session_factory() as db:
        db.scalar(select(EmailOtp)).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    res = verify(client, outbox.code_for(EMAIL))

    assert res.status_code == 400
    assert client.post("/auth/login", json=LOGIN).status_code == 403


def test_five_wrong_codes_kill_the_code(client, outbox):
    register(client)
    code = outbox.code_for(EMAIL)
    for _ in range(5):
        assert verify(client, wrong_code(code)).status_code == 400

    assert verify(client, code).status_code == 400
    assert client.post("/auth/login", json=LOGIN).status_code == 403


def test_right_code_on_the_fifth_try_still_works(client, outbox):
    register(client)
    code = outbox.code_for(EMAIL)
    for _ in range(4):
        verify(client, wrong_code(code))

    assert verify(client, code).status_code == 200


def test_attempts_are_counted_per_user(client, outbox, otp_row):
    register(client)
    verify(client, wrong_code(outbox.code_for(EMAIL)))

    assert otp_row().attempts == 1


def test_all_code_failures_look_the_same(client, outbox, db_session_factory):
    register(client)
    code = outbox.code_for(EMAIL)
    wrong = verify(client, wrong_code(code))
    unknown = verify(client, code, email="nobody@example.com")
    with db_session_factory() as db:
        db.scalar(select(EmailOtp)).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    expired = verify(client, code)

    assert wrong.status_code == unknown.status_code == expired.status_code == 400
    assert wrong.json() == unknown.json() == expired.json()


def test_code_of_another_user_is_refused(client, outbox):
    register(client)
    client.post("/auth/register", json={**REGISTER, "email": "bob@example.com"})

    assert verify(client, outbox.code_for("bob@example.com")).status_code == 400


# ---------- Resend ----------


def test_resend_is_refused_during_the_cooldown(client, outbox, clock):
    register(client)
    clock[0] += 59

    res = resend(client)

    assert res.status_code == 429
    assert res.headers["Retry-After"] == "1"
    assert len(outbox) == 1


def test_resend_after_the_cooldown_replaces_the_code(client, outbox, clock, monkeypatch):
    codes = iter(["111111", "222222"])
    monkeypatch.setattr(auth_api, "generate_otp", lambda: next(codes))
    register(client)
    clock[0] += 60

    assert resend(client).status_code == 200

    assert len(outbox) == 2
    assert verify(client, "111111").status_code == 400
    assert verify(client, "222222").status_code == 200


def test_resend_gives_fresh_attempts(client, outbox, otp_row, clock):
    register(client)
    code = outbox.code_for(EMAIL)
    for _ in range(5):
        verify(client, wrong_code(code))
    clock[0] += 60

    resend(client)

    assert otp_row().attempts == 0
    assert verify(client, outbox.code_for(EMAIL)).status_code == 200


def test_resend_cooldown_repeats(client, clock):
    register(client)
    clock[0] += 60
    resend(client)

    assert resend(client).status_code == 429


def test_resend_answers_the_same_for_every_kind_of_email(client, outbox, clock):
    register(client)
    client.post("/auth/register", json={**REGISTER, "email": "done@example.com"})
    verify(client, outbox.code_for("done@example.com"), email="done@example.com")
    clock[0] += 60
    sent_before = len(outbox)

    unverified = resend(client)
    verified = resend(client, "done@example.com")
    unknown = resend(client, "nobody@example.com")

    assert unverified.status_code == verified.status_code == unknown.status_code == 200
    assert unverified.json() == verified.json() == unknown.json()
    assert [to for to, _ in outbox[sent_before:]] == [EMAIL]


def test_unknown_email_gets_the_same_cooldown(client, clock):
    assert resend(client, "nobody@example.com").status_code == 200

    assert resend(client, "nobody@example.com").status_code == 429


# ---------- Taken emails ----------


def test_register_on_a_verified_email_is_409(client, outbox):
    register(client)
    verify(client, outbox.code_for(EMAIL))

    assert client.post("/auth/register", json=REGISTER).status_code == 409


def test_register_on_an_unverified_email_keeps_the_first_password(client, db_session_factory):
    register(client)

    res = client.post("/auth/register", json={**REGISTER, "password": "another123", "confirm_password": "another123"})

    assert res.status_code == 409
    with db_session_factory() as db:
        assert verify_password(PASSWORD, db.scalar(select(User)).password_hash)


def test_code_email_states_the_expiry(client, outbox):
    register(client)

    assert f"{auth_api.OTP_EXPIRE_MINUTES} minutes" in outbox.last_to(EMAIL).text
