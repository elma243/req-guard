"""Whole auth flows through the API: email verification, password reset, rate limits and lockout."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.api import auth as auth_api
from app.core import ratelimit
from app.core.config import settings
from app.core.security import verify_password
from app.models import EmailOtp, PasswordResetToken, User

EMAIL = "alice@example.com"
PASSWORD = "password123"
REGISTER = {"name": "Alice", "email": EMAIL, "password": PASSWORD, "confirm_password": PASSWORD}
LOGIN = {"email": EMAIL, "password": PASSWORD}
NEW_PASSWORD = "brand-new-pass9"


def register(client):
    res = client.post("/auth/register", json=REGISTER)
    assert res.status_code == 201, res.text
    return res


def verify(client, code, email=EMAIL):
    return client.post("/auth/verify-email", json={"email": email, "code": code})


def resend(client, email=EMAIL):
    return client.post("/auth/resend-code", json={"email": email})


def forgot(client, email=EMAIL):
    return client.post("/auth/forgot-password", json={"email": email})


def reset(client, token, password=NEW_PASSWORD, confirm=None):
    body = {"token": token, "password": password, "confirm_password": confirm or password}
    return client.post("/auth/reset-password", json=body)


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


@pytest.fixture
def verified(client, outbox):
    """A verified account, logged in; returns its auth headers."""
    register(client)
    verify(client, outbox.code_for(EMAIL))
    token = client.post("/auth/login", json=LOGIN).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


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


# ---------- Forgot and reset password ----------


def test_forgot_password_answers_the_same_for_known_and_unknown_emails(client, outbox, verified):
    sent_before = len(outbox)

    known = forgot(client)
    unknown = forgot(client, "nobody@example.com")

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json() == {"message": "If this email exists, we sent a link"}
    assert [to for to, _ in outbox[sent_before:]] == [EMAIL]


def test_reset_link_uses_the_configured_site_not_the_host_header(client, outbox, verified, monkeypatch):
    monkeypatch.setattr(settings, "frontend_url", "https://app.example.com/")

    client.post("/auth/forgot-password", json={"email": EMAIL}, headers={"Host": "evil.example"})

    token = outbox.reset_token_for(EMAIL)
    assert f"https://app.example.com/reset-password?token={token}" in outbox.last_to(EMAIL).text
    assert "evil.example" not in outbox.last_to(EMAIL).html


def test_only_a_hash_of_the_reset_token_is_stored(client, outbox, verified, db_session_factory):
    forgot(client)

    with db_session_factory() as db:
        stored = db.scalar(select(PasswordResetToken))
    assert stored.token_hash != outbox.reset_token_for(EMAIL)
    assert outbox.reset_token_for(EMAIL) not in stored.token_hash


def test_reset_changes_the_password(client, outbox, verified):
    forgot(client)

    assert reset(client, outbox.reset_token_for(EMAIL)).status_code == 200

    assert client.post("/auth/login", json=LOGIN).status_code == 401
    assert client.post("/auth/login", json={**LOGIN, "password": NEW_PASSWORD}).status_code == 200


def test_reset_token_works_only_once(client, outbox, verified):
    forgot(client)
    token = outbox.reset_token_for(EMAIL)
    reset(client, token)

    again = reset(client, token, "another-pass8")

    assert again.status_code == 400
    assert client.post("/auth/login", json={**LOGIN, "password": "another-pass8"}).status_code == 401


def test_reset_token_expires_after_thirty_minutes(client, outbox, verified, db_session_factory):
    before = datetime.now(UTC)
    forgot(client)
    with db_session_factory() as db:
        row = db.scalar(select(PasswordResetToken))
        lifetime = row.expires_at.replace(tzinfo=UTC) - before
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    assert timedelta(minutes=29, seconds=59) < lifetime < timedelta(minutes=30, seconds=5)
    assert reset(client, outbox.reset_token_for(EMAIL)).status_code == 400


def test_unknown_reset_token_is_refused(client):
    assert reset(client, "made-up-token").status_code == 400


def test_a_new_request_cancels_the_older_link(client, outbox, verified):
    forgot(client)
    first = outbox.reset_token_for(EMAIL)
    forgot(client)
    second = outbox.reset_token_for(EMAIL)

    assert first != second
    assert reset(client, first).status_code == 400
    assert reset(client, second).status_code == 200


def test_reset_ends_old_sessions(client, outbox, verified):
    assert client.get("/auth/me", headers=verified).status_code == 200
    forgot(client)

    reset(client, outbox.reset_token_for(EMAIL))

    assert client.get("/auth/me", headers=verified).status_code == 401


def test_session_after_the_reset_works(client, outbox, verified):
    forgot(client)
    reset(client, outbox.reset_token_for(EMAIL))
    token = client.post("/auth/login", json={**LOGIN, "password": NEW_PASSWORD}).json()["access_token"]

    assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_reset_verifies_an_unverified_account_and_drops_its_code(client, outbox, otp_row):
    register(client)  # a squatter's password, on a mailbox they cannot read
    forgot(client)

    reset(client, outbox.reset_token_for(EMAIL))

    assert otp_row() is None
    assert client.post("/auth/login", json=LOGIN).status_code == 401
    assert client.post("/auth/login", json={**LOGIN, "password": NEW_PASSWORD}).status_code == 200


@pytest.mark.parametrize(
    "password, confirm, field, message",
    [
        ("short1", None, "password", "Password must be at least 8 characters."),
        ("onlyletters", None, "password", "Password must contain a number."),
        ("12345678", None, "password", "Password must contain a letter."),
        (NEW_PASSWORD, "different9", "confirm_password", "Passwords do not match."),
    ],
)
def test_reset_applies_the_password_rules(client, outbox, verified, password, confirm, field, message):
    forgot(client)

    res = reset(client, outbox.reset_token_for(EMAIL), password, confirm)

    assert res.status_code == 422
    assert [(e["loc"][-1], e["msg"]) for e in res.json()["detail"]] == [(field, message)]


def test_reset_refuses_a_password_equal_to_the_email(client, outbox):
    email = "user1@example.com"
    client.post("/auth/register", json={**REGISTER, "email": email})
    forgot(client, email)
    token = outbox.reset_token_for(email)

    res = reset(client, token, "USER1@example.com")

    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["body", "password"]
    assert res.json()["detail"][0]["msg"] == "Password must not be the same as your email."
    assert reset(client, token).status_code == 200  # a refused password does not use up the link


# ---------- Lockout ----------


def fail_login(client, times, email=EMAIL):
    return [client.post("/auth/login", json={"email": email, "password": "wrong-pass1"}) for _ in range(times)]


def test_ten_failed_logins_lock_the_account_for_fifteen_minutes(client, verified, db_session_factory):
    responses = fail_login(client, 10)

    assert [r.status_code for r in responses] == [401] * 9 + [423]
    assert all(r.json()["detail"] == "Email or password is incorrect" for r in responses[:9])
    assert "temporarily locked" in responses[9].json()["detail"]
    with db_session_factory() as db:
        remaining = db.scalar(select(User)).locked_until.replace(tzinfo=UTC) - datetime.now(UTC)
    assert timedelta(minutes=14, seconds=55) < remaining <= timedelta(minutes=15)


def test_locked_account_refuses_the_right_password(client, verified):
    fail_login(client, 10)

    res = client.post("/auth/login", json=LOGIN)

    assert res.status_code == 423
    assert "15 minutes" in res.json()["detail"]


def test_last_minute_of_a_lock_reads_correctly(client, verified, db_session_factory):
    with db_session_factory() as db:
        db.scalar(select(User)).locked_until = datetime.now(UTC) + timedelta(seconds=30)
        db.commit()

    assert "Try again in 1 minute or" in client.post("/auth/login", json=LOGIN).json()["detail"]


def test_lock_ends_after_its_time(client, verified, db_session_factory):
    fail_login(client, 10)
    with db_session_factory() as db:
        db.scalar(select(User)).locked_until = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    assert client.post("/auth/login", json=LOGIN).status_code == 200
    with db_session_factory() as db:
        assert db.scalar(select(User)).locked_until is None


def test_failures_start_from_zero_after_a_lock(client, verified, db_session_factory):
    fail_login(client, 10)
    with db_session_factory() as db:
        db.scalar(select(User)).locked_until = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    assert fail_login(client, 1)[0].status_code == 401


def test_successful_login_clears_the_failure_count(client, verified, db_session_factory):
    fail_login(client, 9)
    client.post("/auth/login", json=LOGIN)

    assert fail_login(client, 9)[-1].status_code == 401
    with db_session_factory() as db:
        assert db.scalar(select(User)).locked_until is None


def test_password_reset_unlocks_the_account(client, outbox, verified):
    fail_login(client, 10)
    forgot(client)

    reset(client, outbox.reset_token_for(EMAIL))

    assert client.post("/auth/login", json={**LOGIN, "password": NEW_PASSWORD}).status_code == 200


def test_unknown_email_is_never_locked(client):
    assert {r.status_code for r in fail_login(client, 12, "nobody@example.com")} == {401}


def test_unknown_email_still_spends_a_password_check(client, monkeypatch):
    calls = []
    monkeypatch.setattr(auth_api, "verify_dummy_password", calls.append)

    fail_login(client, 1, "nobody@example.com")

    assert calls == ["wrong-pass1"]


# ---------- Rate limits on the endpoints ----------


@pytest.fixture
def five_per_minute(monkeypatch):
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 5)


def test_login_is_limited_per_email(client, five_per_minute):
    responses = fail_login(client, 6)

    assert [r.status_code for r in responses] == [401] * 5 + [429]
    assert int(responses[5].headers["Retry-After"]) > 0
    assert "Try again in" in responses[5].json()["detail"]


def test_login_is_limited_per_ip(client, five_per_minute):
    responses = [fail_login(client, 1, f"user{n}@example.com")[0] for n in range(6)]

    assert [r.status_code for r in responses] == [401] * 5 + [429]


def test_limit_lifts_after_a_minute(client, five_per_minute, clock):
    fail_login(client, 5)
    assert fail_login(client, 1)[0].status_code == 429

    clock[0] += 60

    assert fail_login(client, 1)[0].status_code == 401


def test_limited_attempts_do_not_count_towards_lockout(client, verified, five_per_minute, db_session_factory):
    ratelimit.limiter.reset()  # forget the fixture's own login

    fail_login(client, 8)

    with db_session_factory() as db:
        assert db.scalar(select(User)).failed_logins == 5


def test_register_is_limited(client, five_per_minute):
    statuses = [
        client.post("/auth/register", json={**REGISTER, "email": f"user{n}@example.com"}).status_code
        for n in range(6)
    ]

    assert statuses == [201] * 5 + [429]


def test_verify_is_limited_before_the_code_is_checked(client, outbox, five_per_minute):
    register(client)
    code = outbox.code_for(EMAIL)
    for _ in range(4):
        verify(client, wrong_code(code))
    verify(client, wrong_code(code))

    assert verify(client, code).status_code == 429


def test_forgot_password_is_limited_alike_for_known_and_unknown_emails(client, verified, five_per_minute, clock):
    known =[forgot(client).status_code for _ in range(6)]
    clock[0] += 60
    unknown = [forgot(client, "nobody@example.com").status_code for _ in range(6)]

    assert known == unknown == [200] * 5 + [429]


def test_spoofed_client_ip_gets_no_fresh_budget(client, five_per_minute):
    statuses = [
        client.post(
            "/auth/login",
            json={"email": f"user{n}@example.com", "password": "wrong-pass1"},
            headers={"X-Client-IP": f"203.0.113.{n}"},
        ).status_code
        for n in range(6)
    ]

    assert statuses == [401] * 5 + [429]


def test_each_browser_has_its_own_budget_behind_the_proxy(client, five_per_minute, monkeypatch):
    monkeypatch.setattr(settings, "proxy_shared_secret", "shared-secret")
    statuses = [
        client.post(
            "/auth/login",
            json={"email": f"user{n}@example.com", "password": "wrong-pass1"},
            headers={"X-Client-IP": f"203.0.113.{n}", "X-Proxy-Secret": "shared-secret"},
        ).status_code
        for n in range(6)
    ]

    assert statuses == [401] * 6
