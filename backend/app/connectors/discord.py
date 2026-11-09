"""Discord connector (REST API v10, bot token).

Lists the text channels of one server (guild) and reads their messages forward with `after=<id>`.
The newest message id per channel is stored in Source.config["state"], so the next sync only reads
new messages. The bot needs the "Message Content" intent, and View Channel + Read Message History
permissions in the channels.

Voice notes: Discord voice messages (flag IS_VOICE_MESSAGE) and audio attachments (.ogg, .mp3, .m4a...) become
voice messages. Attachment URLs are signed and expire; when a download fails, the message is read again for a
fresh URL.
"""

import re
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, field_validator

from app.connectors.base import AudioAttachment, NormalizedMessage, as_utc, audio_mime, voice_message
from app.connectors.http import MAX_PAGES, ApiConnector, AudioTooLargeError, ConnectorError, download, json_body, send

SOURCE = "discord"
SERVICE = "Discord"
API = "https://discord.com/api/v10"
AUTH_HINT = (
    "Check DISCORD_BOT_TOKEN in backend/.env and that the bot can view the channel and read message history."
)
PAGE_SIZE = 100
TEXT_CHANNEL_TYPES = {0, 5}  # GUILD_TEXT, GUILD_ANNOUNCEMENT
MESSAGE_TYPES = {0, 19}  # DEFAULT, REPLY (joins, pins, boosts... are skipped)
IS_VOICE_MESSAGE = 1 << 13


class DiscordSettings(BaseModel):
    guild_id: str
    # Channel names (with or without #) or ids; empty = every text channel the bot can read
    channels: list[str] = []

    @field_validator("guild_id")
    @classmethod
    def check_guild(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"\d{15,21}", value):
            raise ValueError("Server (guild) id is a long number; copy it with Developer Mode → Copy Server ID")
        return value

    @field_validator("channels")
    @classmethod
    def clean(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(c.strip().lstrip("#").strip() for c in value if c.strip().lstrip("#").strip()))


def _author(message: dict) -> str:
    author = message.get("author") or {}
    return str(author.get("global_name") or author.get("username") or author.get("id") or "")


def _audio(attachment: dict, voice_flag: bool, channel_id: str, message_id: str) -> AudioAttachment | None:
    filename = str(attachment.get("filename") or "voice-message.ogg")
    mime = audio_mime(filename, attachment.get("content_type"))
    if mime is None and voice_flag:
        mime = "audio/ogg"  # voice messages are Ogg Opus
    if mime is None or not attachment.get("id"):
        return None
    return AudioAttachment(
        file_id=str(attachment["id"]),
        filename=filename,
        mime_type=mime,
        size=attachment.get("size") if isinstance(attachment.get("size"), int) else None,
        duration=attachment.get("duration_secs"),
        url=attachment.get("url"),
        ref={"channel_id": channel_id, "message_id": message_id, "voice_message": voice_flag},
    )


