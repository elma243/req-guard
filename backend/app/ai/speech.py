"""Voice notes: one shared pipeline that downloads, transcribes and stores the audio of every source.

Connectors only report audio attachments (app/connectors/base.py); the importer stores each one as a voice message
with transcript_status "pending" (and its audio in voice_audio when the upload carried it). This module:

- `GroqWhisper`: Groq's OpenAI-compatible /audio/transcriptions endpoint over httpx (no SDK). It always sends
  WHISPER_MODEL (whisper-large-v3, the better model for Bangla); WHISPER_FALLBACK_MODEL (whisper-large-v3-turbo)
  is asked only when large-v3 is unavailable (busy, rate limited, timed out). A short prompt with project words
  in Bengali script and English helps the spelling and the script. The answer is validated with a Pydantic model.
- Languages (`transcribe_with_languages`): the project's voice language is sent as `language` (Auto sends none).
  In Auto mode Whisper often hears Bangla as Hindi (Devanagari text) or another neighbour language. When the
  detected language is not one of VOICE_ALLOWED_LANGUAGES (default Bangla and English), or the text contains
  Devanagari, the audio is transcribed once more with language="bn". `raw["voice"]` records the requested and
  detected language, whether that retry happened and the final language ("bangla" / "english").
- `retranscribe`: the "Re-transcribe as Bangla / English" button. The old transcript is kept in
  `raw["voice"]["history"]`.
- `RateLimiter`: at most GROQ_AUDIO_RPM requests per 60 seconds for the whole process (Groq free tier: 20).
  A 429/503 is retried after Retry-After or 2/4/8 seconds; after that the message goes back to the queue with a
  later `transcribe_after`, and after MAX_ATTEMPTS it is marked failed (the Retry button queues it again).
- `TranscriptionWorker`: like the auto-sync scheduler, an asyncio task in the API process (one backend worker)
  that claims pending messages one by one, gets their audio (stored, or downloaded again with the source's
  connector), transcribes them, stores the transcript as the message text and deletes the audio unless
  KEEP_AUDIO=true. When the project has auto-extract on, the new transcripts are extracted and validated.

Never logged: the API key and the transcript (it contains client messages).
"""

import asyncio
import logging
import re
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import PurePath

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.connectors.base import MAX_AUDIO_BYTES, TOO_LARGE_NOTE, AudioAttachment
from app.connectors.http import AudioTooLargeError, ConnectorError
from app.connectors.registry import API_CONNECTORS, build_connector
from app.core.config import settings
from app.core.crypto import DecryptionError
from app.core.db import SessionLocal
from app.models import Message, Project, Source, VoiceAudio
from app.models.message import (
    KIND_VOICE,
    TRANSCRIPT_DONE,
    TRANSCRIPT_FAILED,
    TRANSCRIPT_PENDING,
    TRANSCRIPT_RUNNING,
    TRANSCRIPT_SKIPPED,
)

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
# Common words of the client projects, so Whisper spells them the same way (and keeps "bKash", "OTP"). The Bengali
# script words also steer Bangla speech to Bengali script instead of Devanagari.
PROMPT_EN = "login, dashboard, order, payment, report, OTP, admin, bKash"
PROMPT = f"অর্ডার, লগইন, ড্যাশবোর্ড, পেমেন্ট, রিপোর্ট, {PROMPT_EN}"

# Whisper's language names → codes, for the neighbours it confuses with Bangla
LANGUAGE_CODES = {
    "bengali": "bn", "bangla": "bn", "english": "en", "hindi": "hi", "urdu": "ur", "nepali": "ne",
    "assamese": "as", "marathi": "mr", "punjabi": "pa", "gujarati": "gu", "oriya": "or", "odia": "or",
    "sanskrit": "sa", "sinhala": "si", "tamil": "ta", "telugu": "te", "kannada": "kn", "malayalam": "ml",
}
# Final language shown for a voice note
LANGUAGE_LABELS = {"bn": "bangla", "en": "english"}
# Devanagari letters (Hindi, Marathi, Nepali...): Bangla heard as Hindi comes back in this script
DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
TIMEOUT_SECONDS = 120.0

