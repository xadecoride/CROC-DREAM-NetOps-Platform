from __future__ import annotations

import os
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response, status

from netops.api.deps import Admin, ContainerDep, DeviceServiceDep, Viewer
from netops.enums import DeviceRole, DeviceStatus, Platform
from netops.intent import IntentValidationError
from netops.intent.netbox import load_netbox_inventory
from netops.models import Device
from netops.schemas.devices import (
    DeviceCreate,
    DeviceDetail,
    DeviceEmergencyAction,
    DeviceEmergencyActionResult,
    DeviceProbeRequest,
    DeviceProbeResult,
    DeviceRead,
    DeviceUpdate,
    InventorySyncResult,
)
from netops.schemas.intent import IntentIssueRead
from netops.services.discovery import (
    bind_device_to_sot,
    execute_device_emergency_action,
    remove_device_from_sot,
    run_device_probe,
)
from netops.telemetry.targets import refresh_telemetry_targets

router = APIRouter(tags=["devices"])



def _iter_allowed_tokens(allowed_system: str | None) -> list[tuple[str, str]]:
    if not allowed_system or allowed_system in ("*", "all"):
        return []
    tokens: list[tuple[str, str]] = []
    for raw_token in allowed_system.split(","):
        token = raw_token.strip().lower()
        if not token:
            continue
        if token in ("*", "all"):
            return []
        target = (
            token.removeprefix("stand:")
            .removeprefix("fabric:")
            .removeprefix("site:")
            .removeprefix("platform:")
        )
        tokens.append((token, target))
    return tokens


def stand_allowed(slug: str, site: str | None, allowed_system: str | None) -> bool:
    tokens = _iter_allowed_tokens(allowed_system)
    if not tokens:
        return True
    slug_l = (slug or "").lower()
    site_l = (site or "").lower()
    return any(
        (slug_l and slug_l in (token, target)) or (site_l and site_l in (token, target))
        for token, target in tokens
    )


def assert_stand_allowed(slug: str, site: str | None, allowed_system: str | None) -> None:
    if not stand_allowed(slug, site, allowed_system):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Доступ вашей ссылки ограничен системой {allowed_system}",
        )


def device_allowed(device: Any, allowed_system: str | None) -> bool:
    tokens = _iter_allowed_tokens(allowed_system)
    if not tokens:
        return True
    plat = getattr(device, "platform", None)
    plat_str = str(getattr(plat, "value", plat) or "").lower()
    hostname = str(getattr(device, "hostname", "") or "")
    dev_id = str(getattr(device, "id", "") or "")
    fabric = str(getattr(device, "fabric_id", "") or "")
    site = str(getattr(device, "site", "") or "")

    for token, target in tokens:
        if (
            hostname.lower() == token
            or hostname.lower() == target
            or dev_id == token
            or fabric.lower() in (token, target)
            or site.lower() in (token, target)
            or plat_str in (token, target)
            or (target == "huawei" and "huawei" in plat_str)
            or (target == "arista" and "arista" in plat_str)
            or (target == "frr" and "frr" in plat_str)
            or (target == "server" and "server" in plat_str)
        ):
            return True
    return False


def filter_devices_by_system(devices: Any, allowed_system: str | None) -> list[Any]:
    items = list(devices)
    if not allowed_system:
        return items
    return [d for d in items if device_allowed(d, allowed_system)]


def list_allowed_devices(
    service: Any,
    allowed_system: str | None,
    *,
    limit: int = 5000,
    fabric_id: str | None = None,
) -> list[Device]:
    """Fetch devices from DB with SQL-level allowed_tokens filter + Python RBAC guard."""
    rows = service.list_devices(
        limit=limit,
        fabric_id=fabric_id,
        allowed_tokens=_iter_allowed_tokens(allowed_system),
    )
    return [d for d in rows if device_allowed(d, allowed_system)]


def assert_device_allowed(device: Any, allowed_system: str | None) -> None:
    if not device_allowed(device, allowed_system):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Доступ вашей ссылки ограничен системой {allowed_system}",
        )


