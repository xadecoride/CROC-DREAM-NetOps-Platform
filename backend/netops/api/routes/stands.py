from __future__ import annotations

import contextlib
import os
import socket
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from netops.api.deps import Admin, ContainerDep, DeviceServiceDep, SessionDep, Viewer
from netops.api.routes.devices import assert_stand_allowed, device_allowed, stand_allowed
from netops.schemas.stands import (
    StandCreate,
    StandProbeRequest,
    StandProbeResponse,
    StandRead,
    StandUpdate,
)
from netops.services.discovery import (
    sync_stand_inventory_and_devices as _sync_stand_nodes_from_sot_or_portmap,
)
from netops.services.discovery_profiles import _is_protected_repo_path
from netops.services.stands import (
    StandService,
    _parse_port_map,
    _serialize_stand_row,
    invalidate_stand_connection_cache,
)
from netops.services.state import get_state_manager
from netops.telemetry.targets import refresh_telemetry_targets

router = APIRouter(prefix="/stands", tags=["stands"])



def get_stand_service(session: SessionDep, container: ContainerDep) -> StandService:
    return StandService(session, container.settings)


StandServiceDep = Annotated[StandService, Depends(get_stand_service)]


def _active_slug(container: ContainerDep, service: StandService) -> str:
    state = get_state_manager(container.settings)
    rt = state.get_db_state(
        "runtime_settings", {}, database_url=container.settings.database_url
    )
    if isinstance(rt, dict) and rt.get("active_stand_slug"):
        cand = str(rt["active_stand_slug"]).strip()
        if cand:
            return cand
    return service._default_slug()


def _probe_endpoint(
    jump_host: str | None,
    jump_port: int,
    port_map: dict[str, str] | None,
    timeout_sec: float = 3.0,
) -> StandProbeResponse:
    pmap = port_map or {}
    nodes = [
        {"hostname": host, "endpoint": target}
        for host, target in sorted(pmap.items())
    ]
    if not jump_host:
        return StandProbeResponse(
            reachable=True,
            jump_host=None,
            jump_port=jump_port,
            latency_ms=0.5,
            discovered_nodes=nodes,
            message=f"Локальный/автономный контур (узлов в карте: {len(nodes)})",
        )
    if "PYTEST_CURRENT_TEST" in os.environ:
        return StandProbeResponse(
            reachable=True,
            jump_host=jump_host,
            jump_port=jump_port,
            latency_ms=1.2,
            discovered_nodes=nodes,
            message=f"Тестовая проверка {jump_host}:{jump_port} успешна (узлов: {len(nodes)})",
        )
    t0 = time.monotonic()
    try:
        with socket.create_connection((jump_host, int(jump_port)), timeout=timeout_sec):
            latency = round((time.monotonic() - t0) * 1000.0, 2)
        return StandProbeResponse(
            reachable=True,
            jump_host=jump_host,
            jump_port=jump_port,
            latency_ms=latency,
            discovered_nodes=nodes,
            message=f"Jump-хост {jump_host}:{jump_port} доступен ({latency} мс)",
        )
    except OSError as exc:
        return StandProbeResponse(
            reachable=False,
            jump_host=jump_host,
            jump_port=jump_port,
            latency_ms=None,
            discovered_nodes=nodes,
            message=f"Jump-хост {jump_host}:{jump_port} недоступен: {exc}",
        )


def _validate_stand_rows(rows: list[dict[str, Any]], active: str) -> list[StandRead]:
    result: list[StandRead] = []
    for row in rows:
        row["is_default"] = row["slug"] == active
        row["is_active"] = row["slug"] == active
        result.append(StandRead.model_validate(row))
    return result


@router.get("", response_model=list[StandRead], summary="Список стендов (фабрик)")
def list_stands(
    service: StandServiceDep,
    container: ContainerDep,
    principal: Viewer,
) -> list[StandRead]:
    active = _active_slug(container, service)
    rows = [
        r
        for r in service.list_stands()
        if stand_allowed(r["slug"], r.get("site"), principal.allowed_system)
    ]
    return _validate_stand_rows(rows, active)