# Retries of one request on 429/503: waits before retry 1, 2 and 3 when there is no Retry-After header
BACKOFF_SECONDS = (2.0, 4.0, 8.0)
MAX_WAIT_SECONDS = 60.0
# After the retries the message is queued again this many seconds later (by attempt), at most MAX_ATTEMPTS times
REQUEUE_SECONDS = (60, 120, 300, 600)
MAX_ATTEMPTS = 5
# A "transcribing" claim older than this is stale (the process stopped) and is taken again
CLAIM_TIMEOUT = timedelta(minutes=15)

TICK_SECONDS = 5
MAX_PER_TICK = 10

# Extensions Groq accepts; others are renamed after their MIME type (.opus is an Ogg file)
GROQ_EXTENSIONS = {".flac", ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".ogg", ".wav", ".webm"}
EXTENSION_FOR_MIME = {
    "audio/ogg": ".ogg",
    "audio/opus": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/m4a": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/webm": ".webm",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
}

NOT_CONFIGURED = "Voice notes cannot be transcribed: set GROQ_API_KEY in backend/.env, then click Retry."
NO_AUDIO = "The audio of this voice note is no longer available. Upload the file again."
UNEXPECTED = "Unexpected error while transcribing. See the backend log."

# Replaced in tests so waits do not really happen
sleep = time.sleep
clock = time.monotonic


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Groq Whisper
# ---------------------------------------------------------------------------


class TranscriptionError(Exception):
    """The audio could not be transcribed; the message is marked failed and can be retried."""


class TranscriptionUnavailable(TranscriptionError):
    """Groq is busy or rate limited; the message is queued again for later."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class TranscriptionNotConfigured(TranscriptionError):
    """GROQ_API_KEY is not set."""


class Transcript(BaseModel):
    """Groq's verbose_json answer (only the fields we keep)."""

    text: str
    language: str | None = None
    duration: float | None = None


@dataclass
class TranscriptResult:
    text: str
    language: str | None
    duration: float | None
    model: str


class RateLimiter:
    """At most `per_minute()` requests in any 60 seconds, for every thread of the process."""

    def __init__(self, per_minute: Callable[[], int] = lambda: settings.groq_audio_rpm):
        self.per_minute = per_minute
        self._times: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            limit = max(1, self.per_minute())
            while True:
                now = clock()
                while self._times and self._times[0] <= now - 60.0:
                    self._times.popleft()
                if len(self._times) < limit:
                    self._times.append(now)
                    return
                sleep(self._times[0] + 60.0 - now)

    def reset(self) -> None:
        with self._lock:
            self._times.clear()


limiter = RateLimiter()


def upload_name(filename: str, mime_type: str) -> tuple[str, str]:
    """File name and type sent to Groq: it reads the type from the extension, so .opus is sent as .ogg."""
    path = PurePath(filename or "voice")
    ext = path.suffix.lower()
    if ext == ".opus":
        return f"{path.stem}.ogg", "audio/ogg"
    if ext in GROQ_EXTENSIONS:
        return path.name, mime_type
    new_ext = EXTENSION_FOR_MIME.get(mime_type.lower())
    return (f"{path.stem}{new_ext}", mime_type) if new_ext else (path.name, mime_type)


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers.get("Retry-After", "").strip())
    except ValueError:
        return None


def _error_detail(response: httpx.Response) -> str:
    try:
        error = response.json().get("error") or {}
    except (ValueError, AttributeError):
        return ""
    return str(error.get("message") or "") if isinstance(error, dict) else ""