class DiscordConnector(ApiConnector):
    source = SOURCE
    label = "Discord"
    required_env = ("DISCORD_BOT_TOKEN",)
    Settings = DiscordSettings

    def __init__(
        self,
        settings: DiscordSettings,
        *,
        token: str,
        client: httpx.Client | None = None,
        state: dict | None = None,
    ):
        super().__init__(client, state)
        self.settings = settings
        self.headers = {
            "Authorization": f"Bot {token}",
            # Discord asks bots to identify themselves this way
            "User-Agent": "DiscordBot (https://github.com/reqguard, 0.1)",
        }

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = send(
            self.client, "GET", f"{API}{path}", service=SERVICE, auth_hint=AUTH_HINT, params=params,
            headers=self.headers,
        )
        return json_body(response, SERVICE)

    def _selected_channels(self) -> list[dict]:
        channels = [
            c for c in self._get(f"/guilds/{self.settings.guild_id}/channels") if c.get("type") in TEXT_CHANNEL_TYPES
        ]
        if not self.settings.channels:
            return channels
        by_key = {c["id"]: c for c in channels} | {c.get("name", ""): c for c in channels}
        missing = [name for name in self.settings.channels if name not in by_key]
        if missing:
            raise ConnectorError(f"Discord channel(s) not found: {', '.join('#' + m for m in missing)}.")
        return [by_key[name] for name in self.settings.channels]

    def _channel_messages(self, channel_id: str, after: str) -> list[dict]:
        found: list[dict] = []
        for _ in range(MAX_PAGES):
            page = self._get(f"/channels/{channel_id}/messages", {"limit": PAGE_SIZE, "after": after})
            if not page:
                break
            found.extend(page)
            # Order inside a page is not guaranteed to be oldest first; snowflake ids grow with time
            after = str(max(int(m["id"]) for m in page))
            if len(page) < PAGE_SIZE:
                break
        return found

    def _to_messages(self, message: dict, channel: dict, channel_id: str) -> list[NormalizedMessage]:
        """The text message and one voice message per audio attachment."""
        message_id = str(message["id"])
        fields = {
            "source": SOURCE,
            "channel": f"#{channel.get('name', channel_id)}",
            "author": _author(message),
            "sent_at": as_utc(datetime.fromisoformat(message["timestamp"])),
        }
        raw = {"guild_id": self.settings.guild_id, "channel_id": channel_id, "message": message}
        found: list[NormalizedMessage] = []
        text = str(message.get("content") or "").strip()
        if text:
            found.append(NormalizedMessage(external_id=message_id, text=text, raw=raw, **fields))
        voice_flag = bool(int(message.get("flags") or 0) & IS_VOICE_MESSAGE)
        for attachment in message.get("attachments") or []:
            audio = _audio(attachment, voice_flag, channel_id, message_id)
            if audio is not None:
                found.append(voice_message(parent_id=message_id, attachment=audio, raw=raw, **fields))
        return found

    def download_audio(self, attachment: AudioAttachment) -> bytes:
        """The CDN URL from the sync; if it expired (or fails), read the message again for a fresh one."""
        if attachment.data is not None:
            return attachment.data
        if attachment.url:
            try:
                return download(self.client, attachment.url, service=SERVICE)
            except AudioTooLargeError:
                raise
            except ConnectorError:
                pass
        channel_id, message_id = attachment.ref.get("channel_id"), attachment.ref.get("message_id")
        if not channel_id or not message_id:
            raise ConnectorError("Discord gave no download address for this voice note.")
        message = self._get(f"/channels/{channel_id}/messages/{message_id}")
        fresh = next(
            (a.get("url") for a in message.get("attachments") or [] if str(a.get("id")) == attachment.file_id), None
        )
        if not fresh:
            raise ConnectorError("This voice note is no longer on Discord.")
        return download(self.client, fresh, service=SERVICE)

    def fetch(self, since: datetime | None = None) -> list[NormalizedMessage]:
        """`since` is not used: the stored message id per channel decides what is new."""
        last_ids: dict[str, str] = dict(self.state.get("last_message_ids") or {})
        messages: list[NormalizedMessage] = []
        human_messages = 0
        for channel in self._selected_channels():
            channel_id = str(channel["id"])
            raw_messages = self._channel_messages(channel_id, last_ids.get(channel_id, "0"))
            if raw_messages:
                last_ids[channel_id] = str(max(int(m["id"]) for m in raw_messages))
            for message in raw_messages:
                if message.get("type") not in MESSAGE_TYPES or (message.get("author") or {}).get("bot"):
                    continue
                human_messages += 1
                messages.extend(self._to_messages(message, channel, channel_id))
        if human_messages and not messages:
            raise ConnectorError(
                "Discord returned messages without text. Turn on the Message Content intent for the bot "
                "(Developer Portal → Bot → Privileged Gateway Intents)."
            )
        self.state["last_message_ids"] = last_ids
        return sorted(messages, key=lambda m: m.sent_at)
