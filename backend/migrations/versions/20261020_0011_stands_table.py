"""Таблица stands для мульти-стендовой архитектуры и шифрования секретов стендов.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("stands"):
        op.create_table(
            "stands",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("slug", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("site", sa.String(length=64), nullable=True),
            sa.Column(
                "driver", sa.String(length=32), server_default="scrapli", nullable=False
            ),
            sa.Column("jump_host", sa.String(length=255), nullable=True),
            sa.Column("jump_port", sa.Integer(), server_default="22", nullable=False),
            sa.Column("jump_user", sa.String(length=64), nullable=True),
            sa.Column("encrypted_password", sa.String(length=512), nullable=True),
            sa.Column("port_map_json", sa.String(length=4096), nullable=True),
            sa.Column("topology_path", sa.String(length=512), nullable=True),
            sa.Column("intent_subpath", sa.String(length=255), nullable=True),
            sa.Column(
                "queue_name", sa.String(length=64), server_default="default", nullable=False
            ),
            sa.Column(
                "status", sa.String(length=32), server_default="ONLINE", nullable=False
            ),
            sa.Column("description", sa.String(length=512), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_stands")),
        )
    existing_idx = {idx["name"] for idx in inspector.get_indexes("stands")}
    if op.f("ix_stands_slug") not in existing_idx:
        op.create_index(op.f("ix_stands_slug"), "stands", ["slug"], unique=True)
    if op.f("ix_stands_site") not in existing_idx:
        op.create_index(op.f("ix_stands_site"), "stands", ["site"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_stands_site"), table_name="stands")
    op.drop_index(op.f("ix_stands_slug"), table_name="stands")
    op.drop_table("stands")