class GroqWhisper:
    """Transcribes audio with Groq Whisper; the first model that answers wins."""

    def __init__(self, api_key: str, models: list[str], client: httpx.Client | None = None):
        self._api_key = api_key
        self.models = [m for m in dict.fromkeys(models) if m]
        self._own_client = client is None
        self.client = client or httpx.Client(timeout=httpx.Timeout(TIMEOUT_SECONDS))

    def close(self) -> None:
        if self._own_client:
            self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _safe(self, text: str) -> str:
        return text.replace(self._api_key, "***") if self._api_key else text

    def transcribe(self, data: bytes, filename: str, mime_type: str, language: str | None) -> TranscriptResult:
        """The first model (large-v3); the next one only when it is unavailable. Other errors fail at once."""
        last: TranscriptionError | None = None
        for model in self.models:
            try:
                return self._transcribe(model, data, filename, mime_type, language)
            except TranscriptionUnavailable as exc:
                logger.warning("speech model=%s unavailable: %s", model, exc)
                last = exc
        raise last or TranscriptionError("No Whisper model is configured (WHISPER_MODEL).")

    def _transcribe(
        self, model: str, data: bytes, filename: str, mime_type: str, language: str | None
    ) -> TranscriptResult:
        name, mime = upload_name(filename, mime_type)
        form = {"model": model, "response_format": "verbose_json", "temperature": "0", "prompt": prompt_for(language)}
        if language:
            form["language"] = language
        retries = 0
        while True:
            limiter.acquire()
            started = time.perf_counter()
            try:
                response = self.client.post(
                    GROQ_URL,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    data=form,
                    files={"file": (name, data, mime)},
                )
            except httpx.TimeoutException:
                raise TranscriptionUnavailable("Groq did not answer in time.") from None
            except httpx.HTTPError:
                raise TranscriptionUnavailable("Could not reach Groq. Check the internet connection.") from None
            status = response.status_code
            logger.info(
                "speech http model=%s attempt=%d status=%d bytes=%d elapsed_ms=%d",
                model, retries + 1, status, len(data), (time.perf_counter() - started) * 1000,
            )
            if status == 429 or status >= 500:
                delay = _retry_after(response)
                if retries == len(BACKOFF_SECONDS) or (delay or 0) > MAX_WAIT_SECONDS:
                    problem = "rate limit reached" if status == 429 else f"is busy (HTTP {status})"
                    raise TranscriptionUnavailable(f"Groq {problem}. It will be tried again later.", delay)
                sleep(BACKOFF_SECONDS[retries] if delay is None else delay)
                retries += 1
                continue
            break

        detail = self._safe(_error_detail(response))
        if status in (401, 403):
            raise TranscriptionNotConfigured(f"Groq rejected GROQ_API_KEY (HTTP {status}). Check backend/.env.")
        if status == 413:
            raise TranscriptionError(TOO_LARGE_NOTE)
        if status >= 400:
            hint = f": {detail}" if detail else ""
            raise TranscriptionError(f"Groq could not transcribe this audio (HTTP {status}){hint}.")
        try:
            answer = Transcript.model_validate(response.json())
        except (ValueError, ValidationError):
            raise TranscriptionError("Groq returned an answer that is not a transcript.") from None
        return TranscriptResult(
            text=answer.text.strip(),
            language=(answer.language or "").strip().lower() or None,
            duration=answer.duration,
            model=model,
        )


def prompt_for(language: str | None) -> str:
    """English notes get only the English words; Bangla and Auto get both scripts."""
    return PROMPT_EN if language == "en" else PROMPT


def language_code(language: str | None) -> str | None:
    """"bengali" / "bn" → "bn"; None when unknown."""
    value = (language or "").strip().lower()
    if not value:
        return None
    return value if len(value) <= 3 else LANGUAGE_CODES.get(value)


def language_label(code: str | None, detected: str | None = None) -> str | None:
    """Final language label: "bangla", "english", else Whisper's name for it."""
    return LANGUAGE_LABELS.get(code or "") or ((detected or "").strip().lower() or None)


def allowed_languages() -> list[str]:
    codes = [c.strip().lower() for c in settings.voice_allowed_languages.split(",") if c.strip()]
    return codes or ["bn", "en"]


def retry_language(allowed: list[str]) -> str:
    """Language for the second try in Auto mode: Bangla when allowed (the language Whisper mistakes)."""
    return "bn" if "bn" in allowed else next((c for c in allowed if c != "en"), allowed[0])


@dataclass
class Transcription:
    """A transcript with its language history, as stored in raw["voice"]."""

    text: str
    model: str
    duration: float | None
    language: str | None  # final label: "bangla" / "english" / Whisper's name
    requested_language: str  # "auto", "bn" or "en"
    detected_language: str | None  # Whisper's answer to the first request
    retried: bool = False
    retried_as: str | None = None
    retry_reason: str | None = None

    def fields(self) -> dict:
        return {
            "language": self.language,
            "requested_language": self.requested_language,
            "detected_language": self.detected_language,
            "retried": self.retried,
            "retried_as": self.retried_as,
            "retry_reason": self.retry_reason,
            "model": self.model,
        }


