from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from netops.db import Base, enum_type, utcnow
from netops.enums import DriftStatus
from netops.models.device import Device


class DriftRecord(Base):
    __tablename__ = "drift_records"
    __table_args__ = (Index("ix_drift_records_device_checked", "device_id", "checked_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    status: Mapped[DriftStatus] = mapped_column(enum_type(DriftStatus), index=True)
    checked_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    unauthorized_lines: Mapped[list[str]] = mapped_column(default=list)
    missing_lines: Mapped[list[str]] = mapped_column(default=list)
    remediation_config: Mapped[str | None] = mapped_column(Text)
    rollback_config: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)

    device: Mapped[Device] = relationship()