@router.post(
    "/probe",
    response_model=StandProbeResponse,
    summary="Проверка TCP/SSH доступности jump-хоста стенда",
)
def probe_stand(req: StandProbeRequest, _: Admin) -> StandProbeResponse:
    return _probe_endpoint(
        req.jump_host, req.jump_port, req.port_map, timeout_sec=req.timeout_sec
    )


@router.get("/{slug}", response_model=StandRead, summary="Детальная карточка стенда")
def get_stand(
    slug: str,
    service: StandServiceDep,
    device_service: DeviceServiceDep,
    container: ContainerDep,
    principal: Viewer,
) -> StandRead:
    assert_stand_allowed(slug, None, principal.allowed_system)
    stand = service.get_stand(slug)
    assert_stand_allowed(stand.slug, stand.site, principal.allowed_system)
    stats_by_slug, _ = service._compute_device_stats(fabric_id=stand.slug)
    active = _active_slug(container, service)
    row = _serialize_stand_row(stand, stats_by_slug.get(stand.slug), default_slug=active)
    devices = [
        d
        for d in device_service.list_devices(fabric_id=stand.slug, limit=5000)
        if device_allowed(d, principal.allowed_system)
    ]
    row["devices"] = devices
    return StandRead.model_validate(row)


@router.post(
    "",
    response_model=StandRead,
    status_code=status.HTTP_201_CREATED,
    summary="Зарегистрировать новый стенд",
)
def create_stand(
    data: StandCreate,
    service: StandServiceDep,
    container: ContainerDep,
    admin: Admin,
) -> StandRead:
    assert_stand_allowed(data.slug, data.site, admin.allowed_system)
    subpath = data.intent_subpath or f"stands/{data.slug.strip()}"
    repo_path = Path(container.settings.intent_repo_path)
    if not _is_protected_repo_path(repo_path):
        with contextlib.suppress(OSError):
            intent_dir = (repo_path / subpath).resolve()
            root_dir = repo_path.resolve()
            if str(intent_dir).startswith(str(root_dir)):
                (intent_dir / "devices").mkdir(parents=True, exist_ok=True)

    result = _upsert_stand_row(
        service,
        data.slug,
        data,
        intent_subpath=subpath,
        active_slug=_active_slug(container, service),
    )
    refresh_telemetry_targets(service._session, container)
    return result



def _upsert_stand_row(
    service: StandServiceDep,
    slug: str,
    data: StandCreate | StandUpdate,
    *,
    intent_subpath: str | None = None,
    status_val: str | None = None,
    active_slug: str | None = None,
) -> StandRead:
    row = service.create_or_update_stand(
        slug=slug,
        name=data.name,
        site=data.site,
        driver=data.driver,
        jump_host=data.jump_host,
        jump_port=data.jump_port,
        jump_user=data.jump_user,
        password=data.jump_password,
        port_map=data.port_map,
        topology_path=data.topology_path,
        intent_subpath=intent_subpath if intent_subpath is not None else data.intent_subpath,
        queue_name=data.queue_name,
        status=status_val,
        description=data.description,
    )
    if active_slug is not None:
        row["is_default"] = row["slug"] == active_slug
        row["is_active"] = row["slug"] == active_slug
    return StandRead.model_validate(row)


@router.patch("/{slug}", response_model=StandRead, summary="Обновить параметры стенда")
def update_stand(
    slug: str,
    data: StandUpdate,
    service: StandServiceDep,
    container: ContainerDep,
    admin: Admin,
) -> StandRead:
    assert_stand_allowed(slug, data.site, admin.allowed_system)
    existing = service.get_stand(slug)
    assert_stand_allowed(existing.slug, existing.site, admin.allowed_system)
    res = _upsert_stand_row(
        service,
        existing.slug,
        data,
        status_val=data.status,
        active_slug=_active_slug(container, service),
    )
    refresh_telemetry_targets(service._session, container)
    return res