def transcribe_with_languages(
    transcriber: "GroqWhisper",
    data: bytes,
    filename: str,
    mime_type: str,
    requested: str | None,
    allowed: list[str] | None = None,
) -> Transcription:
    """Transcribe; in Auto mode retry once with language="bn" when Whisper picked a language that is not allowed
    or wrote Devanagari. A requested language (project setting or Re-transcribe) is never second-guessed."""
    allowed = allowed or allowed_languages()
    first = transcriber.transcribe(data, filename, mime_type, requested)
    detected_code = language_code(first.language)
    if requested:
        return Transcription(
            text=first.text, model=first.model, duration=first.duration,
            language=language_label(requested, first.language), requested_language=requested,
            detected_language=first.language,
        )

    reason = None
    if DEVANAGARI_RE.search(first.text):
        reason = "Devanagari text"
    elif first.language and detected_code not in allowed:
        reason = f"detected {first.language}"
    if reason is None:
        return Transcription(
            text=first.text, model=first.model, duration=first.duration,
            language=language_label(detected_code, first.language), requested_language="auto",
            detected_language=first.language,
        )

    target = retry_language(allowed)
    logger.info("speech retry language=%s reason=%s", target, reason)
    second = transcriber.transcribe(data, filename, mime_type, target)
    return Transcription(
        text=second.text, model=second.model,
        duration=second.duration if second.duration is not None else first.duration,
        language=language_label(target, second.language), requested_language="auto",
        detected_language=first.language, retried=True, retried_as=target, retry_reason=reason,
    )


def get_transcriber() -> GroqWhisper:
    key = str(settings.groq_api_key or "").strip()
    if not key:
        raise TranscriptionNotConfigured(NOT_CONFIGURED)
    return GroqWhisper(key, [settings.whisper_model, settings.whisper_fallback_model])


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------


def _due(now: datetime):
    """Pending messages whose backoff is over, and stale claims."""
    return or_(
        and_(
            Message.transcript_status == TRANSCRIPT_PENDING,
            or_(Message.transcribe_after.is_(None), Message.transcribe_after <= now),
        ),
        and_(Message.transcript_status == TRANSCRIPT_RUNNING, Message.transcribe_after < now - CLAIM_TIMEOUT),
    )


def claim_next(db: Session, now: datetime) -> Message | None:
    """Claim the oldest due voice message (pending → transcribing) and commit. None when nothing is due.

    The claim is a conditional UPDATE, so a message is never transcribed twice at the same time.
    """
    for _ in range(3):  # another claimer may win the race for the first candidate
        candidate = db.scalar(
            select(Message.id)
            .where(Message.kind == KIND_VOICE, _due(now))
            .order_by(Message.sent_at, Message.id)
            .limit(1)
        )
        if candidate is None:
            return None
        claimed = db.execute(
            update(Message)
            .where(Message.id == candidate, _due(now))
            .values(transcript_status=TRANSCRIPT_RUNNING, transcribe_after=now)
            .execution_options(synchronize_session=False)
        ).rowcount
        db.commit()
        if claimed == 1:
            message = db.get(Message, candidate)
            db.refresh(message)
            return message
    return None


def _audio(db: Session, message: Message) -> tuple[bytes, str, str, bool]:
    """(data, filename, mime type, stored) of the message's audio: the stored copy, else downloaded again."""
    if message.audio is not None:
        audio = message.audio
        return audio.data, audio.filename, audio.mime_type, True
    voice = (message.raw or {}).get("voice") or {}
    source = db.get(Source, message.source_id) if message.source_id else None
    if not voice or source is None or source.type not in API_CONNECTORS:
        raise TranscriptionError(NO_AUDIO)
    attachment = AudioAttachment.model_validate(voice)
    with build_connector(source) as connector:
        data = connector.download_audio(attachment)
    if len(data) > MAX_AUDIO_BYTES:
        raise AudioTooLargeError(TOO_LARGE_NOTE)
    return data, attachment.filename, attachment.mime_type, False


