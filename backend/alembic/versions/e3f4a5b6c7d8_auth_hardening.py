"""auth hardening

Revision ID: e3f4a5b6c7d8
Revises: d2b3c4e5f6a7
Create Date: 2026-11-07 10:00:00.000000

Email verification, login lockout and session invalidation columns on users, plus the email_otps and
password_reset_tokens tables. Accounts that exist before this migration count as verified, otherwise their
owners could no longer log in. Rule 12: the new tables get row level security (PostgreSQL only).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, Sequence[str], None] = "d2b3c4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("failed_logins", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("token_version", sa.Integer(), server_default="0", nullable=False))
    op.execute("UPDATE users SET email_verified_at = CURRENT_TIMESTAMP")

    op.create_table(
        "email_otps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_email_otps_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_otps")),
        sa.UniqueConstraint("user_id", name=op.f("uq_email_otps_user_id")),
    )
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_password_reset_tokens_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_password_reset_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_password_reset_tokens_token_hash")),
    )
    op.create_index(op.f("ix_password_reset_tokens_user_id"), "password_reset_tokens", ["user_id"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE email_otps ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE password_reset_tokens ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index(op.f("ix_password_reset_tokens_user_id"), table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
    op.drop_table("email_otps")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("token_version")
        batch.drop_column("locked_until")
        batch.drop_column("failed_logins")
        batch.drop_column("email_verified_at")
