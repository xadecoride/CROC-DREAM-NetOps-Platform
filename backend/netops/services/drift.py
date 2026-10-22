from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from netops.enums import DriftStatus
from netops.models import Device, DriftRecord


def latest_drift_records(
    session: Session,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    status: DriftStatus | None = None,
    fabric_id: str | None = None,
    site: str | None = None,
) -> Sequence[DriftRecord]:
    ranked = select(
        DriftRecord.id,
        func.row_number()
        .over(
            partition_by=DriftRecord.device_id,
            order_by=(DriftRecord.checked_at.desc(), DriftRecord.id.desc()),
        )
        .label("position"),
    )
    if since is not None:
        ranked = ranked.where(DriftRecord.checked_at >= since)
    if until is not None:
        ranked = ranked.where(DriftRecord.checked_at <= until)
    latest = ranked.subquery()

    query = (
        select(DriftRecord)
        .join(latest, (latest.c.id == DriftRecord.id) & (latest.c.position == 1))
        .join(DriftRecord.device)
        .options(joinedload(DriftRecord.device))
        .order_by(Device.hostname)
    )
    if status is not None:
        query = query.where(DriftRecord.status == status)
    if fabric_id is not None:
        query = query.where((Device.fabric_id == fabric_id) | (Device.site == fabric_id))
    if site is not None:
        query = query.where((Device.site == site) | (Device.fabric_id == site))
    return session.scalars(query).all()


SAFE_KEYWORDS = (
    "description ",
    "logging ",
    "syslog",
    "ntp ",
    "snmp",
    "banner ",
    "remark ",
    "ip domain",
    "alias ",
)

RISKY_KEYWORDS = (
    "bgp ",
    "neighbor ",
    "remote-as",
    "router-id",
    "address-family",
    "interface ",
    "ip address",
    "shutdown",
    "undo shutdown",
    "no shutdown",
    "mtu ",
    "vlan ",
    "access-list ",
    "acl ",
    "rule ",
    "permit ",
    "deny ",
    "route-map ",
    "ip route",
    "ip prefix-list",
    "spanning-tree",
)


def classify_drift_lines(lines: Sequence[str]) -> tuple[list[str], list[str], str]:
    safe: list[str] = []
    risky: list[str] = []
    for raw in lines:
        line = raw.strip()
        lower = line.lower()
        if any(kw in lower for kw in RISKY_KEYWORDS):
            risky.append(line)
        elif any(kw in lower for kw in SAFE_KEYWORDS):
            safe.append(line)
        else:
            risky.append(line)
    risk_level = "HIGH" if risky else "LOW"
    return safe, risky, risk_level