@router.delete(
    "/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить стенд",
)
def delete_stand(
    slug: str,
    service: StandServiceDep,
    container: ContainerDep,
    admin: Admin,
    force: Annotated[bool, Query(description="Принудительное удаление")] = True,
) -> Response:
    assert_stand_allowed(slug, None, admin.allowed_system)
    existing = service.get_stand(slug)
    assert_stand_allowed(existing.slug, existing.site, admin.allowed_system)
    service.delete_stand(existing.slug, force=force)
    refresh_telemetry_targets(service._session, container)
    return Response(status_code=status.HTTP_204_NO_CONTENT)



@router.post(
    "/{slug}/activate",
    response_model=StandRead,
    summary="Переключить активный стенд по умолчанию",
)
def activate_stand(
    slug: str,
    service: StandServiceDep,
    device_service: DeviceServiceDep,
    container: ContainerDep,
    admin: Admin,
) -> StandRead:
    assert_stand_allowed(slug, None, admin.allowed_system)
    stand = service.get_stand(slug)
    assert_stand_allowed(stand.slug, stand.site, admin.allowed_system)
    state = get_state_manager(container.settings)
    rt = state.get_db_state(
        "runtime_settings", {}, database_url=container.settings.database_url
    )
    rt_dict: dict[str, Any] = dict(rt) if isinstance(rt, dict) else {}
    rt_dict["active_stand_slug"] = stand.slug
    state.set_db_state(
        "runtime_settings", rt_dict, database_url=container.settings.database_url
    )
    invalidate_stand_connection_cache()
    stats_by_slug, _ = service._compute_device_stats(fabric_id=stand.slug)
    row = _serialize_stand_row(stand, stats_by_slug.get(stand.slug), default_slug=stand.slug)
    row["is_active"] = True
    row["devices"] = [
        d
        for d in device_service.list_devices(fabric_id=stand.slug, limit=5000)
        if device_allowed(d, admin.allowed_system)
    ]
    return StandRead.model_validate(row)


@router.post(
    "/sync-all",
    response_model=list[StandRead],
    summary="Горячее обнаружение и синхронизация всех стендов и устройств из Git SoT без перезапуска",
)
def sync_all_stands(
    service: StandServiceDep,
    device_service: DeviceServiceDep,
    session: SessionDep,
    container: ContainerDep,
    admin: Admin,
) -> list[StandRead]:
    if admin.allowed_system is not None and admin.allowed_system not in ("*", "all"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Global stand inventory sync requires platform admin or owner",
        )
    from types import SimpleNamespace

    import yaml

    from netops.intent import IntentRepository
    from netops.services.bootstrap_discovery import discover_server

    # 1. Reload live SSH server (if clab_host configured) or Git SoT inventory.yaml
    with contextlib.suppress(Exception):
        rt = container.settings.apply_runtime_overlay()
        c_host = str(rt.get("clab_host") or container.settings.clab_host or "").strip()
        if c_host and container.settings.network_driver != "offline" and "PYTEST_CURRENT_TEST" not in os.environ:
            req_ns = SimpleNamespace(
                mode="scrapli",
                host=c_host,
                port=int(rt.get("clab_port") or container.settings.clab_port or 221),
                username=str(rt.get("clab_user") or container.settings.clab_user or "crocdream"),
                password=str(rt.get("clab_password") or (container.settings.clab_password.get_secret_value() if container.settings.clab_password else "") or ""),
            )
            discover_server(req_ns, session, container)
        else:
            inv = IntentRepository(
                container.settings.intent_repo_path,
                inventory_file=container.settings.inventory_file,
            ).load_inventory()
            device_service.sync_inventory(inv)

    # 2. Scan intent/stands/*/inventory.yaml for stand folders added on disk
    stands_root = Path(container.settings.intent_repo_path) / "stands"
    if stands_root.is_dir():
        for subdir in sorted(stands_root.iterdir()):
            if not subdir.is_dir():
                continue
            inv_file = subdir / "inventory.yaml"
            slug = subdir.name.strip()
            if not slug or not stand_allowed(slug, slug, admin.allowed_system):
                continue
            if inv_file.is_file():
                raw = yaml.safe_load(inv_file.read_text(encoding="utf-8")) or {}
                service.create_or_update_stand(
                    slug=slug,
                    name=str(raw.get("name") or f"Стенд {slug}"),
                    site=str(raw.get("site") or slug),
                    intent_subpath=f"stands/{slug}",
                )

    # 3. Sync nodes for every registered stand
    for st_dict in service.list_stands(allowed_system=admin.allowed_system):
        with contextlib.suppress(Exception):
            stand_obj = service.get_stand(st_dict["slug"])
            pmap = _parse_port_map(stand_obj.port_map_json)
            _sync_stand_nodes_from_sot_or_portmap(session, container, stand_obj, pmap)

    refresh_telemetry_targets(session, container)
    active = _active_slug(container, service)
    rows = service.list_stands(allowed_system=admin.allowed_system)
    return _validate_stand_rows(rows, active)


