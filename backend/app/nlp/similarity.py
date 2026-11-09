"""Sentence embeddings for the similarity checks (BUILD_PLAN Step 7, part B).

The model (all-MiniLM-L6-v2 by default) runs locally on the CPU. It is loaded on first use, once per process,
and downloaded into the Hugging Face cache the first time. sentence_transformers (and so torch) is only imported
when a model is loaded: tests replace get_embedder with a fake and never import it.

When a model cannot load, ModelUnavailable says why; the caller still runs the rule-based checks. A failure is
remembered for RETRY_AFTER_SECONDS so requests do not each wait for a download that keeps failing.
"""

import logging
import threading
import time
from collections.abc import Callable, Sequence
from typing import Generic, Protocol, TypeVar

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import RequirementEmbedding, RequirementVersion

logger = logging.getLogger(__name__)

RETRY_AFTER_SECONDS = 600
BATCH_SIZE = 32

T = TypeVar("T")


class ModelUnavailable(Exception):
    """A similarity model is switched off or could not be loaded; str(exc) is the reason for the user."""


class LazyModel(Generic[T]):
    """Loads a model on the first get(), once, even when several requests ask at the same time."""

    def __init__(self, what: str, load: Callable[[], T]) -> None:
        self.what = what
        self._load = load
        self._lock = threading.Lock()
        self._model: T | None = None
        self._failed_at: float | None = None
        self._reason = ""

    def get(self) -> T:
        if not settings.similarity_enabled:
            raise ModelUnavailable("Similarity checks are switched off (SIMILARITY_ENABLED=false).")
        with self._lock:
            if self._model is not None:
                return self._model
            if self._failed_at is not None and time.monotonic() - self._failed_at < RETRY_AFTER_SECONDS:
                raise ModelUnavailable(self._reason)
            started = time.monotonic()
            try:
                self._model = self._load()
            except Exception as exc:  # missing package, no network for the first download, out of memory...
                logger.exception("Could not load the %s", self.what)
                self._failed_at = time.monotonic()
                self._reason = f"The {self.what} could not be loaded: {exc}"[:500]
                raise ModelUnavailable(self._reason) from exc
            logger.info("Loaded the %s in %.1f s", self.what, time.monotonic() - started)
            return self._model

    def reset(self) -> None:
        with self._lock:
            self._model, self._failed_at, self._reason = None, None, ""


class Embedder(Protocol):
    name: str

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, normalised to length 1."""
        ...


class SentenceEmbedder:
    def __init__(self, name: str) -> None:
        from sentence_transformers import SentenceTransformer

        self.name = name
        self._model = SentenceTransformer(name, device="cpu")

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(
            list(texts), batch_size=BATCH_SIZE, normalize_embeddings=True, show_progress_bar=False
        )
        return np.asarray(vectors, dtype=np.float32).tolist()


_embedder: LazyModel[Embedder] = LazyModel("embedding model", lambda: SentenceEmbedder(settings.embedding_model))


def get_embedder() -> Embedder:
    """The embedding model; raises ModelUnavailable."""
    return _embedder.get()


def existing_version_ids(db: Session, ids: Sequence[int]) -> set[int]:
    """Which of these requirement versions are still in the database."""
    found: set[int] = set()
    unique = list(dict.fromkeys(ids))
    for chunk in (unique[i : i + 500] for i in range(0, len(unique), 500)):
        found.update(db.scalars(select(RequirementVersion.id).where(RequirementVersion.id.in_(chunk))))
    return found


def ensure_embeddings(db: Session, embedder: Embedder, versions: Sequence[RequirementVersion]) -> dict[int, np.ndarray]:
    """The embedding of each version's statement by version id. Missing ones are made in one batch and stored.

    The caller commits. Versions never change, so a stored embedding is never made again. Versions that are no
    longer in the database (deleted by another extraction since they were loaded) are skipped and left out of
    the result, so no embedding is written for them.
    """
    db.flush()
    existing = existing_version_ids(db, [v.id for v in versions])
    versions = [v for v in versions if v.id in existing]
    ids = [v.id for v in versions]
    stored: dict[int, list[float]] = {}
    for chunk in (ids[i : i + 500] for i in range(0, len(ids), 500)):  # keep the IN list short
        rows = db.execute(
            select(RequirementEmbedding.version_id, RequirementEmbedding.embedding).where(
                RequirementEmbedding.model == embedder.name, RequirementEmbedding.version_id.in_(chunk)
            )
        ).all()
        stored.update({version_id: vector for version_id, vector in rows})
    missing = [v for v in versions if v.id not in stored]
    if missing:
        vectors = embedder.encode([v.statement for v in missing])
        for version, vector in zip(missing, vectors, strict=True):
            db.add(RequirementEmbedding(version_id=version.id, model=embedder.name, embedding=vector))
            stored[version.id] = vector
    return {vid: np.asarray(vector, dtype=np.float32) for vid, vector in stored.items()}


def similarity_matrix(vectors: Sequence[np.ndarray]) -> np.ndarray:
    """Cosine similarity of every pair (the vectors have length 1, so it is the dot product)."""
    if not vectors:
        return np.zeros((0, 0), dtype=np.float32)
    matrix = np.vstack(vectors)
    return np.clip(matrix @ matrix.T, -1.0, 1.0)
