from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from netops.enums import DriftStatus


class DriftScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_ids: list[int] | None = Field(
        default=None, min_length=1, description="Устройства для скана; если не указаны — все"
    )
    fabric_id: str | None = Field(default=None, description="Идентификатор стенда/фабрики")
    site: str | None = Field(default=None, description="Псевдоним стенда/фабрики")


class DriftRemediateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: int


class DriftReportItem(BaseModel):
    device_id: int
    hostname: str
    fabric_id: str = "croc-dc1-pod1"
    site: str | None = None
    status: DriftStatus
    checked_at: datetime
    job_id: uuid.UUID | None
    unauthorized_lines: list[str]
    missing_lines: list[str]
    remediation_patch: str | None
    error: str | None
    safe_lines: list[str] = Field(default_factory=list)
    risky_lines: list[str] = Field(default_factory=list)
    risk_level: str = "LOW"
