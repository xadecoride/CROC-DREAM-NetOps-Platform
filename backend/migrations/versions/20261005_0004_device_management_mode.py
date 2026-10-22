"""Добавление management_mode, proxy_jump и hardware_specs в devices.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = {col["name"] for col in sa.inspect(bind).get_columns("devices")}
    with op.batch_alter_table("devices") as batch_op:
        if "management_mode" not in existing_columns:
            batch_op.add_column(
                sa.Column(
                    "management_mode",
                    sa.String(32),
                    nullable=False,
                    server_default="MONITORING_ONLY",
                )
            )
        if "proxy_jump" not in existing_columns:
            batch_op.add_column(
                sa.Column(
                    "proxy_jump",
                    sa.String(255),
                    nullable=True,
                )
            )
        if "hardware_specs" not in existing_columns:
            batch_op.add_column(
                sa.Column(
                    "hardware_specs",
                    sa.String(2048),
                    nullable=True,
                )
            )


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch_op:
        batch_op.drop_column("hardware_specs")
        batch_op.drop_column("proxy_jump")
        batch_op.drop_column("management_mode")