def _language(db: Session, message: Message) -> str | None:
    project = db.get(Project, message.project_id)
    value = project.voice_language if project else "auto"
    return None if value in (None, "", "auto") else value


def _finish(db: Session, message: Message, status: str, error: str | None, *, attempts: int | None = None) -> str:
    message.transcript_status = status
    message.transcript_error = error
    message.transcribe_after = None
    if attempts is not None:
        message.transcript_attempts = attempts
    db.commit()
    return status


def fail(db: Session, message: Message, error: str) -> str:
    """Mark a claimed message failed (Retry queues it again). Its audio, if stored, is kept."""
    return _finish(db, message, TRANSCRIPT_FAILED, error, attempts=(message.transcript_attempts or 0) + 1)


def transcribe_message(db: Session, message: Message, transcriber: GroqWhisper, now: datetime) -> str:
    """Transcribe one claimed voice message and store the result. Returns its new transcript_status."""
    try:
        data, filename, mime_type, stored = _audio(db, message)
        result = transcribe_with_languages(transcriber, data, filename, mime_type, _language(db, message))
    except AudioTooLargeError:
        return _finish(db, message, TRANSCRIPT_SKIPPED, TOO_LARGE_NOTE)
    except TranscriptionUnavailable as exc:
        attempts = (message.transcript_attempts or 0) + 1
        if attempts >= MAX_ATTEMPTS:
            return _finish(db, message, TRANSCRIPT_FAILED, str(exc), attempts=attempts)
        wait = max(exc.retry_after or 0, REQUEUE_SECONDS[min(attempts, len(REQUEUE_SECONDS)) - 1])
        message.transcript_status = TRANSCRIPT_PENDING
        message.transcript_error = str(exc)
        message.transcript_attempts = attempts
        message.transcribe_after = now + timedelta(seconds=wait)
        db.commit()
        return TRANSCRIPT_PENDING
    except (TranscriptionError, ConnectorError, DecryptionError) as exc:
        return fail(db, message, str(exc))
    except NotImplementedError:
        return fail(db, message, NO_AUDIO)
    except Exception:
        logger.exception("transcription of message %s failed", message.id)
        db.rollback()
        return fail(db, message, UNEXPECTED)

    _store(db, message, result, data, filename, mime_type, stored)
    return _finish(db, message, TRANSCRIPT_DONE, None)


def _store(
    db: Session, message: Message, result: Transcription, data: bytes, filename: str, mime_type: str, stored: bool
) -> None:
    """Save the transcript as the message text and its language details in raw["voice"]; keep or drop the audio.
    The previous transcript, if any, goes to raw["voice"]["history"]."""
    voice = dict((message.raw or {}).get("voice") or {})
    if voice.get("transcribed_at"):
        earlier = {k: voice.get(k) for k in (*result.fields(), "transcribed_at")}
        voice["history"] = [*(voice.get("history") or []), {**earlier, "text": message.text}]
    voice.update(
        result.fields(),
        duration=result.duration if result.duration is not None else voice.get("duration"),
        transcribed_at=utcnow().isoformat(),
    )
    message.raw = {**(message.raw or {}), "voice": voice}
    message.text = result.text.replace("\x00", "")
    if settings.keep_audio:
        if not stored:
            message.audio = VoiceAudio(
                filename=filename[:255], mime_type=mime_type[:100], size_bytes=len(data), data=data
            )
    elif message.audio is not None:
        db.delete(message.audio)
        message.audio = None
    # A message read again after a new transcript gives its requirements again
    message.requirements_extracted_at = None
    logger.info(
        "speech message=%d transcribed model=%s language=%s retried=%s seconds=%s",
        message.id, result.model, result.language, result.retried, result.duration,
    )


class RetranscribeError(Exception):
    """Re-transcription failed; the old transcript is kept."""


class RetranscribeBusy(RetranscribeError):
    """The voice note is waiting for or in transcription right now."""


def can_retranscribe(message: Message, has_audio: bool) -> bool:
    """The audio is stored, or the source's connector can download it again."""
    return message.kind == KIND_VOICE and (
        has_audio or (message.source in API_CONNECTORS and message.source_id is not None)
    )


