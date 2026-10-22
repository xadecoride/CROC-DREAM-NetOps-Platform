from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from netops.db import Base, enum_type, utcnow
from netops.enums import DeviceRole, DeviceStatus, Platform


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint("fabric_id", "management_ip", "management_port"),
        Index("ix_devices_fabric_id_hostname", "fabric_id", "hostname"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    hostname: Mapped[str] = mapped_column(String(253), unique=True)
    management_ip: Mapped[str] = mapped_column(String(45))
    management_port: Mapped[int] = mapped_column(default=22, server_default="22")
    platform: Mapped[Platform] = mapped_column(enum_type(Platform), index=True)
    role: Mapped[DeviceRole] = mapped_column(enum_type(DeviceRole), index=True)
    auth_profile: Mapped[str] = mapped_column(String(64))
    status: Mapped[DeviceStatus] = mapped_column(
        enum_type(DeviceStatus),
        default=DeviceStatus.UNKNOWN,
        server_default=DeviceStatus.UNKNOWN.value,
        index=True,
    )
    management_mode: Mapped[str] = mapped_column(String(32), default="MONITORING_ONLY", server_default="MONITORING_ONLY")
    fabric_id: Mapped[str] = mapped_column(
        String(64),
        default="croc-dc1-pod1",
        server_default="croc-dc1-pod1",
        nullable=False,
        index=True,
    )
    site: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None, index=True)
    rack: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    unit: Mapped[int | None] = mapped_column(nullable=True, default=None)
    proxy_jump: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hardware_specs: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    last_checked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("fabric_id", "croc-dc1-pod1")
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return f"<Device id={self.id} hostname={self.hostname!r} status={self.status}>"
