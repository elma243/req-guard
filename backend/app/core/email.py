"""Sends auth emails over SMTP, or prints them to the console when SMTP is not configured (dev mode)."""

import html
import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import NamedTuple

from app.core.config import settings

logger = logging.getLogger(__name__)

SMTP_TIMEOUT_SECONDS = 15
SMTP_SSL_PORT = 465


class Email(NamedTuple):
    subject: str
    html: str
    text: str


def _send_over_smtp(message: EmailMessage) -> None:
    if settings.smtp_port == SMTP_SSL_PORT:
        with smtplib.SMTP_SSL(
            settings.smtp_host,
            settings.smtp_port,
            timeout=SMTP_TIMEOUT_SECONDS,
            context=ssl.create_default_context(),
        ) as server:
            _log_in_and_send(server, message)
        return
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT_SECONDS) as server:
        server.ehlo()
        # With a password TLS is compulsory: skipping it when the server does not offer it would expose the password
        if settings.smtp_user or server.has_extn("starttls"):
            server.starttls(context=ssl.create_default_context())
        _log_in_and_send(server, message)


def _log_in_and_send(server: smtplib.SMTP, message: EmailMessage) -> None:
    if settings.smtp_user:
        server.login(settings.smtp_user, settings.smtp_password)
    server.send_message(message)


def _print_to_console(to: str, email: Email) -> None:
    block = f"\n--- Email (SMTP not configured) ---\nTo: {to}\nSubject: {email.subject}\n\n{email.text}\n---\n"
    # Replacing non-ASCII keeps a Bangla name from crashing a Windows console; the code and link are ASCII
    print(block.encode("ascii", "replace").decode(), flush=True)


def deliver(to: str, email: Email) -> None:
    """Send one email. Runs as a background task, so a failure is logged, never raised to the user."""
    sender = settings.email_from or settings.smtp_user
    if not (settings.smtp_host and sender):
        _print_to_console(to, email)
        return

    message = EmailMessage()
    message["Subject"] = email.subject
    message["From"] = sender
    message["To"] = to
    message.set_content(email.text)
    message.add_alternative(email.html, subtype="html")
    try:
        _send_over_smtp(message)
    except (smtplib.SMTPException, OSError) as exc:
        # The subject and SMTP error carry neither the code nor the link
        logger.error("Could not send email %r: %s", email.subject, exc)


def _layout(name: str, intro: str, highlight: str, footer: str) -> str:
    """Branded frame. Tables and inline styles, because email clients ignore most other CSS."""
    return f"""\
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:Arial,Helvetica,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;padding:32px 12px;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             style="max-width:480px;background:#ffffff;border-radius:12px;overflow:hidden;">
        <tr><td style="background:#18181b;padding:20px 28px;color:#ffffff;font-size:18px;font-weight:bold;">
          <span style="color:#f59e0b;">&#9632;</span> ReqGuard
        </td></tr>
        <tr><td style="padding:28px;color:#27272a;font-size:15px;line-height:1.6;">
          <p style="margin:0 0 12px;">Hi {html.escape(name)},</p>
          <p style="margin:0 0 20px;">{intro}</p>
          {highlight}
          <p style="margin:20px 0 0;color:#71717a;font-size:13px;">{footer}</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""


def otp_email(name: str, code: str, minutes: int) -> Email:
    highlight = (
        '<p style="margin:0;padding:14px;background:#eef2ff;border-radius:8px;text-align:center;'
        f'color:#3730a3;font-size:30px;font-weight:bold;letter-spacing:8px;">{code}</p>'
    )
    return Email(
        subject="Your ReqGuard verification code",
        html=_layout(
            name,
            "Enter this code to verify your email and finish creating your account.",
            highlight,
            f"This code expires in {minutes} minutes. If you did not sign up, you can ignore this email.",
        ),
        text=(
            f"Hi {name},\n\nYour ReqGuard verification code is {code}\n\n"
            f"It expires in {minutes} minutes. If you did not sign up, you can ignore this email."
        ),
    )


def reset_email(name: str, url: str, minutes: int) -> Email:
    link = html.escape(url, quote=True)
    highlight = (
        f'<p style="margin:0;text-align:center;"><a href="{link}" style="display:inline-block;padding:12px 24px;'
        'background:#6366f1;border-radius:8px;color:#ffffff;font-weight:bold;text-decoration:none;">'
        "Reset password</a></p>"
    )
    return Email(
        subject="Reset your ReqGuard password",
        html=_layout(
            name,
            "We received a request to reset your password. Use the button below to choose a new one.",
            highlight,
            f"This link works once and expires in {minutes} minutes. "
            "If you did not ask for it, you can ignore this email; your password stays the same.",
        ),
        text=(
            f"Hi {name},\n\nReset your ReqGuard password with this link:\n{url}\n\n"
            f"It works once and expires in {minutes} minutes. "
            "If you did not ask for it, you can ignore this email; your password stays the same."
        ),
    )
