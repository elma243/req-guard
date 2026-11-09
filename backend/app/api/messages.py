"""Imported client messages of a project, with filters and pagination. Owner only.

Voice notes: Retry queues a failed transcription again; the audio endpoint plays a stored voice note.
"""

import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import func, select, update

from app.ai import speech
from app.ai.speech import RetranscribeBusy, RetranscribeError, TranscriptionNotConfigured, can_retranscribe, queue_again
from app.api.deps import DbSession, OptionalLLM, OwnedProject
from app.connectors.base import audio_mime
from app.models import Message, Project, VoiceAudio
from app.models.message import KIND_VOICE, TRANSCRIPT_DONE, TRANSCRIPT_RUNNING, TRANSCRIPT_SKIPPED
from app.nlp.extraction import extract_requirements
from app.nlp.validate import validate_project
from app.schemas.extraction import ExtractResult
from app.schemas.message import MessageOut, MessagePage, RetranscribeRequest, UnseenMessages, VoiceInfo

router = APIRouter(prefix="/projects/{project_id}/messages", tags=["messages"])


def _contains(value: str) -> str:
    """LIKE pattern for 'contains value', with LIKE wildcards in the value escaped."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def message_out(message: Message, has_audio: bool) -> MessageOut:
    out = MessageOut.model_validate(message)
    if message.kind != KIND_VOICE:
        return out
    return out.model_copy(
        update={
            "has_audio": has_audio,
            "can_retranscribe": can_retranscribe(message, has_audio),
            "voice": VoiceInfo.of(message.raw),
        }
    )


class RetranscribeResult(BaseModel):
    message: MessageOut
    # Extraction run again on the new transcript (unreviewed AI suggestions from the old one are replaced)
    extraction: ExtractResult


NO_LLM_CONFIGURED = "No AI model is configured. Set GROQ_API_KEY, or LLM_MODEL and LLM_API_KEY, in backend/.env."
NO_AUDIO_KEPT = (
    "The audio of this voice note was deleted after transcription (KEEP_AUDIO=false), so it cannot be transcribed "
    "again. Upload the file again, or set KEEP_AUDIO=true for new voice notes."
)


def _with_audio(db: DbSession, message_ids: list[int]) -> set[int]:
    if not message_ids:
        return set()
    return set(db.scalars(select(VoiceAudio.message_id).where(VoiceAudio.message_id.in_(message_ids))))


def _get_message(db: DbSession, project: Project, message_id: int) -> Message:
    message = db.get(Message, message_id)
    if message is None or message.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Message not found")
    return message


@router.get("", response_model=MessagePage)
def list_messages(
    project: OwnedProject,
    db: DbSession,
    source: Annotated[str | None, Query(max_length=30)] = None,
    author: Annotated[str | None, Query(max_length=200)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: Annotated[str | None, Query(max_length=200, description="Text search")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> MessagePage:
    conditions = [Message.project_id == project.id]
    if source:
        conditions.append(Message.source == source)
    if author and author.strip():
        conditions.append(Message.author.ilike(_contains(author.strip()), escape="\\"))
    if q and q.strip():
        conditions.append(Message.text.ilike(_contains(q.strip()), escape="\\"))
    if date_from:
        conditions.append(Message.sent_at >= _day_start(date_from))
    if date_to:  # inclusive: the whole of that day
        conditions.append(Message.sent_at < _day_start(date_to + timedelta(days=1)))

    total = db.scalar(select(func.count()).select_from(Message).where(*conditions)) or 0
    rows = db.scalars(
        select(Message)
        .where(*conditions)
        .order_by(Message.sent_at.desc(), Message.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    audio = _with_audio(db, [m.id for m in rows if m.kind == KIND_VOICE])
    return MessagePage(
        items=[message_out(m, m.id in audio) for m in rows], total=total, page=page, page_size=page_size
    )


@router.get("/unseen", response_model=UnseenMessages)
def unseen_messages(project: OwnedProject) -> UnseenMessages:
    """How many messages auto-sync imported since the Messages page was last opened (the menu badge)."""
    return UnseenMessages(count=project.unseen_auto_messages)


@router.post("/seen", response_model=UnseenMessages)
def mark_messages_seen(project: OwnedProject, db: DbSession) -> UnseenMessages:
    """The Messages page was opened: clear the badge."""
    db.execute(
        update(Project)
        .where(Project.id == project.id)
        # A badge change is not an edit of the project, so updated_at stays as it was
        .values(unseen_auto_messages=0, updated_at=Project.updated_at)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return UnseenMessages(count=0)


@router.post("/{message_id}/transcribe", response_model=MessageOut)
def retry_transcription(message_id: int, project: OwnedProject, db: DbSession) -> MessageOut:
    """Retry: queue a voice note that was not transcribed. The worker picks it up within seconds."""
    message = _get_message(db, project, message_id)
    if message.kind != KIND_VOICE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Only voice notes are transcribed.")
    if message.transcript_status == TRANSCRIPT_SKIPPED:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"This voice note cannot be transcribed: {message.transcript_error or 'no audio'}",
        )
    if message.transcript_status in (TRANSCRIPT_DONE, TRANSCRIPT_RUNNING):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This voice note is already transcribed or in progress.")
    queue_again(db, message)
    db.commit()
    return message_out(message, bool(_with_audio(db, [message.id])))


@router.post("/{message_id}/retranscribe", response_model=RetranscribeResult)
def retranscribe_message(
    message_id: int, body: RetranscribeRequest, project: OwnedProject, db: DbSession, llm: OptionalLLM
) -> RetranscribeResult:
    """Re-transcribe as Bangla / English: a new transcript (the old one is kept in raw["voice"]["history"]), then
    requirement extraction and validation run again for this message."""
    message = _get_message(db, project, message_id)
    if message.kind != KIND_VOICE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Only voice notes are transcribed.")
    if not can_retranscribe(message, bool(_with_audio(db, [message.id]))):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=NO_AUDIO_KEPT)
    try:
        transcriber = speech.get_transcriber()
    except TranscriptionNotConfigured as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    try:
        with transcriber:
            speech.retranscribe(db, message, body.language, transcriber)
    except RetranscribeBusy as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except RetranscribeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"{exc} The old transcript was kept.") from None

    extraction = extract_requirements(
        db, project, llm, message_ids=[message.id], llm_unavailable_reason=None if llm else NO_LLM_CONFIGURED
    )
    extraction.similarity_unavailable_reason = validate_project(
        db, project, only_new=True, llm=llm
    ).similarity_unavailable_reason
    db.commit()
    db.refresh(message)
    return RetranscribeResult(message=message_out(message, bool(_with_audio(db, [message.id]))), extraction=extraction)


# Types browsers know for <audio> (Chrome does not play "audio/opus" or "audio/x-m4a" as such)
PLAYABLE_TYPES = {
    "audio/opus": "audio/ogg",
    "audio/x-m4a": "audio/mp4",
    "audio/m4a": "audio/mp4",
    "audio/mp3": "audio/mpeg",
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
    "audio/x-flac": "audio/flac",
}
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def playable_type(filename: str, mime_type: str) -> str:
    mime = audio_mime(filename, mime_type) or "application/octet-stream"
    return PLAYABLE_TYPES.get(mime, mime)


@router.get("/{message_id}/audio")
def message_audio(
    message_id: int,
    project: OwnedProject,
    db: DbSession,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
) -> Response:
    """The stored audio of a voice note, for the play button.

    Supports HTTP Range requests (206 Partial Content): Chrome reads the end of an Ogg/Opus file to find its
    length and needs ranges to show the duration and to seek.
    """
    message = _get_message(db, project, message_id)
    audio = db.get(VoiceAudio, message.id)
    if audio is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="The audio of this message is not stored.")
    data, size = audio.data, len(audio.data)
    media_type = playable_type(audio.filename, audio.mime_type)
    headers = {"Accept-Ranges": "bytes", "Content-Disposition": "inline", "Cache-Control": "private, max-age=3600"}
    match = _RANGE_RE.match((range_header or "").strip())
    if match and (match[1] or match[2]):
        if match[1]:
            start, end = int(match[1]), min(int(match[2]) if match[2] else size - 1, size - 1)
        else:  # "bytes=-500": the last 500 bytes
            start, end = max(0, size - int(match[2])), size - 1
        if start >= size or start > end:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
        return Response(
            content=data[start : end + 1],
            status_code=206,
            media_type=media_type,
            headers={**headers, "Content-Range": f"bytes {start}-{end}/{size}"},
        )
    return Response(content=data, media_type=media_type, headers=headers)


@router.delete("/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_message(message_id: int, project: OwnedProject, db: DbSession) -> Response:
    """Permanently delete one imported message (NFR-04)."""
    message = _get_message(db, project, message_id)
    db.delete(message)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