def retranscribe(db: Session, message: Message, language: str, transcriber: "GroqWhisper") -> None:
    """Transcribe a voice note again in the given language ("bn" / "en") and commit. The old transcript goes to
    raw["voice"]["history"]. Raises RetranscribeError (old transcript kept) when it is not possible or fails."""
    previous = message.transcript_status
    if previous not in (TRANSCRIPT_DONE, TRANSCRIPT_FAILED):
        raise RetranscribeBusy("This voice note is waiting to be transcribed; try again when it is done.")
    claimed = db.execute(
        update(Message)
        .where(Message.id == message.id, Message.transcript_status == previous)
        .values(transcript_status=TRANSCRIPT_RUNNING, transcribe_after=utcnow())
        .execution_options(synchronize_session=False)
    ).rowcount
    db.commit()
    if claimed != 1:
        raise RetranscribeBusy("This voice note is being transcribed right now; try again in a moment.")
    db.refresh(message)
    try:
        data, filename, mime_type, stored = _audio(db, message)
        result = transcribe_with_languages(transcriber, data, filename, mime_type, language)
    except Exception as exc:
        db.rollback()
        message.transcript_status = previous
        message.transcribe_after = None
        db.commit()
        if isinstance(exc, (TranscriptionError, ConnectorError, DecryptionError, AudioTooLargeError)):
            raise RetranscribeError(str(exc)) from None
        if isinstance(exc, NotImplementedError):
            raise RetranscribeError(NO_AUDIO) from None
        logger.exception("re-transcription of message %s failed", message.id)
        raise RetranscribeError(UNEXPECTED) from None
    _store(db, message, result, data, filename, mime_type, stored)
    _finish(db, message, TRANSCRIPT_DONE, None, attempts=0)


def queue_again(db: Session, message: Message) -> None:
    """The Retry button: a failed (or waiting) voice message goes back to the queue, due now."""
    message.transcript_status = TRANSCRIPT_PENDING
    message.transcript_error = None
    message.transcript_attempts = 0
    message.transcribe_after = None


# ---------------------------------------------------------------------------
# The background worker
# ---------------------------------------------------------------------------


class TranscriptionWorker:
    def __init__(
        self,
        session_factory: sessionmaker | Callable[[], Session] = SessionLocal,
        clock: Callable[[], datetime] = utcnow,
        *,
        transcriber_factory: Callable[[], GroqWhisper] = get_transcriber,
        llm_factory: Callable | None = None,
        tick_seconds: float = TICK_SECONDS,
        max_per_tick: int = MAX_PER_TICK,
    ):
        from app.connectors.scheduler import optional_llm

        self.session_factory = session_factory
        self.clock = clock
        self.transcriber_factory = transcriber_factory
        self.llm_factory = llm_factory or optional_llm
        self.tick_seconds = tick_seconds
        self.max_per_tick = max_per_tick

    def tick(self) -> int:
        """Transcribe up to max_per_tick due voice messages. Returns how many were handled."""
        from app.connectors.scheduler import run_auto_extract

        handled = 0
        done: dict[int, list[int]] = defaultdict(list)
        transcriber: GroqWhisper | None = None
        with self.session_factory() as db:
            try:
                for _ in range(self.max_per_tick):
                    now = self.clock()
                    message = claim_next(db, now)
                    if message is None:
                        break
                    handled += 1
                    if transcriber is None:
                        try:
                            transcriber = self.transcriber_factory()
                        except TranscriptionNotConfigured as exc:
                            fail(db, message, str(exc))
                            continue
                    if transcribe_message(db, message, transcriber, now) == TRANSCRIPT_DONE:
                        done[message.project_id].append(message.id)
            finally:
                if transcriber is not None:
                    transcriber.close()
            for project_id, message_ids in done.items():
                run_auto_extract(db, project_id, message_ids, self.llm_factory)
        return handled

    async def run_forever(self) -> None:
        logger.info(
            "Transcription worker started (needs a single backend worker; "
            "TRANSCRIPTION_WORKER_ENABLED=false turns it off)"
        )
        while True:
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                logger.exception("transcription tick failed")
            await asyncio.sleep(self.tick_seconds)


def start_worker() -> asyncio.Task:
    """Start the transcription worker on the running event loop (called from the app lifespan)."""
    return asyncio.create_task(TranscriptionWorker().run_forever(), name="transcription")

