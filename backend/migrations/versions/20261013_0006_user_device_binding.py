"""Привязка персонального токена к устройству (bound_client_id).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(
                "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'platform') THEN "
                "ALTER TYPE platform ADD VALUE IF NOT EXISTS 'linux_server'; "
                "END IF; END $$;"
            )
            op.execute(
                "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'devicerole') THEN "
                "ALTER TYPE devicerole ADD VALUE IF NOT EXISTS 'server'; "
                "END IF; END $$;"
            )
            op.execute(
                "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'userrole') THEN "
                "ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'owner'; "
                "END IF; END $$;"
            )
    existing_cols = {c["name"] for c in sa.inspect(bind).get_columns("users")}
    new_cols = [
        ("allowed_system", sa.Column("allowed_system", sa.String(length=128), nullable=True)),
        ("bound_client_id", sa.Column("bound_client_id", sa.String(length=128), nullable=True)),
        ("bound_ip", sa.Column("bound_ip", sa.String(length=64), nullable=True)),
        ("bound_user_agent", sa.Column("bound_user_agent", sa.String(length=255), nullable=True)),
        ("bound_at", sa.Column("bound_at", sa.DateTime(timezone=True), nullable=True)),
        ("last_seen_at", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True)),
        ("expires_at", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)),
        ("telegram_chat_id", sa.Column("telegram_chat_id", sa.String(length=64), nullable=True)),
        ("telegram_username", sa.Column("telegram_username", sa.String(length=64), nullable=True)),
        (
            "telegram_verified_at",
            sa.Column("telegram_verified_at", sa.DateTime(timezone=True), nullable=True),
        ),
        ("verification_code", sa.Column("verification_code", sa.String(length=32), nullable=True)),
        (
            "verification_expires_at",
            sa.Column("verification_expires_at", sa.DateTime(timezone=True), nullable=True),
        ),
    ]
    cols_to_add = [col for name, col in new_cols if name not in existing_cols]
    if cols_to_add:
        with op.batch_alter_table("users") as batch_op:
            for col in cols_to_add:
                batch_op.add_column(col)


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("verification_expires_at")
        batch_op.drop_column("verification_code")
        batch_op.drop_column("telegram_verified_at")
        batch_op.drop_column("telegram_username")
        batch_op.drop_column("telegram_chat_id")
        batch_op.drop_column("expires_at")
        batch_op.drop_column("last_seen_at")
        batch_op.drop_column("bound_at")
        batch_op.drop_column("bound_user_agent")
        batch_op.drop_column("bound_ip")
        batch_op.drop_column("bound_client_id")
        batch_op.drop_column("allowed_system")

