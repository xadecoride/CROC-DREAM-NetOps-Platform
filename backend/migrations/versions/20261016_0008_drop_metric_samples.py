"""Удаление таблицы metric_samples: история метрик живёт в VictoriaMetrics.

Последний срез сбора хранится в Redis (netops:telemetry:latest), а Prometheus
скрейпит /metrics и пишет ряды в VictoriaMetrics через remote_write.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("metric_samples"):
        existing_idx = {idx["name"] for idx in inspector.get_indexes("metric_samples")}
        if op.f("ix_metric_samples_collected_at") in existing_idx:
            op.drop_index(op.f("ix_metric_samples_collected_at"), table_name="metric_samples")
        if "ix_metric_samples_name_collected" in existing_idx:
            op.drop_index("ix_metric_samples_name_collected", table_name="metric_samples")
        op.drop_table("metric_samples")


def downgrade() -> None:
    op.create_table(
        "metric_samples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column(
            "labels", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column("value", sa.Double(), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metric_samples")),
    )
    op.create_index("ix_metric_samples_name_collected", "metric_samples", ["name", "collected_at"])
    op.create_index(op.f("ix_metric_samples_collected_at"), "metric_samples", ["collected_at"])
