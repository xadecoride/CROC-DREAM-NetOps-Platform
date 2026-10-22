"""API routes for Multi-Layer Network Topology, Link Toggling, and Commutation Path Tracing."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from netops.api.deps import ContainerDep, DeviceServiceDep, Operator, SessionDep, Viewer
from netops.api.routes.devices import (
    _iter_allowed_tokens,
    assert_stand_allowed,
    device_allowed,
)
from netops.services.topology import (
    build_topology_graph,
    delete_custom_link,
    get_link_overrides,
    toggle_topology_link,
    trace_commutation_path,
    upsert_custom_link,
)
from netops.services.topology_resilience import (
    analyze_link_spof,
    create_topology_checkpoint,
    delete_topology_checkpoint,
    list_topology_checkpoints,
    restore_topology_checkpoint,
    sync_link_to_lab_config,
    toggle_node_drain,
)

router = APIRouter(prefix="/topology", tags=["topology"])


class TopologyLinkCreateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    source_id: int
    target_id: int
    source_port: str = Field(default="Ethernet1", min_length=1, max_length=64)
    target_port: str = Field(default="Ethernet1", min_length=1, max_length=64)
    layer: str = Field(default="L3_SUBNET")
    subnet: str = Field(default="10.10.99.0/31")
    protocol: str = Field(default="LLDP + eBGP")
    bgp_asn: str | None = None
    vxlan_vni: int | None = None
    admin_state: str = Field(default="UP")
    description: str | None = None


class TopologyLinkToggleRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    admin_state: str | None = None
    reason: str | None = None
    force: bool = False
    strict_spof_guard: bool = False
    auto_revert_seconds: int | None = None


class TopologyNodeDrainRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    drained: bool | None = None
    reason: str | None = None


class TopologyCheckpointCreateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(default="Golden Baseline Checkpoint", min_length=1, max_length=120)


def _resolve_topology_devices(
    device_service: DeviceServiceDep,
    allowed_system: str | None,
    fabric_id: str | None,
) -> tuple[str | None, list[Any]]:
    eff_fabric = fabric_id if fabric_id and fabric_id != "all" else None
    if eff_fabric:
        assert_stand_allowed(eff_fabric, None, allowed_system)
    tokens = _iter_allowed_tokens(allowed_system)
    devs = [
        d
        for d in device_service.list_devices(fabric_id=eff_fabric, limit=5000, allowed_tokens=tokens)
        if device_allowed(d, allowed_system)
    ]
    return eff_fabric, devs


@router.get("/graph", summary="Полный мульти-слойный граф топологии (L2 LLDP / L3 IP / BGP / Inter-Stand)")
def get_topology_graph(
    device_service: DeviceServiceDep,
    session: SessionDep,
    container: ContainerDep,
    principal: Viewer,
    fabric_id: Annotated[str | None, Query(description="Фильтр по стенду (slug) или 'all'")] = None,
) -> dict[str, Any]:
    fab, devs = _resolve_topology_devices(device_service, principal.allowed_system, fabric_id)
    return build_topology_graph(devs, session, container.settings, fabric_id=fab)


@router.get("/trace", summary="Трассировка маршрута коммутации и пайплайна между двумя узлами (ECMP + VXLAN + BGP)")
def get_commutation_trace(
    source_id: Annotated[int, Query(description="ID исходного устройства")],
    target_id: Annotated[int, Query(description="ID целевого устройства")],
    device_service: DeviceServiceDep,
    session: SessionDep,
    container: ContainerDep,
    principal: Viewer,
    fabric_id: Annotated[str | None, Query(description="Фильтр по стенду (slug) или 'all'")] = None,
) -> dict[str, Any]:
    eff_fabric, all_devices = _resolve_topology_devices(
        device_service, principal.allowed_system, fabric_id
    )
    return trace_commutation_path(
        all_devices,
        session,
        source_id=source_id,
        target_id=target_id,
        settings=container.settings,
        fabric_id=eff_fabric,
    )


@router.post(
    "/links",
    status_code=status.HTTP_201_CREATED,
    summary="Создать или переопределить связь между коммутаторами / стендами",
)
def create_or_update_topology_link(
    req: TopologyLinkCreateRequest,
    device_service: DeviceServiceDep,
    container: ContainerDep,
    operator: Operator,
) -> dict[str, Any]:
    if req.source_id == req.target_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя замкнуть связь устройства на само себя",
        )
    src = device_service.get(req.source_id)
    dst = device_service.get(req.target_id)
    if not device_allowed(src, operator.allowed_system) or not device_allowed(
        dst, operator.allowed_system
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступ к стенду ограничен")
    default_stand = (
        getattr(container.settings, "default_fabric_id", "croc-dc1-pod1") or "croc-dc1-pod1"
    )
    st1 = getattr(src, "fabric_id", None) or getattr(src, "site", None) or default_stand
    st2 = getattr(dst, "fabric_id", None) or getattr(dst, "site", None) or default_stand
    payload = {
        **req.model_dump(),
        "source_hostname": src.hostname,
        "target_hostname": dst.hostname,
        "source_stand": st1,
        "target_stand": st2,
        "fabric_id": st1,
    }
    return upsert_custom_link(payload, container.settings)


def _find_allowed_topology_link(
    link_id: str,
    fabric_id: str | None,
    operator: Operator,
    device_service: DeviceServiceDep,
    session: SessionDep,
    container: ContainerDep,
) -> tuple[dict[str, Any], dict[str, Any]]:
    eff_fabric = fabric_id if fabric_id and fabric_id != "all" else None
    if eff_fabric:
        assert_stand_allowed(eff_fabric, None, operator.allowed_system)
    all_devices = [
        d
        for d in device_service.list_devices(
            fabric_id=eff_fabric,
            limit=5000,
            allowed_tokens=_iter_allowed_tokens(operator.allowed_system),
        )
        if device_allowed(d, operator.allowed_system)
    ]
    graph = build_topology_graph(
        all_devices, session, container.settings, fabric_id=eff_fabric
    )
    target_link = next((lnk for lnk in graph.get("links", []) if str(lnk.get("id")) == link_id), None)
    if target_link is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Link '{link_id}' not found in allowed stand topology",
        )
    return graph, target_link


@router.patch(
    "/links/{link_id}/toggle",
    summary="Переключить состояние связи (UP / SHUTDOWN) с проверкой SPOF и авто-возвратом",
)
def toggle_link_state(
    link_id: str,
    req: TopologyLinkToggleRequest,
    device_service: DeviceServiceDep,
    session: SessionDep,
    container: ContainerDep,
    operator: Operator,
    fabric_id: Annotated[str | None, Query(description="Фильтр по стенду (slug) или 'all'")] = None,
) -> dict[str, Any]:
    graph, target_link = _find_allowed_topology_link(
        link_id, fabric_id, operator, device_service, session, container
    )
    overrides = get_link_overrides(container.settings)
    cur = overrides.get(link_id) or target_link or {}
    next_state = (
        req.admin_state.upper()
        if req.admin_state in ("UP", "SHUTDOWN", "up", "shutdown")
        else ("UP" if cur.get("admin_state") == "SHUTDOWN" else "SHUTDOWN")
    )

    spof = {"spof_warning": False, "isolated_nodes": []}
    if next_state == "SHUTDOWN":
        spof = analyze_link_spof(graph, link_id)
        if req.strict_spof_guard and spof["spof_warning"] and not req.force:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": f"Отключение связи изолирует узлы: {', '.join(spof['isolated_nodes'])}",
                    "isolated_nodes": spof["isolated_nodes"],
                    "spof_warning": True,
                },
            )

    entry = toggle_topology_link(
        link_id=link_id,
        admin_state=next_state,
        reason=req.reason,
        settings=container.settings,
        auto_revert_seconds=req.auto_revert_seconds,
    )
    synced_hosts = sync_link_to_lab_config(target_link, next_state, container.settings)
    return {
        **entry,
        **spof,
        "synced_lab_configs": synced_hosts,
    }


@router.delete(
    "/links/{link_id}",
    summary="Удалить пользовательскую связь или сбросить переопределение",
)
def remove_topology_link(
    link_id: str,
    device_service: DeviceServiceDep,
    session: SessionDep,
    container: ContainerDep,
    operator: Operator,
    fabric_id: Annotated[str | None, Query(description="Фильтр по стенду (slug) или 'all'")] = None,
) -> dict[str, Any]:
    _find_allowed_topology_link(
        link_id, fabric_id, operator, device_service, session, container
    )
    removed = delete_custom_link(link_id, container.settings)
    return {"deleted": removed, "link_id": link_id}


@router.post(
    "/nodes/{device_id}/drain",
    summary="Перевести узел в режим обслуживания (BGP Graceful Drain GSHUT 65535:0) или вернуть в строй",
)
def drain_topology_node(
    device_id: int,
    req: TopologyNodeDrainRequest,
    device_service: DeviceServiceDep,
    container: ContainerDep,
    operator: Operator,
) -> dict[str, Any]:
    dev = device_service.get(device_id)
    if not device_allowed(dev, operator.allowed_system):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступ к стенду ограничен")
    return toggle_node_drain(
        device_id=device_id,
        drained=req.drained,
        reason=req.reason,
        settings=container.settings,
    )


@router.get(
    "/checkpoints",
    summary="Список именованных контрольных точек топологии и конфигураций",
)
def get_checkpoints(
    container: ContainerDep,
    _: Viewer,
) -> list[dict[str, Any]]:
    return list_topology_checkpoints(container.settings)


@router.post(
    "/checkpoints",
    status_code=status.HTTP_201_CREATED,
    summary="Создать именованный снимок (Checkpoint) топологии и конфигураций стенда",
)
def create_checkpoint(
    req: TopologyCheckpointCreateRequest,
    container: ContainerDep,
    _: Operator,
) -> dict[str, Any]:
    return create_topology_checkpoint(req.name, container.settings)


def _assert_global_checkpoint_scope(operator: Operator, action: str) -> None:
    if operator.allowed_system is not None and operator.allowed_system not in ("*", "all"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{action} global checkpoints requires platform admin or owner with global scope",
        )


@router.post(
    "/checkpoints/{checkpoint_id}/restore",
    summary="Восстановить топологию и конфигурации стенда из контрольной точки",
)
def restore_checkpoint(
    checkpoint_id: str,
    container: ContainerDep,
    operator: Operator,
) -> dict[str, Any]:
    _assert_global_checkpoint_scope(operator, "Restoring")
    try:
        return restore_topology_checkpoint(checkpoint_id, container.settings)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete(
    "/checkpoints/{checkpoint_id}",
    summary="Удалить контрольную точку топологии",
)
def remove_checkpoint(
    checkpoint_id: str,
    container: ContainerDep,
    operator: Operator,
) -> dict[str, Any]:
    _assert_global_checkpoint_scope(operator, "Deleting")
    deleted = delete_topology_checkpoint(checkpoint_id, container.settings)
    return {"deleted": deleted, "checkpoint_id": checkpoint_id}