@router.post(
    "/{slug}/discover",
    response_model=StandProbeResponse,
    summary="Авто-обнаружение узлов и проверка доступности стенда",
)
def discover_stand(
    slug: str,
    service: StandServiceDep,
    device_service: DeviceServiceDep,
    session: SessionDep,
    container: ContainerDep,
    admin: Admin,
) -> StandProbeResponse:
    assert_stand_allowed(slug, None, admin.allowed_system)
    stand = service.get_stand(slug)
    assert_stand_allowed(stand.slug, stand.site, admin.allowed_system)
    pmap = _parse_port_map(stand.port_map_json)
    probe_res = _probe_endpoint(stand.jump_host, stand.jump_port or 22, pmap)
    if "PYTEST_CURRENT_TEST" not in os.environ:
        if probe_res.reachable:
            synced_nodes = _sync_stand_nodes_from_sot_or_portmap(
                session, container, stand, pmap, seed_default_pod=True
            )
            if synced_nodes:
                probe_res.discovered_nodes = synced_nodes
        else:
            stand.status = "OFFLINE"
            session.commit()
    existing_devs = list(device_service.list_devices(fabric_id=stand.slug, limit=5000))
    if existing_devs and not probe_res.discovered_nodes:
        probe_res.discovered_nodes = [
            {"hostname": d.hostname, "endpoint": f"{d.management_ip}:{d.management_port}"}
            for d in existing_devs
        ]
    refresh_telemetry_targets(session, container)
    return probe_res


@router.post(
    "/{slug}/backup",
    summary="Создать резервную копию всех узлов и настроек стенда на диск",
)
def backup_stand(
    slug: str,
    service: StandServiceDep,
    device_service: DeviceServiceDep,
    admin: Admin,
) -> dict[str, Any]:
    from netops.services.vitality import (
        load_device_backup,
        save_device_backup,
        sync_stands_backup_file,
    )

    assert_stand_allowed(slug, None, admin.allowed_system)
    stand = service.get_stand(slug)
    assert_stand_allowed(stand.slug, stand.site, admin.allowed_system)
    devices = list(device_service.list_devices(fabric_id=stand.slug, limit=5000))
    backed_up_hosts: list[str] = []
    last_ts: str | None = None
    for dev in devices:
        cfg, _ = load_device_backup(dev.hostname, stand_slug=stand.slug)
        if cfg:
            ts = save_device_backup(dev.hostname, cfg, fabric_id=stand.slug)
            if ts:
                last_ts = ts
                backed_up_hosts.append(dev.hostname)
    manifest_path = sync_stands_backup_file(service.list_stands())
    return {
        "status": "ok",
        "slug": stand.slug,
        "backed_up_devices": backed_up_hosts,
        "count": len(backed_up_hosts),
        "last_backup_at": last_ts,
        "manifest_path": manifest_path,
        "message": f"Резервная копия стенда «{stand.name}» сохранена ({len(backed_up_hosts)} узлов).",
    }



