"""Automatic sync of API sources, as an asyncio task inside the API process (started in app/main.py).

Every TICK_SECONDS the scheduler looks for sources whose auto-sync is on and whose next run is due, and syncs
them one after another in a worker thread (at most MAX_PER_TICK per tick). The sync itself is app/connectors/
sync.py, the same code as the Sync button, so the per-source lock keeps a manual and an automatic sync of one
source from running together; a run that finds the lock taken is skipped.

New sources are spread over their interval by id, so they do not all start at once. Paused sources (after
MAX_FAILURES failed runs in a row) and Gmail sources that need the Google account connected again are left alone.
When the project has auto-extract on, requirements are extracted and validated from the newly imported messages
only, right after their sync.

The scheduler has no cross-process coordination beyond the lock, so the backend must run as ONE worker.
AUTO_SYNC_ENABLED=false turns it off (tests, deploys with several workers).

`tick()` is plain synchronous code with an injectable clock, so tests drive it with a fake clock.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.ai.llm import LLMClient, LLMNotConfiguredError, get_llm
from app.connectors import sync
from app.connectors.base import as_utc
from app.connectors.registry import API_CONNECTORS, needs_auth
from app.core.db import SessionLocal
from app.models import Project, Source
from app.models.source import SYNC_NEEDS_AUTH
from app.nlp.extraction import extract_requirements
from app.nlp.validate import validate_project

logger = logging.getLogger(__name__)

TICK_SECONDS = 15
MAX_PER_TICK = 5
# Sources are spread over their interval in steps of this many seconds, by id
SPREAD_STEP_SECONDS = 37
NO_LLM_CONFIGURED = "No AI model is configured; requirements were extracted with the rule-based fallback."


def optional_llm() -> LLMClient | None:
    """The configured LLM client, or None (rule-based extraction) when no provider is set up."""
    try:
        return get_llm()
    except LLMNotConfiguredError:
        return None


def run_auto_extract(
    db: Session, project_id: int, message_ids: list[int], llm_factory: Callable[[], LLMClient | None]
) -> None:
    """Extract and validate requirements from these messages only, if the project asks for it (auto-extract).

    Used after an auto-sync and after voice notes are transcribed. A failure is logged and does not count as a
    failed sync; the messages stay unread for the Extract button. Voice messages not transcribed yet are left for
    the transcription worker (extraction skips them).
    """
    project = db.get(Project, project_id)
    if project is None or not project.auto_extract:
        return
    try:
        llm = llm_factory()
        result = extract_requirements(
            db,
            project,
            llm,
            only_message_ids=message_ids,
            llm_unavailable_reason=None if llm else NO_LLM_CONFIGURED,
        )
        validate_project(db, project, only_new=True, llm=llm)
        db.commit()
        logger.info(
            "auto-extract project=%d messages=%d requirements=%d remaining=%d",
            project_id,
            result.messages_processed,
            result.requirements_created,
            result.messages_remaining,
        )
    except Exception:
        logger.exception("auto-extract project=%d failed", project_id)
        db.rollback()


def spread_offset(source_id: int, interval_minutes: int) -> timedelta:
    """Where in its interval this source runs, so sources with the same interval do not start together."""
    return timedelta(seconds=(source_id * SPREAD_STEP_SECONDS) % (interval_minutes * 60))


def schedule_first_run(source: Source, now: datetime) -> None:
    """Set the first automatic run of a source (auto-sync just turned on, interval changed, account connected)."""
    source.next_sync_at = now + spread_offset(source.id, source.sync_interval_minutes)


def can_auto_sync(source: Source) -> bool:
    """Auto-sync is on and nothing stops it: an API source, not paused, no Google account to connect."""
    return (
        source.auto_sync
        and not source.auto_sync_paused
        and source.type in API_CONNECTORS
        and source.sync_status != SYNC_NEEDS_AUTH
        and not needs_auth(source)
    )


class AutoSyncScheduler:
    def __init__(
        self,
        session_factory: sessionmaker | Callable[[], Session] = SessionLocal,
        clock: Callable[[], datetime] = sync.utcnow,
        *,
        tick_seconds: float = TICK_SECONDS,
        max_per_tick: int = MAX_PER_TICK,
        llm_factory: Callable[[], LLMClient | None] = optional_llm,
    ):
        self.session_factory = session_factory
        self.clock = clock
        self.llm_factory = llm_factory
        self.tick_seconds = tick_seconds
        self.max_per_tick = max_per_tick

    # ---------- one tick ----------

    def due_sources(self, db: Session, now: datetime) -> list[Source]:
        """Sources to sync now, earliest first. Gives a first run time to sources that have none."""
        candidates = [
            s
            for s in db.scalars(
                select(Source).where(
                    Source.auto_sync.is_(True),
                    Source.auto_sync_paused.is_(False),
                    Source.type.in_(list(API_CONNECTORS)),
                )
            )
            if can_auto_sync(s)
        ]
        unscheduled = [s for s in candidates if s.next_sync_at is None]
        for source in unscheduled:
            schedule_first_run(source, now)
        if unscheduled:
            db.commit()
        due = [s for s in candidates if as_utc(s.next_sync_at) <= now]
        due.sort(key=lambda s: (as_utc(s.next_sync_at), s.id))
        return due[: self.max_per_tick]

    def tick(self) -> int:
        """Sync every due source once. Returns how many syncs ran (skipped ones not counted)."""
        ran = 0
        with self.session_factory() as db:
            for source in self.due_sources(db, self.clock()):
                if self._run_one(db, source):
                    ran += 1
        return ran

    def _run_one(self, db: Session, source: Source) -> bool:
        started = self.clock()
        interval = timedelta(minutes=source.sync_interval_minutes)
        source_id, project_id = source.id, source.project_id
        try:
            outcome = sync.run_sync(db, source, started)
        except sync.SyncLocked:
            logger.info("auto-sync source=%d skipped: the previous sync is still running", source_id)
            self._set_next_run(db, source_id, started + interval)
            return False
        except Exception:
            logger.exception("auto-sync source=%d failed", source_id)
            db.rollback()
            self._set_next_run(db, source_id, started + interval)
            return True

        self._set_next_run(db, source_id, started + interval)
        new_ids = outcome.new_message_ids
        logger.info(
            "auto-sync source=%d status=%s imported=%d duplicates=%d",
            source_id,
            outcome.status,
            outcome.imported,
            outcome.duplicates,
        )
        if new_ids:
            db.execute(
                update(Project)
                .where(Project.id == project_id)
                .values(
                    unseen_auto_messages=Project.unseen_auto_messages + len(new_ids),
                    updated_at=Project.updated_at,  # not an edit of the project
                )
                .execution_options(synchronize_session=False)
            )
            db.commit()
            self._auto_extract(db, project_id, new_ids)
        return True

    def _auto_extract(self, db: Session, project_id: int, message_ids: list[int]) -> None:
        run_auto_extract(db, project_id, message_ids, self.llm_factory)

    @staticmethod
    def _set_next_run(db: Session, source_id: int, when: datetime) -> None:
        db.execute(
            update(Source)
            .where(Source.id == source_id)
            .values(next_sync_at=when)
            .execution_options(synchronize_session=False)
        )
        db.commit()

    # ---------- the background task ----------

    async def run_forever(self) -> None:
        logger.info(
            "Auto-sync scheduler started (needs a single backend worker; AUTO_SYNC_ENABLED=false turns it off)"
        )
        while True:
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                logger.exception("auto-sync tick failed")
            await asyncio.sleep(self.tick_seconds)


def start_scheduler() -> asyncio.Task:
    """Start the scheduler on the running event loop (called from the app lifespan)."""
    return asyncio.create_task(AutoSyncScheduler().run_forever(), name="auto-sync")
