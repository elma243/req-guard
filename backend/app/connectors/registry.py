"""The API connectors by source type, whether each is configured, and how to build one for a Source.

Tokens come from backend/.env (Jira, Slack, Telegram, Discord, Google OAuth client) or are decrypted
from the Source (Gmail refresh token). Only the *names* of missing variables leave this module.
"""

from typing import Any

from app.connectors.discord import DiscordConnector
from app.connectors.gmail import GmailConnector
from app.connectors.http import ApiConnector, NeedsAuthError, NotConfiguredError
from app.connectors.jira import JiraConnector, JiraIssueCreator
from app.connectors.slack import SlackConnector
from app.connectors.telegram import TelegramConnector
from app.core.config import settings
from app.core.crypto import decrypt, encryption_configured
from app.models import Source

API_CONNECTORS: dict[str, type[ApiConnector]] = {
    c.source: c for c in (JiraConnector, SlackConnector, GmailConnector, TelegramConnector, DiscordConnector)
}


def _env(name: str) -> str:
    return str(getattr(settings, name.lower(), None) or "").strip()


def missing_env(source_type: str) -> list[str]:
    """Names of the .env variables this connector still needs (empty list = configured)."""
    missing = [name for name in API_CONNECTORS[source_type].required_env if not _env(name)]
    if "TOKEN_ENCRYPTION_KEY" in API_CONNECTORS[source_type].required_env and "TOKEN_ENCRYPTION_KEY" not in missing:
        if not encryption_configured():  # set, but not a valid Fernet key
            missing.append("TOKEN_ENCRYPTION_KEY")
    return missing


def not_configured_message(missing: list[str]) -> str:
    return f"Not configured: set {', '.join(missing)} in backend/.env."


def validate_settings(source_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """User-entered settings checked by the connector's schema. Raises pydantic.ValidationError."""
    return API_CONNECTORS[source_type].Settings.model_validate(data).model_dump()


def needs_auth(source: Source) -> bool:
    """Gmail sources need the user to connect a Google account first."""
    return source.type == GmailConnector.source and not source.credentials_encrypted


def build_connector(source: Source) -> ApiConnector:
    """A ready connector for this source. Raises NotConfiguredError / NeedsAuthError."""
    missing = missing_env(source.type)
    if missing:
        raise NotConfiguredError(not_configured_message(missing))
    config = source.config or {}
    cls = API_CONNECTORS[source.type]
    user_settings = cls.Settings.model_validate(config.get("settings") or {})
    state = config.get("state") or {}

    if cls is JiraConnector:
        return JiraConnector(
            user_settings,
            base_url=_env("JIRA_BASE_URL"),
            email=_env("JIRA_EMAIL"),
            api_token=_env("JIRA_API_TOKEN"),
            state=state,
        )
    if cls is SlackConnector:
        return SlackConnector(user_settings, token=_env("SLACK_BOT_TOKEN"), state=state)
    if cls is TelegramConnector:
        return TelegramConnector(user_settings, token=_env("TELEGRAM_BOT_TOKEN"), state=state)
    if cls is DiscordConnector:
        return DiscordConnector(user_settings, token=_env("DISCORD_BOT_TOKEN"), state=state)
    if needs_auth(source):
        raise NeedsAuthError("Connect a Google account for this source first.")
    return GmailConnector(
        user_settings,
        client_id=_env("GOOGLE_CLIENT_ID"),
        client_secret=_env("GOOGLE_CLIENT_SECRET"),
        refresh_token=decrypt(source.credentials_encrypted or ""),
        state=state,
    )


def jira_base_url() -> str | None:
    """For links to Jira issues; the URL is not a secret."""
    return _env("JIRA_BASE_URL").rstrip("/") or None


def jira_issue_creator() -> JiraIssueCreator:
    """Creates issues for "Push to Jira" with the .env credentials. Raises NotConfiguredError."""
    missing = missing_env(JiraConnector.source)
    if missing:
        raise NotConfiguredError(not_configured_message(missing))
    return JiraIssueCreator(base_url=_env("JIRA_BASE_URL"), email=_env("JIRA_EMAIL"), api_token=_env("JIRA_API_TOKEN"))
