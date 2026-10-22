from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Body, Query, status

from netops.api.deps import JobServiceDep, Operator, SessionDep, Viewer
from netops.enums import DriftStatus
from netops.schemas.drift import DriftRemediateRequest, DriftReportItem, DriftScanRequest
from netops.schemas.jobs import JobAccepted
from netops.services.drift import classify_drift_lines, latest_drift_records

router = APIRouter(prefix="/drift", tags=["drift"])


@router.post(
    "/scan",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Внеочередной скан дрейфа",
)
def start_scan(
    service: JobServiceDep,
    user: Operator,
    request: Annotated[DriftScanRequest | None, Body()] = None,
) -> JobAccepted:
    device_ids = request.device_ids if request is not None else None
    fabric_id = request.fabric_id if request is not None else None
    site = request.site if request is not None else None
    if user.allowed_system:
        from sqlalchemy import select

        from netops.api.routes.devices import (
            assert_device_allowed,
            assert_stand_allowed,
            device_allowed,
        )
        from netops.models import Device

        if fabric_id or site:
            assert_stand_allowed(fabric_id or "", site, user.allowed_system)
        if device_ids:
            for dev in service._session.scalars(select(Device).where(Device.id.in_(device_ids))):
                assert_device_allowed(dev, user.allowed_system)
        elif not fabric_id and not site:
            all_devs = service._session.scalars(select(Device).order_by(Device.hostname)).all()
            device_ids = [d.id for d in all_devs if device_allowed(d, user.allowed_system)]
    job = service.create_drift_scan(
        device_ids, fabric_id=fabric_id, site=site, requested_by=user.username
    )
    return JobAccepted(job_id=job.id, status=job.status)


@router.get(
    "/report",
    response_model=list[DriftReportItem],
    summary="Последняя проверка дрейфа по каждому устройству",
)
def drift_report(
    session: SessionDep,
    principal: Viewer,
    since: datetime | None = None,
    until: datetime | None = None,
    status_filter: Annotated[DriftStatus | None, Query(alias="status")] = None,
    fabric_id: Annotated[str | None, Query(description="Фильтр по стенду/фабрике")] = None,
    site: Annotated[str | None, Query(description="Фильтр по площадке/стенду")] = None,
) -> list[DriftReportItem]:
    from netops.api.routes.devices import assert_stand_allowed, device_allowed

    if principal.allowed_system and (fabric_id or site):
        assert_stand_allowed(fabric_id or "", site, principal.allowed_system)

    records = latest_drift_records(
        session,
        since=_as_utc(since),
        until=_as_utc(until),
        status=status_filter,
        fabric_id=fabric_id,
        site=site,
    )
    items = []
    for record in records:
        if not device_allowed(record.device, principal.allowed_system):
            continue
        all_lines = (record.unauthorized_lines or []) + (record.missing_lines or [])
        safe, risky, risk_level = classify_drift_lines(all_lines)
        items.append(
            DriftReportItem(
                device_id=record.device_id,
                hostname=record.device.hostname,
                fabric_id=getattr(record.device, "fabric_id", "croc-dc1-pod1"),
                site=getattr(record.device, "site", None),
                status=record.status,
                checked_at=record.checked_at,
                job_id=record.job_id,
                unauthorized_lines=record.unauthorized_lines,
                missing_lines=record.missing_lines,
                remediation_patch=record.remediation_config,
                error=record.error,
                safe_lines=safe,
                risky_lines=risky,
                risk_level=risk_level,
            )
        )
    return items


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


@router.post(
    "/remediate",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Устранить дрейф: вернуть устройство к эталону",
)
def remediate(
    request: DriftRemediateRequest, service: JobServiceDep, user: Operator
) -> JobAccepted:
    if user.allowed_system:
        from netops.api.routes.devices import assert_device_allowed
        from netops.models import Device

        dev = service._session.get(Device, request.device_id)
        if dev is not None:
            assert_device_allowed(dev, user.allowed_system)
    job = service.create_drift_remediation(request.device_id, requested_by=user.username)
    return JobAccepted(job_id=job.id, status=job.status)


@router.get(
    "/regular-report",
    summary="Регулярный сводный отчёт по состоянию сети, дрейфу и SLA",
)
def regular_report(
    session: SessionDep,
    principal: Viewer,
    fabric_id: Annotated[str | None, Query(description="Фильтр по стенду/фабрике")] = None,
    site: Annotated[str | None, Query(description="Фильтр по площадке/стенду")] = None,
) -> dict[str, object]:
    from sqlalchemy import select

    from netops.api.routes.devices import assert_stand_allowed, device_allowed
    from netops.enums import DeviceStatus
    from netops.models import Device

    stand = fabric_id or site
    if principal.allowed_system and stand:
        assert_stand_allowed(fabric_id or "", site, principal.allowed_system)

    devices = [
        d
        for d in session.scalars(select(Device)).all()
        if device_allowed(d, principal.allowed_system)
        and (stand is None or stand in {d.fabric_id, d.site})
    ]
    total = len(devices)
    in_sync = sum(1 for d in devices if d.status == DeviceStatus.IN_SYNC)
    drifted = sum(1 for d in devices if d.status == DeviceStatus.DRIFT_DETECTED)
    unreachable = sum(1 for d in devices if d.status == DeviceStatus.UNREACHABLE)
    compliance_pct = round((in_sync / total) * 100.0, 2) if total > 0 else 100.0
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "total_devices": total,
        "in_sync": in_sync,
        "drift_detected": drifted,
        "unreachable": unreachable,
        "compliance_pct": compliance_pct,
        "devices": [
            {
                "hostname": d.hostname,
                "platform": str(d.platform.value if hasattr(d.platform, "value") else d.platform),
                "role": str(d.role.value if hasattr(d.role, "value") else d.role),
                "rack": getattr(d, "rack", None),
                "unit": getattr(d, "unit", None),
                "status": str(d.status.value if hasattr(d.status, "value") else d.status),
            }
            for d in devices
        ],
    }