@router.get("/devices", response_model=list[DeviceRead], summary="Список устройств")
def list_devices(
    service: DeviceServiceDep,
    principal: Viewer,
    status_filter: Annotated[DeviceStatus | None, Query(alias="status")] = None,
    role: DeviceRole | None = None,
    platform: Platform | None = None,
    fabric_id: Annotated[str | None, Query(description="Фильтр по стенду/фабрике")] = None,
    site: Annotated[str | None, Query(description="Фильтр по площадке/стенду")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Device]:
    devices = service.list_devices(
        status=status_filter,
        role=role,
        platform=platform,
        fabric_id=fabric_id,
        site=site,
        limit=limit,
        offset=offset,
        allowed_tokens=_iter_allowed_tokens(principal.allowed_system),
    )
    return list(devices)


@router.get("/devices/{device_id}", response_model=DeviceDetail, summary="Карточка устройства")
def get_device(
    device_id: int, service: DeviceServiceDep, container: ContainerDep, principal: Viewer
) -> DeviceDetail:
    device = service.get(device_id)
    assert_device_allowed(device, principal.allowed_system)
    from netops.services.discovery_parsers import _needs_hardware_specs

    if (
        _needs_hardware_specs(device.hardware_specs)
        and device.status != DeviceStatus.UNREACHABLE
        and container.settings.network_driver != "offline"
        and "PYTEST_CURRENT_TEST" not in os.environ
    ):
        try:
            import json

            from netops.intent.models import InventoryDevice
            from netops.network.scrapli_driver import _ParamikoConnAdapter
            from netops.services.discovery_parsers import (
                _parse_ssh_hardware_specs,
                _parse_ssh_topology,
            )
            from netops.services.telemetry import collect_metrics_from_cli
            from netops.toolchain import build_toolchain

            toolchain = build_toolchain(container.settings)
            target = toolchain.target_for(InventoryDevice.model_validate(device))
            with _ParamikoConnAdapter(target, timeout=10, proxy_jump=device.proxy_jump) as conn:
                _, os_ver, cpu_cores, ram_gb, disk_gb = _parse_ssh_hardware_specs(
                    conn, device.platform, device.platform.value
                )
                _, ifaces, lldp = _parse_ssh_topology(conn, device.platform, device.hostname)
                collect_metrics_from_cli(
                    device.hostname,
                    device.platform.value,
                    lambda c: conn.send_command(c).result,
                )
                device.hardware_specs = json.dumps(
                    {
                        "os_version": os_ver,
                        "cpu_cores": cpu_cores,
                        "ram_gb": ram_gb,
                        "disk_gb": disk_gb,
                        "interfaces": ifaces,
                        "lldp_neighbors": lldp,
                    },
                    ensure_ascii=False,
                )
                service._session.commit()
                service._enrich_device(device)
        except Exception:
            _ = None
    detail = DeviceDetail.model_validate(device)
    try:
        detail.intent = container.intents.load_device_intent(device.hostname)
    except IntentValidationError as exc:
        detail.intent_issues = [IntentIssueRead.model_validate(issue) for issue in exc.issues]
    return detail


@router.post(
    "/devices/probe",
    response_model=DeviceProbeResult,
    summary="Автопроверка и автоопределение типа устройства",
)
def probe_device(req: DeviceProbeRequest, container: ContainerDep, _: Admin) -> DeviceProbeResult:
    return run_device_probe(req, container)


@router.post(
    "/devices",
    response_model=DeviceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Добавить устройство",
)
def create_device(
    data: DeviceCreate, service: DeviceServiceDep, container: ContainerDep, admin: Admin
) -> Device:
    from sqlalchemy import select

    from netops.errors import ConflictError
    from netops.services.notifier import sync_access_registry

    assert_device_allowed(data, admin.allowed_system)
    try:
        device = service.create(data)
    except ConflictError:
        if data.hardware_specs is None and data.username is None:
            raise
        existing = service._session.scalars(
            select(Device).where(
                (Device.fabric_id == data.fabric_id)
                & (Device.management_ip == str(data.management_ip))
                & (Device.management_port == data.management_port)
            )
        ).first() or service._session.scalars(
            select(Device).where(Device.hostname == data.hostname)
        ).first()
        if existing is None:
            raise
        assert_device_allowed(existing, admin.allowed_system)
        if existing.status is DeviceStatus.IN_PROGRESS:
            raise ConflictError(
                f"Device {existing.hostname} is being deployed; try again later"
            ) from None
        hostname_taken = service._session.scalars(
            select(Device).where(
                (Device.hostname == data.hostname) & (Device.id != existing.id)
            )
        ).first()
        existing.hostname = existing.hostname if hostname_taken else data.hostname
        existing.management_ip = str(data.management_ip)
        existing.management_port = data.management_port
        existing.platform = data.platform
        existing.role = data.role
        existing.auth_profile = data.auth_profile
        existing.management_mode = data.management_mode
        existing.proxy_jump = data.proxy_jump
        if data.hardware_specs is not None:
            existing.hardware_specs = data.hardware_specs
        service._commit(f"Device {existing.hostname}")
        device = existing
    if data.username or (
        container.settings.network_driver != "offline" and not os.environ.get("PYTEST_CURRENT_TEST")
    ):
        bind_device_to_sot(data, device, container)
        device.status = DeviceStatus.UNKNOWN
        service._session.commit()
    sync_access_registry(service._session, container.settings)
    refresh_telemetry_targets(service._session, container)
    return service.get(device.id)


@router.patch("/devices/{device_id}", response_model=DeviceRead, summary="Изменить устройство")
def update_device(
    device_id: int,
    data: DeviceUpdate,
    service: DeviceServiceDep,
    container: ContainerDep,
    admin: Admin,
) -> Device:
    device = service.get(device_id)
    assert_device_allowed(device, admin.allowed_system)
    updated = service.update(device_id, data)
    refresh_telemetry_targets(service._session, container)
    return updated


@router.delete(
    "/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Удалить устройство"
)
def delete_device(
    device_id: int, service: DeviceServiceDep, container: ContainerDep, admin: Admin
) -> Response:
    from netops.services.notifier import sync_access_registry

    device = service.get(device_id)
    assert_device_allowed(device, admin.allowed_system)
    hostname = device.hostname
    service.delete(device_id)
    remove_device_from_sot(
        hostname,
        container.settings.intent_repo_path,
        container.settings.offline_lab_path,
    )
    sync_access_registry(service._session, container.settings)
    refresh_telemetry_targets(service._session, container)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/devices/{device_id}/promote",
    response_model=DeviceRead,
    summary="Promote device from MONITORING_ONLY to MANAGED",
)
def promote_device(device_id: int, service: DeviceServiceDep, admin: Admin) -> Device:
    device = service.get(device_id)
    assert_device_allowed(device, admin.allowed_system)
    device.management_mode = "MANAGED"
    service._session.commit()
    return device


