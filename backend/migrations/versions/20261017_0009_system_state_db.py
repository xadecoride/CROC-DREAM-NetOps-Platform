"""Таблица system_state для хранения рантайм-конфигурации и целей сборщиков в БД.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("system_state"):
        op.create_table(
            "system_state",
            sa.Column("key", sa.String(length=128), nullable=False),
            sa.Column(
                "value", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("key", name=op.f("pk_system_state")),
        )
    indexes = [
        (op.f("ix_users_bound_client_id"), "users", ["bound_client_id"]),
        (op.f("ix_users_telegram_chat_id"), "users", ["telegram_chat_id"]),
        (op.f("ix_devices_site"), "devices", ["site"]),
        ("ix_jobs_type_status", "jobs", ["type", "status"]),
        (op.f("ix_job_targets_hostname"), "job_targets", ["hostname"]),
    ]
    for idx_name, table_name, columns in indexes:
        existing_idx = {idx["name"] for idx in inspector.get_indexes(table_name)}
        if idx_name not in existing_idx:
            op.create_index(idx_name, table_name, columns, unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_job_targets_hostname"), table_name="job_targets")
    op.drop_index("ix_jobs_type_status", table_name="jobs")
    op.drop_index(op.f("ix_devices_site"), table_name="devices")
    op.drop_index(op.f("ix_users_telegram_chat_id"), table_name="users")
    op.drop_index(op.f("ix_users_bound_client_id"), table_name="users")
    op.drop_table("system_state")
