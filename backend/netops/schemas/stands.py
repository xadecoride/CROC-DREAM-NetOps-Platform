from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from netops.schemas.devices import DeviceRead


class _StandConnectionBase(BaseModel):
    model_config = ConfigDict(extra="ignore")

    site: str | None = None
    jump_host: str | None = None
    jump_user: str | None = None
    jump_password: str | None = None
    port_map: dict[str, Any] | None = None
    topology_path: str | None = None
    intent_subpath: str | None = None
    description: str | None = None


class StandCreate(_StandConnectionBase):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=128)
    driver: str = "scrapli"
    jump_port: int = Field(default=22, ge=1, le=65535)
    queue_name: str = "default"


class StandUpdate(_StandConnectionBase):
    name: str | None = None
    driver: str | None = None
    jump_port: int | None = Field(default=None, ge=1, le=65535)
    queue_name: str | None = None
    status: str | None = None


class StandRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    site: str | None = None
    driver: str = "scrapli"
    jump_host: str | None = None
    jump_port: int = 22
    jump_user: str | None = None
    has_password: bool = False
    port_map: dict[str, Any] = Field(default_factory=dict)
    topology_path: str | None = None
    intent_subpath: str | None = None
    queue_name: str = "default"
    status: str = "ONLINE"
    vitality_state: str = "UP"
    down_reason: str | None = None
    ai_diagnosis: str | None = None
    using_backup_fallback: bool = False
    last_backup_at: str | None = None
    backup_available: bool = True
    description: str | None = None
    is_default: bool = False
    is_active: bool = False
    total_devices: int = 0
    in_sync_devices: int = 0
    drifted_devices: int = 0
    unreachable_devices: int = 0
    in_sync: int = 0
    drifted: int = 0
    unreachable: int = 0
    devices: list[DeviceRead] = Field(default_factory=list)
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None

    @model_validator(mode="before")
    @classmethod
    def _sync_stat_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            d = dict(data)
            in_sync = d.get("in_sync_devices", d.get("in_sync", 0))
            drifted = d.get("drifted_devices", d.get("drifted", 0))
            unreach = d.get("unreachable_devices", d.get("unreachable", 0))
            is_def = bool(d.get("is_active", d.get("is_default", False)))
            d["in_sync_devices"] = in_sync
            d["in_sync"] = in_sync
            d["drifted_devices"] = drifted
            d["drifted"] = drifted
            d["unreachable_devices"] = unreach
            d["unreachable"] = unreach
            d["is_default"] = is_def
            d["is_active"] = is_def
            return d
        return data


class StandProbeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    jump_host: str | None = None
    jump_port: int = Field(default=22, ge=1, le=65535)
    jump_user: str | None = None
    jump_password: str | None = None
    port_map: dict[str, str] | None = None
    timeout_sec: float = Field(default=3.0, ge=0.5, le=30.0)


class StandProbeResponse(BaseModel):
    reachable: bool
    jump_host: str | None = None
    jump_port: int = 22
    latency_ms: float | None = None
    discovered_nodes: list[dict[str, Any]] = Field(default_factory=list)
    message: str
