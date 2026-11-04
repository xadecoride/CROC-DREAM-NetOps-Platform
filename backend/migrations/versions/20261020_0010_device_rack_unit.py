"""Добавление полей стойки и юнита (rack, unit) в таблицу devices.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {col["name"] for col in inspector.get_columns("devices")}
    if "rack" not in columns:
        op.add_column("devices", sa.Column("rack", sa.String(length=64), nullable=True))
    if "unit" not in columns:
        op.add_column("devices", sa.Column("unit", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch_op:
        batch_op.drop_column("unit")
        batch_op.drop_column("rack")
