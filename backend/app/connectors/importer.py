"""Stores normalised messages in the database, skipping ones that were already imported.

Used by file uploads now and by the API connectors (Step 4) later.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.base import NormalizedMessage, stable_id
from app.models import Message, Source

# Keeps the SQL "IN (...)" list small
_ID_CHUNK = 500


def _fit_id(external_id: str) -> str:
    """Ids longer than the column (e.g. very long email Message-IDs) are replaced by their hash."""
    return external_id if len(external_id) <= 255 else stable_id(external_id)


def _no_nul(value: str) -> str:
    """PostgreSQL text columns cannot store NUL characters; drop them (the original stays in `raw`)."""
    return value.replace("\x00", "")


@dataclass
class ImportCounts:
    imported: int = 0
    duplicates: int = 0
    # Ids of the messages added by this import (known after the flush)
    new_ids: list[int] = field(default_factory=list)


def get_or_create_source(db: Session, project_id: int, type_: str, display_name: str, config: dict) -> Source:
    """The project's source with this type and name; created on first use. `config` must not hold secrets."""
    display_name = display_name.strip()[:200] or type_
    source = db.scalar(
        select(Source).where(
            Source.project_id == project_id, Source.type == type_, Source.display_name == display_name
        )
    )
    if source is None:
        source = Source(project_id=project_id, type=type_, display_name=display_name, config=config)
        db.add(source)
        db.flush()
    else:
        source.config = {**(source.config or {}), **config}
    return source


def _existing_ids(db: Session, project_id: int, source: str, external_ids: list[str]) -> set[str]:
    found: set[str] = set()
    for start in range(0, len(external_ids), _ID_CHUNK):
        chunk = external_ids[start : start + _ID_CHUNK]
        found.update(
            db.scalars(
                select(Message.external_id).where(
                    Message.project_id == project_id,
                    Message.source == source,
                    Message.external_id.in_(chunk),
                )
            )
        )
    return found


def import_messages(
    db: Session, project_id: int, source: Source | None, messages: list[NormalizedMessage]
) -> ImportCounts:
    """Add new messages; ones with a known (source, external_id) count as duplicates. Does not commit."""
    counts = ImportCounts()
    added: list[Message] = []
    by_source: dict[str, list[NormalizedMessage]] = {}
    for msg in messages:
        by_source.setdefault(msg.source, []).append(msg)

    for source_type, group in by_source.items():
        ids = [_fit_id(m.external_id) for m in group]
        seen = _existing_ids(db, project_id, source_type, ids)
        for msg, external_id in zip(group, ids):
            if external_id in seen:
                counts.duplicates += 1
                continue
            seen.add(external_id)
            added.append(
                Message(
                    project_id=project_id,
                    source_id=source.id if source else None,
                    source=msg.source,
                    external_id=external_id,
                    channel=_no_nul(msg.channel)[:200],
                    author=_no_nul(msg.author)[:200],
                    sent_at=msg.sent_at,
                    text=_no_nul(msg.text),
                    raw=msg.raw,
                )
            )
            counts.imported += 1

    db.add_all(added)
    if source is not None:
        source.last_synced_at = datetime.now(timezone.utc)
    db.flush()
    counts.new_ids = [m.id for m in added]
    return counts
