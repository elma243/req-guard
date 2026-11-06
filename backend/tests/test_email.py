"""The SMTP sender and the email templates. smtplib is always mocked: no email leaves the machine."""

import logging
import smtplib
from unittest.mock import MagicMock

import pytest

from app.core import email as mailer
from app.core.config import settings
from app.core.email import Email, otp_email, reset_email
from app.core.email import deliver as real_deliver  # conftest replaces mailer.deliver for every test

MESSAGE = Email(subject="Hello", html="<p>Hi</p>", text="Hi")


@pytest.fixture
def smtp_settings(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "smtp_user", "mailer@example.com")
    monkeypatch.setattr(settings, "smtp_password", "app-password")
    monkeypatch.setattr(settings, "email_from", "ReqGuard <no-reply@example.com>")


@pytest.fixture
def smtp(monkeypatch):
    """Mock of smtplib.SMTP; returns the object used inside the `with` block."""
    factory = MagicMock()
    monkeypatch.setattr(mailer.smtplib, "SMTP", factory)
    server = factory.return_value.__enter__.return_value
    server.has_extn.return_value = True
    return server


def test_tests_never_have_smtp_configured():
    assert settings.smtp_host == ""


def test_prints_to_console_when_smtp_is_not_configured(monkeypatch, capsys):
    monkeypatch.setattr(mailer.smtplib, "SMTP", MagicMock(side_effect=AssertionError("must not connect")))

    real_deliver("a@example.com", otp_email("Alice", "123456", 10))

    output = capsys.readouterr().out
    assert "a@example.com" in output
    assert "123456" in output


def test_console_survives_a_name_the_console_cannot_show(capsys):
    real_deliver("a@example.com", otp_email("জিনাইন", "123456", 10))

    output = capsys.readouterr().out
    assert "123456" in output
    output.encode("ascii")


def test_host_without_a_sender_falls_back_to_console(monkeypatch, capsys):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_user", "")
    monkeypatch.setattr(settings, "email_from", "")

    real_deliver("a@example.com", MESSAGE)

    assert "a@example.com" in capsys.readouterr().out


def test_sends_with_starttls_and_login(smtp_settings, smtp):
    real_deliver("a@example.com", MESSAGE)

    smtp.starttls.assert_called_once()
    smtp.login.assert_called_once_with("mailer@example.com", "app-password")
    sent = smtp.send_message.call_args.args[0]
    assert sent["To"] == "a@example.com"
    assert sent["From"] == "ReqGuard <no-reply@example.com>"
    assert sent["Subject"] == "Hello"
    assert sent.get_body(("html",)).get_content().strip() == "<p>Hi</p>"
    assert sent.get_body(("plain",)).get_content().strip() == "Hi"


def test_tls_is_started_before_the_password_is_sent(smtp_settings, smtp):
    real_deliver("a@example.com", MESSAGE)

    names = [call[0] for call in smtp.method_calls]
    assert names.index("starttls") < names.index("login")


def test_credentials_are_never_sent_without_tls(smtp_settings, smtp):
    smtp.has_extn.return_value = False  # a downgrade: the server stops offering STARTTLS
    smtp.starttls.side_effect = smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server.")

    real_deliver("a@example.com", MESSAGE)

    smtp.login.assert_not_called()
    smtp.send_message.assert_not_called()


def test_local_server_without_tls_or_login_still_works(smtp_settings, smtp, monkeypatch):
    monkeypatch.setattr(settings, "smtp_user", "")
    smtp.has_extn.return_value = False

    real_deliver("a@example.com", MESSAGE)

    smtp.starttls.assert_not_called()
    smtp.login.assert_not_called()
    smtp.send_message.assert_called_once()


def test_port_465_uses_implicit_tls(smtp_settings, monkeypatch):
    monkeypatch.setattr(settings, "smtp_port", 465)
    plain = MagicMock()
    secure = MagicMock()
    monkeypatch.setattr(mailer.smtplib, "SMTP", plain)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", secure)

    real_deliver("a@example.com", MESSAGE)

    plain.assert_not_called()
    server = secure.return_value.__enter__.return_value
    server.login.assert_called_once()
    server.send_message.assert_called_once()


@pytest.mark.parametrize(
    "error",
    [smtplib.SMTPAuthenticationError(535, b"Bad credentials"), TimeoutError("timed out"), OSError("no route")],
)
def test_failed_send_is_logged_not_raised_and_hides_the_code(smtp_settings, monkeypatch, caplog, error):
    monkeypatch.setattr(mailer.smtplib, "SMTP", MagicMock(side_effect=error))

    with caplog.at_level(logging.ERROR):
        real_deliver("a@example.com", otp_email("Alice", "123456", 10))

    assert "Could not send email" in caplog.text
    assert "123456" not in caplog.text


def test_otp_template_shows_code_and_expiry():
    email = otp_email("Alice", "012345", 10)

    assert "012345" in email.html and "012345" in email.text
    assert "10 minutes" in email.html and "10 minutes" in email.text
    assert "ReqGuard" in email.html


def test_reset_template_links_and_shows_expiry():
    url = "https://app.example.com/reset-password?token=abc&x=1"
    email = reset_email("Alice", url, 30)

    assert 'href="https://app.example.com/reset-password?token=abc&amp;x=1"' in email.html
    assert url in email.text
    assert "30 minutes" in email.html


def test_templates_escape_the_name():
    name = '<img src=x onerror="alert(1)">'

    for email in (otp_email(name, "123456", 10), reset_email(name, "https://x.example", 30)):
        assert "<img" not in email.html
        assert "&lt;img" in email.html
