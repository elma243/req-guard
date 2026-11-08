"""Message sources of a project: file/export uploads and API connectors (create, sync, status).

Only the project owner can use these. Connector tokens come from backend/.env or are stored
encrypted on the Source (Gmail); no endpoint ever returns them.
"""

import logging
from pathlib import PurePath
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import delete, func, select

from app.api.deps import CurrentUser, DbSession, OwnedProject
from app.connectors import sync
from app.connectors.files import SUPPORTED_EXTENSIONS, connector_for_upload
from app.connectors.gmail import GmailConnector, authorize_url, exchange_code
from app.connectors.http import ConnectorError
from app.connectors.importer import get_or_create_source, import_messages
from app.connectors.registry import (
    API_CONNECTORS,
    missing_env,
    needs_auth,
    not_configured_message,
    validate_settings,
)
from app.connectors.scheduler import schedule_first_run
from app.core.config import settings
from app.core.crypto import EncryptionKeyError, encrypt
from app.core.security import InvalidTokenError, create_oauth_state, decode_oauth_state
from app.models import Message, Project, Source
from app.models.source import SYNC_NEEDS_AUTH, SYNC_OK
from app.schemas.message import (
    AuthorizeUrl,
    ConnectorOut,
    FileImportResult,
    SourceCreate,
    SourceOut,
    SourceStatus,
    SyncResult,
    UploadResult,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}", tags=["sources"])
# Endpoints outside a project: the connector list and the OAuth callback (Google redirects the browser there)
public_router = APIRouter(tags=["sources"])

GMAIL = GmailConnector.source

MAX_FILES = 10
MAX_FILE_BYTES = 10 * 1024 * 1024

# Emails and meeting notes from many files are grouped under one source each
GROUPED_SOURCE_NAMES = {"email": "Email files", "meeting_notes": "Meeting notes"}


@router.get("/sources", response_model=list[SourceOut])
def list_sources(project: OwnedProject, db: DbSession) -> list[SourceOut]:
    counts = dict(
        db.execute(
            select(Message.source_id, func.count())
            .where(Message.project_id == project.id)
            .group_by(Message.source_id)
        ).all()
    )
    return [_source_out(source, counts.get(source.id, 0)) for source in project.sources]


def _source_out(source: Source, message_count: int) -> SourceOut:
    return SourceOut.model_validate(source).model_copy(
        update={"message_count": message_count, "needs_auth": needs_auth(source)}
    )


def _get_source(db: DbSession, project: Project, source_id: int) -> Source:
    source = db.get(Source, source_id)
    if source is None or source.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Source not found")
    return source


def _get_api_source(db: DbSession, project: Project, source_id: int) -> Source:
    source = _get_source(db, project, source_id)
    if source.type not in API_CONNECTORS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="This source was uploaded from files. Upload the files again to import new messages.",
        )
    return source


def _message_count(db: DbSession, source: Source) -> int:
    return db.scalar(select(func.count()).select_from(Message).where(Message.source_id == source.id)) or 0


# ---------- API connectors ----------


@public_router.get("/connectors", response_model=list[ConnectorOut])
def list_connectors(user: CurrentUser) -> list[ConnectorOut]:
    """Every API connector and whether backend/.env has what it needs. Variable names only, never values."""
    result = []
    for source_type, connector in API_CONNECTORS.items():
        missing = missing_env(source_type)
        result.append(
            ConnectorOut(type=source_type, label=connector.label, configured=not missing, missing_env=missing)
        )
    return result


def _validation_message(exc: ValidationError) -> str:
    error = exc.errors()[0]
    field = ".".join(str(part) for part in error.get("loc", ()))
    message = str(error.get("msg", "Invalid value")).removeprefix("Value error, ")
    return f"{field}: {message}" if field else message


@router.post("/sources", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
def create_source(body: SourceCreate, project: OwnedProject, db: DbSession) -> SourceOut:
    """Add an API source (Jira, Slack, Gmail, Telegram, Discord) with its non-secret settings."""
    if body.type not in API_CONNECTORS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"Unknown connector. Use one of: {', '.join(API_CONNECTORS)}."
        )
    missing = missing_env(body.type)
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=not_configured_message(missing))
    try:
        user_settings = validate_settings(body.type, body.settings)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=_validation_message(exc)) from None

    exists = db.scalar(
        select(Source.id).where(
            Source.project_id == project.id, Source.type == body.type, Source.display_name == body.display_name
        )
    )
    if exists is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="A source with this name already exists.")

    source = Source(
        project_id=project.id,
        type=body.type,
        display_name=body.display_name,
        config={"origin": "api", "settings": user_settings, "state": {}},
        sync_status=SYNC_NEEDS_AUTH if body.type == GMAIL else None,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return _source_out(source, 0)


_SYNC_HTTP_STATUS = {
    sync.NOT_CONFIGURED: status.HTTP_400_BAD_REQUEST,
    sync.NEEDS_AUTH: status.HTTP_409_CONFLICT,
    sync.CONNECTOR: status.HTTP_502_BAD_GATEWAY,
    sync.UNEXPECTED: status.HTTP_502_BAD_GATEWAY,
}


@router.post("/sources/{source_id}/sync", response_model=SyncResult)
def sync_source(source_id: int, project: OwnedProject, db: DbSession) -> SyncResult:
    """Fetch new messages from the API. Messages that were already imported are skipped.

    409 while another sync of this source (e.g. an automatic one) is running. A failure is stored on the source,
    so its card shows it, and answered with a clear message.
    """
    source = _get_api_source(db, project, source_id)
    try:
        outcome = sync.run_sync(db, source)
    except sync.SyncLocked as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None
    if outcome.status != SYNC_OK:
        raise HTTPException(_SYNC_HTTP_STATUS[outcome.reason], detail=outcome.error)
    return SyncResult(
        imported=outcome.imported,
        duplicates=outcome.duplicates,
        sync_status=SYNC_OK,
        last_synced_at=source.last_synced_at,
    )


@router.get("/sources/{source_id}/status", response_model=SourceStatus)
def source_status(source_id: int, project: OwnedProject, db: DbSession) -> SourceStatus:
    source = _get_source(db, project, source_id)
    return SourceStatus(
        id=source.id,
        type=source.type,
        configured=source.type not in API_CONNECTORS or not missing_env(source.type),
        needs_auth=needs_auth(source),
        sync_status=source.sync_status,
        sync_error=source.sync_error,
        last_synced_at=source.last_synced_at,
        message_count=_message_count(db, source),
    )


@router.get("/sources/{source_id}/gmail/authorize", response_model=AuthorizeUrl)
def gmail_authorize(source_id: int, project: OwnedProject, db: DbSession, user: CurrentUser) -> AuthorizeUrl:
    """URL of Google's consent page; the frontend sends the browser there."""
    source = _get_api_source(db, project, source_id)
    if source.type != GMAIL:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Only Gmail sources connect a Google account.")
    missing = missing_env(GMAIL)
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=not_configured_message(missing))
    url = authorize_url(
        create_oauth_state(user.id, source.id),
        client_id=str(settings.google_client_id),
        redirect_uri=settings.google_redirect_uri,
    )
    return AuthorizeUrl(url=url)


