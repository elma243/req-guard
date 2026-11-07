"""similarity checks

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-11-07 14:00:00.000000

One embedding per requirement version (pgvector vector(384) on PostgreSQL, JSON on SQLite), and pair findings:
the other requirement and the model's score on validation_findings, plus the categories duplicate, conflict and
change_request. Rule 12: the new table gets row level security (PostgreSQL only). The vector extension was
enabled in d5e3f4a6b7c8.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, Sequence[str], None] = "e3f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_CATEGORIES = "category IN ('vague', 'missing_info', 'out_of_scope', 'module_alignment')"
NEW_CATEGORIES = (
    "category IN ('vague', 'missing_info', 'out_of_scope', 'module_alignment', "
    "'duplicate', 'conflict', 'change_request')"
)


def upgrade() -> None:
    postgresql = op.get_bind().dialect.name == "postgresql"
    if postgresql:
        from pgvector.sqlalchemy import Vector

        embedding_type = Vector(384)
    else:
        embedding_type = sa.JSON()

    op.create_table(
        "requirement_embeddings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("embedding", embedding_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["requirement_versions.id"],
            name=op.f("fk_requirement_embeddings_version_id_requirement_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_requirement_embeddings")),
        sa.UniqueConstraint("version_id", "model", name=op.f("uq_requirement_embeddings_version_id_model")),
    )
    op.create_index(op.f("ix_requirement_embeddings_version_id"), "requirement_embeddings", ["version_id"])
    if postgresql:
        op.execute("ALTER TABLE requirement_embeddings ENABLE ROW LEVEL SECURITY")

    with op.batch_alter_table("validation_findings") as batch:
        batch.add_column(sa.Column("related_requirement_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("score", sa.Float(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_validation_findings_related_requirement_id_requirements"),
            "requirements",
            ["related_requirement_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_index(
            op.f("ix_validation_findings_related_requirement_id"), ["related_requirement_id"]
        )
        batch.drop_constraint(op.f("ck_validation_findings_category"), type_="check")
        batch.create_check_constraint(op.f("ck_validation_findings_category"), NEW_CATEGORIES)


def downgrade() -> None:
    op.execute("DELETE FROM validation_findings WHERE category IN ('duplicate', 'conflict', 'change_request')")
    with op.batch_alter_table("validation_findings") as batch:
        batch.drop_constraint(op.f("ck_validation_findings_category"), type_="check")
        batch.create_check_constraint(op.f("ck_validation_findings_category"), OLD_CATEGORIES)
        batch.drop_index(op.f("ix_validation_findings_related_requirement_id"))
        batch.drop_constraint(op.f("fk_validation_findings_related_requirement_id_requirements"), type_="foreignkey")
        batch.drop_column("score")
        batch.drop_column("related_requirement_id")

    op.drop_index(op.f("ix_requirement_embeddings_version_id"), table_name="requirement_embeddings")
    op.drop_table("requirement_embeddings")
