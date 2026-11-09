"""FastAPI entry point: middleware, routers from app/api/ and the health check."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai.speech import start_worker
from app.api import auth, messages, projects, requirements, sources, team
from app.connectors.scheduler import start_scheduler
from app.core.config import settings
from app.core.db import get_db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Starts the auto-sync scheduler and the voice note transcription worker, unless AUTO_SYNC_ENABLED=false /
    TRANSCRIPTION_WORKER_ENABLED=false (both need a single backend worker)."""
    app.state.auto_sync_task = start_scheduler() if settings.auto_sync_enabled else None
    app.state.transcription_task = start_worker() if settings.transcription_worker_enabled else None
    try:
        yield
    finally:
        for task in (app.state.auto_sync_task, app.state.transcription_task):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


app = FastAPI(title="ReqGuard API", version="0.1.0", lifespan=lifespan)

# Allow the Next.js frontend to call the API from the browser
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(team.router)
app.include_router(sources.router)
app.include_router(sources.public_router)
app.include_router(messages.router)
app.include_router(requirements.router)


@app.get("/health")
def health(db: Session = Depends(get_db)) -> dict[str, str]:
    """Liveness check: the API is up and the database answers a simple query."""
    db.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "database": "ok",
        "db_engine": db.get_bind().dialect.name,
    }