@public_router.get("/oauth/gmail/callback", include_in_schema=False)
def gmail_callback(db: DbSession, state: str = "", code: str = "", error: str = "") -> RedirectResponse:
    """Google redirects the browser here. Only the signed `state` says which user and source it is for."""

    def back(result: str) -> RedirectResponse:
        return RedirectResponse(f"{settings.frontend_url.rstrip('/')}/sources?gmail={result}", status_code=303)

    try:
        user_id, source_id = decode_oauth_state(state)
    except InvalidTokenError:
        return back("error")
    source = db.get(Source, source_id)
    if source is None or source.type != GMAIL or source.project.owner_id != user_id:
        return back("error")

    if error or not code:
        source.sync_status = SYNC_NEEDS_AUTH
        source.sync_error = "Google access was not granted."
        db.commit()
        return back("denied")

    try:
        refresh_token = exchange_code(
            code,
            client_id=str(settings.google_client_id),
            client_secret=str(settings.google_client_secret),
            redirect_uri=settings.google_redirect_uri,
        )
        source.credentials_encrypted = encrypt(refresh_token)
    except (ConnectorError, EncryptionKeyError) as exc:
        source.sync_status = SYNC_NEEDS_AUTH
        source.sync_error = str(exc)
        db.commit()
        return back("error")

    source.sync_status = None
    source.sync_error = None
    # A connected account starts auto-sync again (it stops while the account needs connecting)
    source.failure_count = 0
    source.auto_sync_paused = False
    schedule_first_run(source, sync.utcnow())
    db.commit()
    return back("connected")


# ---------- Delete and uploads ----------


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: int, project: OwnedProject, db: DbSession) -> Response:
    """Permanently delete a source and every message imported from it (NFR-04)."""
    source = _get_source(db, project, source_id)
    # messages.source_id is ON DELETE SET NULL, so the messages are removed explicitly (same transaction)
    db.execute(delete(Message).where(Message.project_id == project.id, Message.source_id == source.id))
    db.delete(source)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _import_file(db: DbSession, project_id: int, filename: str, data: bytes) -> FileImportResult:
    connector = connector_for_upload(filename, data)
    if connector is None:
        return FileImportResult(
            filename=filename,
            type=None,
            error=f"Unsupported file. Upload a WhatsApp .txt export, a Messenger .json export, "
            f"an .eml email or .txt/.md meeting notes ({', '.join(SUPPORTED_EXTENSIONS)}).",
        )
    try:
        messages = connector.fetch()
    except ValueError as exc:
        return FileImportResult(filename=filename, type=connector.source, error=str(exc))
    except Exception:
        logger.exception("Could not parse uploaded file %s", filename)
        return FileImportResult(filename=filename, type=connector.source, error="Could not read this file.")

    name = GROUPED_SOURCE_NAMES.get(connector.source) or (messages[0].channel if messages else PurePath(filename).stem)
    source = get_or_create_source(db, project_id, connector.source, name, {"origin": "upload", "last_file": filename})
    counts = import_messages(db, project_id, source, messages)
    return FileImportResult(
        filename=filename,
        type=connector.source,
        imported=counts.imported,
        duplicates=counts.duplicates,
        skipped=connector.skipped,
    )


@router.post("/uploads", response_model=UploadResult)
def upload_files(
    project: OwnedProject,
    db: DbSession,
    files: Annotated[list[UploadFile], File(description="Chat exports, .eml emails or meeting notes")],
) -> UploadResult:
    """Import messages from uploaded files. The type of each file is detected from its name and content."""
    if len(files) > MAX_FILES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Upload at most {MAX_FILES} files at once")

    results: list[FileImportResult] = []
    for upload in files:
        filename = PurePath(upload.filename or "upload").name
        data = upload.file.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            results.append(
                FileImportResult(filename=filename, type=None, error="File is larger than 10 MB.")
            )
            continue
        results.append(_import_file(db, project.id, filename, data))
    db.commit()

    return UploadResult(
        imported=sum(r.imported for r in results),
        duplicates=sum(r.duplicates for r in results),
        skipped=sum(r.skipped for r in results),
        files=results,
    )
