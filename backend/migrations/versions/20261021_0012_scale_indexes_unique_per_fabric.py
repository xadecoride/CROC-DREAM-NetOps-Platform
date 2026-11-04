"""Масштаб 100+ стендов: UNIQUE per fabric и индексы на горячие таблицы.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_UQ = "uq_devices_management_ip_management_port"
NEW_UQ = "uq_devices_fabric_id_management_ip_management_port"
INDEXES = (
    ("ix_job_logs_created_at", "job_logs", ["created_at"]),
    ("ix_drift_records_checked_at", "drift_records", ["checked_at"]),
    ("ix_config_snapshots_created_at", "config_snapshots", ["created_at"]),
    ("ix_devices_fabric_id_hostname", "devices", ["fabric_id", "hostname"]),
)


def _create_indexes() -> None:
    bind = op.get_bind()
    existing = {t: {i["name"] for i in sa.inspect(bind).get_indexes(t)} for _, t, _ in INDEXES}
    todo = [(n, t, c) for n, t, c in INDEXES if n not in existing[t]]
    if bind.dialect.name == "postgresql":
        # CONCURRENTLY не блокирует запись, но требует выхода из транзакции.
        with op.get_context().autocommit_block():
            for name, table, cols in todo:
                op.create_index(name, table, cols, postgresql_concurrently=True, if_not_exists=True)
    else:
        for name, table, cols in todo:
            op.create_index(name, table, cols)


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch_op:
        batch_op.drop_constraint(OLD_UQ, type_="unique")
        batch_op.create_unique_constraint(NEW_UQ, ["fabric_id", "management_ip", "management_port"])
    _create_indexes()


def downgrade() -> None:
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
    with op.batch_alter_table("devices") as batch_op:
        batch_op.drop_constraint(NEW_UQ, type_="unique")
        batch_op.create_unique_constraint(OLD_UQ, ["management_ip", "management_port"])
