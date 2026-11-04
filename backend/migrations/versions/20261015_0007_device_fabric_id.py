"""Добавление fabric_id и site в devices.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = {col["name"] for col in sa.inspect(bind).get_columns("devices")}
    with op.batch_alter_table("devices") as batch_op:
        if "fabric_id" not in existing_columns:
            batch_op.add_column(
                sa.Column(
                    "fabric_id",
                    sa.String(64),
                    nullable=False,
                    server_default="croc-dc1-pod1",
                )
            )
            batch_op.create_index(batch_op.f("ix_devices_fabric_id"), ["fabric_id"], unique=False)
        if "site" not in existing_columns:
            batch_op.add_column(
                sa.Column(
                    "site",
                    sa.String(64),
                    nullable=True,
                )
            )


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch_op:
        batch_op.drop_column("site")
        batch_op.drop_index(batch_op.f("ix_devices_fabric_id"))
        batch_op.drop_column("fabric_id")