@router.post(
    "/devices/{device_id}/emergency-action",
    response_model=DeviceEmergencyActionResult,
    summary="Execute emergency remediation action on device",
)
def device_emergency_action(
    device_id: int,
    data: DeviceEmergencyAction,
    service: DeviceServiceDep,
    container: ContainerDep,
    admin: Admin,
) -> dict[str, Any]:
    device = service.get(device_id)
    assert_device_allowed(device, admin.allowed_system)
    return execute_device_emergency_action(device, data, container, admin.username)


@router.post(
    "/devices/{device_id}/restore-backup",
    summary="Восстановить устройство из последней рабочей резервной копии (.cfg)",
)
def restore_device_backup_route(
    device_id: int,
    service: DeviceServiceDep,
    admin: Admin,
) -> dict[str, Any]:
    from netops.services.vitality import restore_device_from_backup

    device = service.get(device_id)
    assert_device_allowed(device, admin.allowed_system)
    return restore_device_from_backup(device, service._session)


@router.post(
    "/inventory/sync",
    response_model=InventorySyncResult,
    summary="Import devices from NetBox (if configured) or inventory.yaml of the intent repository",
)
def sync_inventory(
    service: DeviceServiceDep, container: ContainerDep, admin: Admin
) -> InventorySyncResult:
    if admin.allowed_system is not None and admin.allowed_system not in ("*", "all"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Global inventory/Git SoT sync requires platform admin or owner",
        )
    import yaml
    from sqlalchemy import select

    from netops.intent.models import Inventory, InventoryDevice
    from netops.models import Stand
    from netops.services.notifier import sync_access_registry

    settings = container.settings
    if settings.netbox_url and settings.netbox_token:
        inventory = load_netbox_inventory(
            settings.netbox_url, settings.netbox_token.get_secret_value()
        )
    else:
        inventory = container.intents.load_inventory()
        stands = list(service._session.scalars(select(Stand).order_by(Stand.id)).all())
        if stands:
            by_host = {d.hostname: d for d in inventory.devices}
            for st in stands:
                st_inv_path = settings.intent_repo_path / "stands" / st.slug / "inventory.yaml"
                if st_inv_path.is_file():
                    raw = yaml.safe_load(st_inv_path.read_text(encoding="utf-8")) or {}
                    for item in raw.get("devices") or []:
                        if isinstance(item, dict) and item.get("hostname"):
                            by_host[str(item["hostname"])] = InventoryDevice.model_validate(item)
            inventory = Inventory(devices=list(by_host.values()))
    res = service.sync_inventory(inventory)
    sync_access_registry(service._session, settings)
    return res
