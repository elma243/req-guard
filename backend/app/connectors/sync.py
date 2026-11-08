"""One sync of an API source, shared by the Sync button and the auto-sync scheduler.

Only one sync per source runs at a time. The lock is `Source.sync_started_at`, taken with a conditional UPDATE
so it also holds across sessions and processes. A lock older than LOCK_TIMEOUT is stale (the process stopped
in the middle of a sync) and may be taken over.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from app.connectors.base import as_utc
from app.connectors.http import ConnectorError, NeedsAuthError, NotConfiguredError
from app.connectors.importer import import_messages
from app.connectors.registry import build_connector
from app.core.crypto import DecryptionError
from app.models import Source
from app.models.source import SYNC_ERROR, SYNC_NEEDS_AUTH, SYNC_OK

logger = logging.getLogger(__name__)

LOCK_TIMEOUT = timedelta(minutes=15)
# Failed syncs in a row after which auto-sync pauses (until a sync succeeds or auto-sync is turned on again)
MAX_FAILURES = 3
UNEXPECTED_ERROR = "Unexpected error while syncing. See the backend log."

# Why a sync failed, for the HTTP status of the Sync button
NOT_CONFIGURED = "not_configured"
NEEDS_AUTH = "needs_auth"
CONNECTOR = "connector"
UNEXPECTED = "unexpected"


class SyncLocked(Exception):
    """Another sync of this source is still running."""


@dataclass
class SyncOutcome:
    status: str  # ok | error | needs_auth (same values as Source.sync_status)
    imported: int = 0
    duplicates: int = 0
    new_message_ids: list[int] = field(default_factory=list)
    error: str | None = None
    reason: str | None = None  # NOT_CONFIGURED | NEEDS_AUTH | CONNECTOR | UNEXPECTED when it failed


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_locked(source: Source, now: datetime) -> bool:
    """A sync of this source is running (its lock is set and not stale)."""
    started = source.sync_started_at
    return started is not None and as_utc(started) > now - LOCK_TIMEOUT


def acquire_lock(db: Session, source_id: int, now: datetime) -> bool:
    """Take the source's lock and commit. False when another sync holds it."""
    result = db.execute(
        update(Source)
        .where(
            Source.id == source_id,
            or_(Source.sync_started_at.is_(None), Source.sync_started_at < now - LOCK_TIMEOUT),
        )
        .values(sync_started_at=now)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return result.rowcount == 1


def _failed(db: Session, source: Source, sync_status: str, message: str, reason: str) -> SyncOutcome:
    """Store the failure on the source, so its card shows it.

    An error counts towards the pause after MAX_FAILURES. Needing the Google account connected again does not:
    auto-sync stops for that source anyway until the account is connected again.
    """
    source.sync_status = sync_status
    source.sync_error = message
    if sync_status == SYNC_NEEDS_AUTH:
        source.credentials_encrypted = None  # revoked/expired or unreadable: connect again
    else:
        source.failure_count = (source.failure_count or 0) + 1
        if source.failure_count >= MAX_FAILURES and source.auto_sync:
            source.auto_sync_paused = True
            logger.warning("auto-sync of source %s paused after %d failed syncs", source.id, source.failure_count)
    db.commit()
    return SyncOutcome(status=sync_status, error=message, reason=reason)


def _fetch_and_store(db: Session, source: Source) -> SyncOutcome:
    try:
        with build_connector(source) as connector:
            since = as_utc(source.last_synced_at) if source.last_synced_at else None
            messages = connector.fetch(since=since)
    except NotConfiguredError as exc:
        return _failed(db, source, SYNC_ERROR, str(exc), NOT_CONFIGURED)
    except (NeedsAuthError, DecryptionError) as exc:
        return _failed(db, source, SYNC_NEEDS_AUTH, str(exc), NEEDS_AUTH)
    except ConnectorError as exc:
        return _failed(db, source, SYNC_ERROR, str(exc), CONNECTOR)
    except Exception:
        logger.exception("Sync of source %s (%s) failed", source.id, source.type)
        return _failed(db, source, SYNC_ERROR, UNEXPECTED_ERROR, UNEXPECTED)

    counts = import_messages(db, source.project_id, source, messages)
    # Save the cursor only together with the messages, so nothing is skipped if storing fails
    source.config = {**(source.config or {}), "state": connector.state}
    source.sync_status = SYNC_OK
    source.sync_error = None
    source.failure_count = 0
    source.auto_sync_paused = False  # a successful sync (e.g. the Retry button) ends a pause
    db.commit()
    return SyncOutcome(
        status=SYNC_OK, imported=counts.imported, duplicates=counts.duplicates, new_message_ids=counts.new_ids
    )


def run_sync(db: Session, source: Source, now: datetime | None = None) -> SyncOutcome:
    """Fetch new messages of an API source and store them. Raises SyncLocked when a sync is already running.

    Messages that were already imported are skipped. A failure is stored on the source and returned, not raised.
    """
    now = now or utcnow()
    if not acquire_lock(db, source.id, now):
        raise SyncLocked("A sync for this source is already running. Try again in a moment.")
    db.refresh(source)
    try:
        return _fetch_and_store(db, source)
    except Exception:
        db.rollback()
        raise
    finally:
        source.sync_started_at = None
        db.commit()
