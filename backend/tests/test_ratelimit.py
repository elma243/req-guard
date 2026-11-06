"""The sliding-window limiter and how it finds the client's address."""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core import ratelimit
from app.core.config import settings
from app.core.ratelimit import RateLimiter, client_ip, enforce, limit_auth


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(ratelimit, "monotonic", lambda: now[0])
    return now


def make_request(headers: dict[str, str] | None = None, host: str = "10.0.0.9") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw, "client": (host, 1234)})


def test_allows_up_to_the_limit_then_gives_the_wait(clock):
    limiter = RateLimiter()

    assert [limiter.hit("k", 2, 60) for _ in range(2)] == [0, 0]
    assert limiter.hit("k", 2, 60) == 60


def test_window_slides(clock):
    limiter = RateLimiter()
    limiter.hit("k", 2, 60)
    clock[0] += 30
    limiter.hit("k", 2, 60)

    clock[0] += 29
    assert limiter.hit("k", 2, 60) == 1  # first hit leaves in one second

    clock[0] += 1
    assert limiter.hit("k", 2, 60) == 0


def test_refused_attempts_do_not_extend_the_wait(clock):
    limiter = RateLimiter()
    limiter.hit("k", 1, 60)
    for _ in range(5):
        limiter.hit("k", 1, 60)

    clock[0] += 60
    assert limiter.hit("k", 1, 60) == 0


def test_keys_are_counted_separately(clock):
    limiter = RateLimiter()
    limiter.hit("a", 1, 60)

    assert limiter.hit("b", 1, 60) == 0


def test_sweep_drops_only_finished_keys(clock, monkeypatch):
    monkeypatch.setattr(ratelimit, "MAX_KEYS", 2)
    limiter = RateLimiter()
    limiter.hit("old", 1, 60)
    clock[0] += 50
    limiter.hit("recent", 1, 60)
    clock[0] += 20

    limiter.hit("new", 1, 60)

    assert limiter.hit("old", 1, 60) == 0  # forgotten
    assert limiter.hit("recent", 1, 60) > 0  # still counted


def test_enforce_answers_429_with_retry_after(clock):
    enforce("key", 1, 60)

    with pytest.raises(HTTPException) as caught:
        enforce("key", 1, 60, "Too many tries.")

    assert caught.value.status_code == 429
    assert caught.value.headers == {"Retry-After": "60"}
    assert caught.value.detail == "Too many tries. Try again in 60 seconds."


def test_last_second_reads_correctly(clock):
    enforce("key", 1, 60)
    clock[0] += 59.5

    with pytest.raises(HTTPException) as caught:
        enforce("key", 1, 60)

    assert caught.value.detail == "Too many attempts. Try again in 1 second."


def test_client_ip_is_the_socket_address_without_a_proxy():
    assert client_ip(make_request()) == "10.0.0.9"


def test_client_ip_trusts_the_header_with_the_right_secret(monkeypatch):
    monkeypatch.setattr(settings, "proxy_shared_secret", "shared-secret")
    request = make_request({"X-Client-IP": "203.0.113.7", "X-Proxy-Secret": "shared-secret"})

    assert client_ip(request) == "203.0.113.7"


@pytest.mark.parametrize(
    "secret, headers",
    [
        ("shared-secret", {"X-Client-IP": "203.0.113.7"}),
        ("shared-secret", {"X-Client-IP": "203.0.113.7", "X-Proxy-Secret": "wrong"}),
        ("", {"X-Client-IP": "203.0.113.7", "X-Proxy-Secret": ""}),
    ],
)
def test_client_ip_ignores_a_spoofed_header(monkeypatch, secret, headers):
    monkeypatch.setattr(settings, "proxy_shared_secret", secret)

    assert client_ip(make_request(headers)) == "10.0.0.9"


def test_limit_auth_counts_per_email_across_addresses(monkeypatch, clock):
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 2)
    limit_auth(make_request(host="10.0.0.1"), "login", "a@example.com")
    limit_auth(make_request(host="10.0.0.2"), "login", "a@example.com")

    with pytest.raises(HTTPException):
        limit_auth(make_request(host="10.0.0.3"), "login", "a@example.com")


def test_limit_auth_counts_per_address_across_emails(monkeypatch, clock):
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 2)
    limit_auth(make_request(), "login", "a@example.com")
    limit_auth(make_request(), "login", "b@example.com")

    with pytest.raises(HTTPException):
        limit_auth(make_request(), "login", "c@example.com")


def test_each_action_has_its_own_budget(monkeypatch, clock):
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 1)
    limit_auth(make_request(), "login", "a@example.com")

    limit_auth(make_request(), "register", "a@example.com")
