from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator, TypeEngine

from app.core.db import Base

# all-MiniLM-L6-v2 gives 384 numbers per text
EMBEDDING_DIMENSIONS = 384


class EmbeddingVector(TypeDecorator):
    """pgvector's vector(384) on PostgreSQL; a JSON list on SQLite, which has no vector type (tests)."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine:
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(EMBEDDING_DIMENSIONS))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect: Dialect):
        return None if value is None else [float(x) for x in value]

    def process_result_value(self, value, dialect: Dialect) -> list[float] | None:
        return None if value is None else [float(x) for x in value]


class RequirementEmbedding(Base):
    """The embedding of one requirement version's statement. Versions never change, so neither does this row."""

    __tablename__ = "requirement_embeddings"
    __table_args__ = (UniqueConstraint("version_id", "model"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("requirement_versions.id", ondelete="CASCADE"), index=True)
    # The model that made it; a different model needs new embeddings
    model: Mapped[str] = mapped_column(String(100))
    # Normalised to length 1, so the dot product of two embeddings is their cosine similarity
    embedding: Mapped[list[float]] = mapped_column(EmbeddingVector())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
