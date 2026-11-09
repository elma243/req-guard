"""voice notes

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-11-09 10:00:00.000000

Voice notes (FR-19, AR-10): messages get a kind (text | voice) and the state of their transcription (status,
error, attempts, backoff time); projects get the voice language (auto | bn | en). The new voice_audio table holds
the audio of a voice message until it is transcribed (or for good with KEEP_AUDIO=true). Rule 12: it gets row
level security (PostgreSQL only).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, Sequence[str], None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch:
        batch.add_column(sa.Column("kind", sa.String(length=10), server_default="text", nullable=False))
        batch.add_column(sa.Column("transcript_status", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("transcript_error", sa.Text(), nullable=True))
        batch.add_column(sa.Column("transcript_attempts", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("transcribe_after", sa.DateTime(timezone=True), nullable=True))
        batch.create_index(
            "ix_messages_transcript_status_transcribe_after", ["transcript_status", "transcribe_after"]
        )

    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("voice_language", sa.String(length=10), server_default="auto", nullable=False))
        batch.create_check_constraint(op.f("ck_projects_voice_language"), "voice_language IN ('auto', 'bn', 'en')")

    op.create_table(
        "voice_audio",
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"], ["messages.id"], name=op.f("fk_voice_audio_message_id_messages"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("message_id", name=op.f("pk_voice_audio")),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE voice_audio ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("voice_audio")
    with op.batch_alter_table("projects") as batch:
        batch.drop_constraint(op.f("ck_projects_voice_language"), type_="check")
        batch.drop_column("voice_language")
    with op.batch_alter_table("messages") as batch:
        batch.drop_index("ix_messages_transcript_status_transcribe_after")
        batch.drop_column("transcribe_after")
        batch.drop_column("transcript_attempts")
        batch.drop_column("transcript_error")
        batch.drop_column("transcript_status")
        batch.drop_column("kind")
