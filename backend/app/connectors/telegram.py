"""Telegram connector (Bot API getUpdates).

Imports text messages and voice notes (voice messages, audio files, audio documents) from groups, supergroups and
channels the bot was added to. Audio is downloaded later with getFile; bots can only download files up to 20 MB. The bot's privacy
mode must be off (BotFather → /setprivacy → Disable) so it receives every group message.

How getUpdates works, and what that means here:
- Telegram keeps updates for about 24 hours only; sync at least once a day.
- Calling getUpdates with offset = last update_id + 1 confirms (deletes) all older updates, for every
  chat. The last update_id is stored in Source.config["state"] after the messages are saved.
  Updates for chats outside this source's chat filter are confirmed too and not imported anywhere,
  so use one Telegram source per bot.
- getUpdates does not work while a webhook is set for the bot.
"""

from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel

from app.connectors.base import AudioAttachment, NormalizedMessage, audio_mime, voice_message
from app.connectors.http import MAX_PAGES, ApiConnector, ConnectorError, download, json_body, send

SOURCE = "telegram"
SERVICE = "Telegram"
AUTH_HINT = "Check TELEGRAM_BOT_TOKEN in backend/.env."
PAGE_SIZE = 100
GROUP_CHATS = {"group", "supergroup", "channel"}
# The Bot API does not let bots download larger files
BOT_DOWNLOAD_LIMIT = 20 * 1024 * 1024
BOT_LIMIT_NOTE = "Telegram bots can only download files up to 20 MB, so this voice note was not transcribed."


class TelegramSettings(BaseModel):
    # Only import these chats; empty = every group the bot is in
    chat_ids: list[int] = []


def _author(message: dict) -> str:
    sender = message.get("from") or {}
    name = " ".join(p for p in (sender.get("first_name"), sender.get("last_name")) if p)
    if name:
        return name
    if sender.get("username"):
        return f"@{sender['username']}"
    # Channel posts have no sender, only the channel
    return str(message.get("author_signature") or (message.get("sender_chat") or {}).get("title") or "")


def _audio(message: dict) -> AudioAttachment | None:
    """The voice message, audio file or audio document of a message, if it has one."""
    for key, default_ext, default_mime in (
        ("voice", ".ogg", "audio/ogg"),
        ("audio", ".mp3", ""),
        ("document", "", ""),
    ):
        item = message.get(key)
        if not isinstance(item, dict) or not item.get("file_id"):
            continue
        filename = str(item.get("file_name") or f"{key}-{message.get('message_id')}{default_ext}")
        mime = audio_mime(filename, item.get("mime_type") or default_mime)
        if mime is None:
            continue  # a document that is not audio
        size = item.get("file_size") if isinstance(item.get("file_size"), int) else None
        return AudioAttachment(
            file_id=str(item["file_id"]),
            filename=filename,
            mime_type=mime,
            size=size,
            duration=item.get("duration"),
            ref={"kind": key, "file_unique_id": item.get("file_unique_id")},
            skip_reason=BOT_LIMIT_NOTE if size is not None and size > BOT_DOWNLOAD_LIMIT else None,
        )
    return None


class TelegramConnector(ApiConnector):
    source = SOURCE
    label = "Telegram"
    required_env = ("TELEGRAM_BOT_TOKEN",)
    Settings = TelegramSettings

    def __init__(
        self,
        settings: TelegramSettings,
        *,
        token: str,
        client: httpx.Client | None = None,
        state: dict | None = None,
    ):
        super().__init__(client, state)
        self.settings = settings
        # The URLs contain the token: they are never logged, stored or put in an error message (see http.py)
        self.api = f"https://api.telegram.org/bot{token}"
        self.url = f"{self.api}/getUpdates"
        self.file_url = f"https://api.telegram.org/file/bot{token}"

    def _updates(self, offset: int | None) -> list[dict]:
        params: dict[str, Any] = {
            "limit": PAGE_SIZE,
            "timeout": 0,
            "allowed_updates": '["message","channel_post"]',
        }
        if offset is not None:
            params["offset"] = offset
        response = send(
            self.client, "GET", self.url, service=SERVICE, auth_hint=AUTH_HINT, params=params, allow_status=(404, 409)
        )
        if response.status_code == 404:
            # Telegram answers 404 when the token has a wrong format
            raise ConnectorError(f"Telegram rejected the bot token. {AUTH_HINT}")
        if response.status_code == 409:
            raise ConnectorError(
                "A webhook is set for this Telegram bot, so messages cannot be read with getUpdates. "
                "Remove it with deleteWebhook."
            )
        data = json_body(response, SERVICE)
        if not data.get("ok"):
            raise ConnectorError(f"Telegram API error: {data.get('description') or 'unknown error'}")
        return data.get("result") or []

    def _to_messages(self, message: dict) -> list[NormalizedMessage]:
        """The text message (text or caption) and the voice message of one Telegram message, if any."""
        chat = message.get("chat") or {}
        if chat.get("type") not in GROUP_CHATS:
            return []
        if self.settings.chat_ids and chat.get("id") not in self.settings.chat_ids:
            return []
        external_id = f"{chat['id']}:{message['message_id']}"
        channel = str(chat.get("title") or chat["id"])
        author = _author(message)
        sent_at = datetime.fromtimestamp(int(message["date"]), tz=timezone.utc)
        raw = {"chat_id": chat["id"], "message": message}
        found: list[NormalizedMessage] = []
        text = str(message.get("text") or message.get("caption") or "").strip()
        if text:
            found.append(
                NormalizedMessage(
                    source=SOURCE, external_id=external_id, channel=channel, author=author, sent_at=sent_at,
                    text=text, raw=raw,
                )
            )
        audio = _audio(message)
        if audio is not None:
            found.append(
                voice_message(
                    source=SOURCE, parent_id=external_id, channel=channel, author=author, sent_at=sent_at,
                    attachment=audio, raw=raw,
                )
            )
        return found

    def download_audio(self, attachment: AudioAttachment) -> bytes:
        """getFile gives the file's path (valid for at least an hour), then the file itself is downloaded."""
        if attachment.data is not None:
            return attachment.data
        response = send(
            self.client, "GET", f"{self.api}/getFile", service=SERVICE, auth_hint=AUTH_HINT,
            params={"file_id": attachment.file_id}, allow_status=(400,),
        )
        data = json_body(response, SERVICE)
        path = (data.get("result") or {}).get("file_path") if data.get("ok") else None
        if not path:
            description = str(data.get("description") or "unknown error")
            if "too big" in description.lower():
                raise ConnectorError(BOT_LIMIT_NOTE)
            raise ConnectorError(f"Telegram could not give this voice note: {description}")
        return download(self.client, f"{self.file_url}/{path}", service=SERVICE, auth_hint=AUTH_HINT)

    def fetch(self, since: datetime | None = None) -> list[NormalizedMessage]:
        """`since` is not used: the stored update_id decides what is new."""
        last = self.state.get("last_update_id")
        messages: list[NormalizedMessage] = []
        for _ in range(MAX_PAGES):
            updates = self._updates(last + 1 if isinstance(last, int) else None)
            if not updates:
                break
            for update in updates:
                last = max(int(update["update_id"]), last if isinstance(last, int) else -1)
                message = update.get("message") or update.get("channel_post")
                if message:
                    messages.extend(self._to_messages(message))
            if len(updates) < PAGE_SIZE:
                break
        if isinstance(last, int):
            self.state["last_update_id"] = last
        return sorted(messages, key=lambda m: m.sent_at)
