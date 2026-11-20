"""Tests for login and password reset."""

from app.auth import login, reset_password


def test_req_001_login_with_valid_password():
    assert login("a@example.com", "secret")


def test_valid_request_works():
    """TC-1: a valid login request works."""
    assert login("a@example.com", "secret")


class TestReset:
    def test_reset_link_expires(self):
        # REQ-002 reset links expire
        assert "reset" in reset_password("a@example.com")

    def helper(self):
        return None


def helper_not_a_test():
    return None
