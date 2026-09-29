"""Initial schema: devices, jobs, job targets and logs, config snapshots, drift history.

Revision ID: 0001
Revises:
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Enum values are spelled out so that this migration never changes when the
# Python enums evolve; add new values in a new migration.
PLATFORMS = ("cisco_iosxe", "arista_eos", "huawei_vrp")
DEVICE_ROLES = ("spine", "leaf", "border")
DEVICE_STATUSES = ("UNKNOWN", "IN_SYNC", "DRIFT_DETECTED", "UNREACHABLE", "IN_PROGRESS")
JOB_TYPES = ("DRY_RUN", "DEPLOY", "DRIFT_SCAN", "DRIFT_REMEDIATE")
JOB_STATUSES = ("PENDING", "RUNNING", "SUCCESS", "FAILED")
TARGET_STATUSES = ("PENDING", "SUCCESS", "FAILED", "SKIPPED", "ROLLED_BACK")
SNAPSHOT_KINDS = ("RUNNING", "INTENDED")
LOG_LEVELS = ("INFO", "WARNING", "ERROR")
DRIFT_STATUSES = ("IN_SYNC", "DRIFT_DETECTED", "UNREACHABLE")


def _enum(name: str, values: Sequence[str]) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, length=32)


from typing import Any
def _timestamp(name: str, *, nullable: bool = False) -> sa.Column[Any]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _json(name: str) -> sa.Column[Any]:
    return sa.Column(name, sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("hostname", sa.String(length=253), nullable=False),
        sa.Column("management_ip", sa.String(length=45), nullable=False),
        sa.Column("management_port", sa.Integer(), server_default="22", nullable=False),
        sa.Column("platform", _enum("platform", PLATFORMS), nullable=False),
        sa.Column("role", _enum("devicerole", DEVICE_ROLES), nullable=False),
        sa.Column("auth_profile", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            _enum("devicestatus", DEVICE_STATUSES),
            server_default="UNKNOWN",
            nullable=False,
        ),
        _timestamp("last_checked_at", nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_devices")),
        sa.UniqueConstraint("hostname", name=op.f("uq_devices_hostname")),
        sa.UniqueConstraint(
            "management_ip",
            "management_port",
            name=op.f("uq_devices_management_ip_management_port"),
        ),
    )
    op.create_index(op.f("ix_devices_platform"), "devices", ["platform"])
    op.create_index(op.f("ix_devices_role"), "devices", ["role"])
    op.create_index(op.f("ix_devices_status"), "devices", ["status"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("type", _enum("jobtype", JOB_TYPES), nullable=False),
        sa.Column("status", _enum("jobstatus", JOB_STATUSES), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("intent_source", sa.String(length=64), nullable=True),
        sa.Column("parent_job_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("confirmed_by", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        _timestamp("created_at"),
        _timestamp("started_at", nullable=True),
        _timestamp("finished_at", nullable=True),
        sa.ForeignKeyConstraint(
            ["parent_job_id"],
            ["jobs.id"],
            name=op.f("fk_jobs_parent_job_id_jobs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_index(op.f("ix_jobs_created_at"), "jobs", ["created_at"])
    op.create_index(op.f("ix_jobs_parent_job_id"), "jobs", ["parent_job_id"])
    op.create_index(op.f("ix_jobs_status"), "jobs", ["status"])
    op.create_index(op.f("ix_jobs_type"), "jobs", ["type"])

    op.create_table(
        "config_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("kind", _enum("snapshotkind", SNAPSHOT_KINDS), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        _timestamp("created_at"),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            name=op.f("fk_config_snapshots_device_id_devices"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_config_snapshots_job_id_jobs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_config_snapshots")),
    )
    op.create_index(
        "ix_config_snapshots_device_kind_created",
        "config_snapshots",
        ["device_id", "kind", "created_at"],
    )
    op.create_index(op.f("ix_config_snapshots_job_id"), "config_snapshots", ["job_id"])

    op.create_table(
        "drift_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("status", _enum("driftstatus", DRIFT_STATUSES), nullable=False),
        _timestamp("checked_at"),
        _json("unauthorized_lines"),
        _json("missing_lines"),
        sa.Column("remediation_config", sa.Text(), nullable=True),
        sa.Column("rollback_config", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            name=op.f("fk_drift_records_device_id_devices"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_drift_records_job_id_jobs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_drift_records")),
    )
    op.create_index("ix_drift_records_device_checked", "drift_records", ["device_id", "checked_at"])
    op.create_index(op.f("ix_drift_records_job_id"), "drift_records", ["job_id"])
    op.create_index(op.f("ix_drift_records_status"), "drift_records", ["status"])

    op.create_table(
        "job_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        _timestamp("created_at"),
        sa.Column("level", _enum("loglevel", LOG_LEVELS), nullable=False),
        sa.Column("step", sa.String(length=64), nullable=False),
        sa.Column("hostname", sa.String(length=253), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_job_logs_job_id_jobs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_logs")),
    )
    op.create_index(op.f("ix_job_logs_job_id"), "job_logs", ["job_id"])

    op.create_table(
        "job_targets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=True),
        sa.Column("hostname", sa.String(length=253), nullable=False),
        sa.Column("status", _enum("targetstatus", TARGET_STATUSES), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("running_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("intended_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("remediation_config", sa.Text(), nullable=True),
        sa.Column("rollback_config", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            name=op.f("fk_job_targets_device_id_devices"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["intended_snapshot_id"],
            ["config_snapshots.id"],
            name=op.f("fk_job_targets_intended_snapshot_id_config_snapshots"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_job_targets_job_id_jobs"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["running_snapshot_id"],
            ["config_snapshots.id"],
            name=op.f("fk_job_targets_running_snapshot_id_config_snapshots"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_targets")),
        sa.UniqueConstraint("job_id", "hostname", name=op.f("uq_job_targets_job_id_hostname")),
    )
    op.create_index(op.f("ix_job_targets_device_id"), "job_targets", ["device_id"])


def downgrade() -> None:
    op.drop_table("job_targets")
    op.drop_table("job_logs")
    op.drop_table("drift_records")
    op.drop_table("config_snapshots")
    op.drop_table("jobs")
    op.drop_table("devices")
