from __future__ import annotations

from datetime import datetime
from ipaddress import IPv4Address
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from netops.enums import DeviceRole, DeviceStatus, Platform
from netops.intent.models import (
    DeviceIntent,
    Hostname,
    Identifier,
)
from netops.intent.models import (
    normalize_stand_dict as _normalize_stand_dict,
)
from netops.schemas.common import PartialUpdate
from netops.schemas.intent import IntentIssueRead


class DeviceCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hostname: Hostname = Field(examples=["leaf-1.croc.lab"])
    management_ip: IPv4Address = Field(examples=["172.20.20.11"])
    management_port: int = Field(default=22, ge=1, le=65535)
    platform: Platform
    role: DeviceRole
    auth_profile: Identifier = Field(examples=["lab"])
    username: str | None = Field(default=None, exclude=True)
    password: str | None = Field(default=None, exclude=True)
    management_mode: Literal["MONITORING_ONLY", "MANAGED"] = "MONITORING_ONLY"
    fabric_id: str = "croc-dc1-pod1"
    site: str | None = None
    rack: str | None = None
    unit: int | None = None
    proxy_jump: str | None = None
    hardware_specs: str | None = None

    _normalize_stand = model_validator(mode="before")(_normalize_stand_dict)

    @field_validator("hostname", mode="before")
    @classmethod
    def sanitize_hostname(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip().replace("_", "-")
        return v

    @field_validator("management_ip", mode="before")
    @classmethod
    def sanitize_ip(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip().removeprefix("http://").removeprefix("https://")
            if ":" in v:
                v = v.split(":", 1)[0]
        return v


class DeviceUpdate(PartialUpdate):
    hostname: Hostname | None = None
    management_ip: IPv4Address | None = None
    management_port: int | None = Field(default=None, ge=1, le=65535)
    platform: Platform | None = None
    role: DeviceRole | None = None
    auth_profile: Identifier | None = None
    management_mode: Literal["MONITORING_ONLY", "MANAGED"] | None = None
    fabric_id: str | None = None
    site: str | None = None
    rack: str | None = None
    unit: int | None = None
    proxy_jump: str | None = None
    hardware_specs: str | None = None

    _normalize_stand = model_validator(mode="before")(_normalize_stand_dict)


class DeviceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    hostname: str
    management_ip: str
    management_port: int
    platform: Platform
    role: DeviceRole
    auth_profile: str
    status: DeviceStatus
    management_mode: str = "MONITORING_ONLY"
    fabric_id: str = "croc-dc1-pod1"
    site: str | None = None
    rack: str | None = None
    unit: int | None = None
    proxy_jump: str | None = None
    hardware_specs: str | None = None
    oper_status: Literal["UP", "DOWN", "DEGRADED", "UNKNOWN"] = "UNKNOWN"
    vitality_state: Literal["UP", "DOWN"] = "UP"
    down_reason: str | None = None
    ai_diagnosis: str | None = None
    using_backup_fallback: bool = False
    last_backup_at: str | None = None
    backup_available: bool = True
    sparkline: list[float] = Field(default_factory=list)
    state_timeline: list[str] = Field(default_factory=list)
    last_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DeviceDetail(DeviceRead):
    intent: DeviceIntent | None = None
    intent_issues: list[IntentIssueRead] = Field(default_factory=list)


class DeviceProbeRequest(BaseModel):
    management_ip: str
    management_port: int = 22
    username: str = "root"
    password: str | None = None
    proxy_jump: str | None = None  # e.g. "crocdream@198.51.100.10:221"


class DeviceProbeResult(BaseModel):
    reachable: bool
    detected_platform: Platform
    detected_role: DeviceRole
    hostname: str
    os_version: str | None = None
    cpu_cores: int | None = None
    ram_gb: float | None = None
    disk_gb: float | None = None
    interfaces: list[str] = Field(default_factory=list)
    lldp_neighbors: list[dict] = Field(default_factory=list)
    message: str


class DeviceEmergencyAction(BaseModel):
    action: Literal["rollback_last_commit", "reset_bgp_sessions", "restart_services", "reboot"]
    force: bool = False
    reason: str = "Emergency manual intervention"


class DeviceEmergencyActionResult(BaseModel):
    status: str
    action: str
    device: str
    message: str
    entry: dict[str, Any]


class InventorySyncResult(BaseModel):
    created: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)
