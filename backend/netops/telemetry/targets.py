"""Платформа управляет сборщиками, а не собирает всё сама.

Для каждого устройства из базы определяем лучший доказанно работающий протокол
(detect_optimal_telemetry_protocol) и раскладываем устройства по целям внешних сборщиков:

- gnmic_targets.yaml             — file loader gnmic (gNMI-подписки, без паролей: они в env gnmic);
- prometheus_snmp_targets (БД)     — http_sd джобы snmp_fabric / snmp_hardware (snmp-exporter);
- prometheus_exporter_targets (БД) — http_sd джобы node_exporter_fleet.

Устройства, для которых ничего не доказано, остаются на SSH CLI-опросе платформы.
Цели для Prometheus отдаются напрямую из БД через HTTP SD endpoints без JSON-файлов на диске.
"""

from __future__ import annotations

import contextlib
import logging
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from netops.db import utcnow
from netops.enums import TelemetryProtocol
from netops.intent import InventoryDevice
from netops.models import Device
from netops.services.state import get_state_manager
from netops.services.telemetry import (
    detect_optimal_telemetry_protocol,
)
from netops.settings import Settings
from netops.telemetry import fallback
from netops.telemetry.sd_builder import (
    EXPORTER_TARGETS_FILE,
    GNMI_RELAY_BASE_PORT,
    GNMIC_TARGETS_FILE,
    PROTOCOL_OVERRIDES_KEY,
    PROTOCOLS_STATE_KEY,
    SNMP_RELAY_BASE_PORT,
    SNMP_TARGETS_FILE,
    _apply_protocol_override,
    _atomic_write,
    _get_sd_targets,
    _get_snmp_yml_community,
    _sanitize_huawei_snmp,
    build_target_files,
    get_exporter_sd_targets,
    get_snmp_sd_targets,
    invalidate_sd_targets_cache,
    sync_grafana_dashboard_to_db,
)
from netops.toolchain import Toolchain, build_toolchain

logger = logging.getLogger(__name__)

invalidate_collector_targets_cache = invalidate_sd_targets_cache

__all__ = [
    "EXPORTER_TARGETS_FILE",
    "GNMIC_TARGETS_FILE",
    "GNMI_RELAY_BASE_PORT",
    "PROTOCOLS_STATE_KEY",
    "PROTOCOL_OVERRIDES_KEY",
    "SNMP_RELAY_BASE_PORT",
    "SNMP_TARGETS_FILE",
    "_apply_protocol_override",
    "_atomic_write",
    "_get_sd_targets",
    "_get_snmp_yml_community",
    "_sanitize_huawei_snmp",
    "build_collector_matrix",
    "build_target_files",
    "clear_device_protocol_override",
    "get_exporter_sd_targets",
    "get_protocol_overrides",
    "get_snmp_sd_targets",
    "invalidate_collector_targets_cache",
    "invalidate_sd_targets_cache",
    "is_externally_collected",
    "last_collector_plan",
    "refresh_collector_targets",
    "refresh_telemetry_targets",
    "resolve_device_protocol",
    "set_device_protocol_override",
    "sync_grafana_dashboard_to_db",
]



def get_protocol_overrides() -> dict[str, str]:
    sm = get_state_manager()
    raw = sm.get_json(PROTOCOL_OVERRIDES_KEY) or sm.get_db_state(PROTOCOL_OVERRIDES_KEY, {})
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def _make_ssh_trigger_sender(device: Device, toolchain: Toolchain) -> Callable[[str], str] | None:
    if toolchain.health_probe.__class__.__name__ != "ScrapliNetworkDriver":
        return None
    try:
        from netops.network.scrapli_driver import _ParamikoConnAdapter

        target = toolchain.target_for(InventoryDevice.model_validate(device))

        def _send(cmd: str) -> str:
            with _ParamikoConnAdapter(target, timeout=6) as conn:
                return str(conn.send_command(cmd).result or "")

        return _send
    except Exception:
        return None


def _probe_device(
    device: Device, toolchain: Toolchain, settings: Settings, detect: Callable[..., dict]
) -> dict[str, Any]:
    try:
        creds = toolchain.target_for(InventoryDevice.model_validate(device)).credentials
        username, password = creds.username, creds.password
    except Exception:
        username = password = None
    community = settings.snmp_community.get_secret_value() if settings.snmp_community else None
    override = get_protocol_overrides().get(device.hostname)
    extra_kwargs: dict[str, Any] = {}
    if detect is detect_optimal_telemetry_protocol:
        import shutil

        from netops.services import protocol_probe

        yml_comm = _get_snmp_yml_community()
        comms = [c for c in (yml_comm, community) if c]
        if comms:
            community = ",".join(dict.fromkeys(str(c) for c in comms))
        ssh_send = _make_ssh_trigger_sender(device, toolchain)
        is_live = toolchain.health_probe.__class__.__name__ == "ScrapliNetworkDriver"
        extra_kwargs["timeout"] = 3.0 if is_live else 0.2
        if not shutil.which("gnmic"):
            extra_kwargs["gnmi_probe"] = protocol_probe.grpc_h2_capabilities
        if ssh_send is not None:
            extra_kwargs["ssh_send"] = ssh_send
        if override:
            extra_kwargs["preferred_protocol"] = override
    probe_host = str(device.management_ip)
    stand_host = getattr(settings, "clab_host", None)
    port_map = getattr(settings, "clab_port_map", None) or {}
    mapped_ip = port_map.get(str(device.management_port))
    try:
        import ipaddress

        ip_obj = ipaddress.ip_address(mapped_ip or probe_host)
        if ip_obj.is_private and device.platform.value != "linux_server":
            if stand_host:
                probe_host = str(stand_host)
            extra_kwargs["snmp_port"] = SNMP_RELAY_BASE_PORT + int(ip_obj.packed[-1])
            extra_kwargs["gnmi_port"] = GNMI_RELAY_BASE_PORT + int(ip_obj.packed[-1])
    except ValueError:
        _ = None
    try:
        result = detect(
            probe_host,
            device.platform.value,
            management_port=device.management_port,
            username=username,
            password=password,
            snmp_community=community,
            **extra_kwargs,
        )
    except Exception as exc:
        logger.warning("Проба протоколов для %s упала: %s", device.hostname, exc)
        result = {"protocol": TelemetryProtocol.SSH_CLI, "error": str(exc)}
    protocol = result.get("protocol", TelemetryProtocol.SSH_CLI)
    proto_val = str(getattr(protocol, "value", protocol))

    base_item = _sanitize_huawei_snmp(
        {
            **result,
            "protocol": proto_val,
            "host": probe_host,
            "platform": device.platform.value,
            "fabric_id": device.fabric_id or "default",
        },
        device.platform.value,
        device.management_port,
    )
    return _apply_protocol_override(
        base_item, override, device.platform.value, device.management_port
    )


def _persist_collector_plan(
    plan: dict[str, dict[str, Any]],
    settings: Settings,
    *,
    overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    files = build_target_files(plan)
    _atomic_write(
        settings.offline_lab_path / GNMIC_TARGETS_FILE,
        yaml.safe_dump(files[GNMIC_TARGETS_FILE], sort_keys=True, allow_unicode=True),
    )
    sm = get_state_manager()
    sm.set_db_state(SNMP_TARGETS_FILE, files[SNMP_TARGETS_FILE])
    sm.set_db_state(EXPORTER_TARGETS_FILE, files[EXPORTER_TARGETS_FILE])
    sm.set_db_state("sd_targets_dirty", False)

    summary: dict[str, Any] = {
        "refreshed_at": utcnow().isoformat(),
        "protocols": dict(Counter(item.get("protocol", "ssh_cli") for item in plan.values())),
        "devices": plan,
    }
    if overrides is not None:
        summary["overrides"] = overrides
    sm.set_json(PROTOCOLS_STATE_KEY, summary)
    sm.set_db_state(PROTOCOLS_STATE_KEY, summary)
    return summary


def refresh_collector_targets(
    session: Session,
    toolchain: Toolchain,
    settings: Settings,
    *,
    detect: Callable[..., dict] = detect_optimal_telemetry_protocol,
    max_workers: int = 32,
) -> dict[str, Any]:
    devices = list(session.scalars(select(Device).order_by(Device.hostname)).all())
    workers = max(1, min(max_workers, len(devices) or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        probed = list(pool.map(lambda d: _probe_device(d, toolchain, settings, detect), devices))
    plan = {dev.hostname: item for dev, item in zip(devices, probed, strict=True)}
    prev = (last_collector_plan() or {}).get("devices") or {}
    overrides = get_protocol_overrides()
    by_host = {d.hostname: d for d in devices}
    fallback.confirm_downgrades(
        prev, plan, overrides, lambda h: _probe_device(by_host[h], toolchain, settings, detect)
    )
    live_overrides = {h: v for h, v in overrides.items() if h in plan}
    if live_overrides != overrides:
        sm = get_state_manager()
        sm.set_json(PROTOCOL_OVERRIDES_KEY, live_overrides)
        sm.set_db_state(PROTOCOL_OVERRIDES_KEY, live_overrides)
    summary = _persist_collector_plan(plan, settings)
    fallback.record_changes(prev, plan)
    sync_grafana_dashboard_to_db()
    return summary


def last_collector_plan() -> dict[str, Any] | None:
    sm = get_state_manager()
    cached = sm.get_json(PROTOCOLS_STATE_KEY)
    if isinstance(cached, dict):
        return cached
    db_val = sm.get_db_state(PROTOCOLS_STATE_KEY)
    return db_val if isinstance(db_val, dict) else None


def is_externally_collected(hostname: str) -> bool:
    plan = last_collector_plan()
    if not plan:
        return False
    proto = (plan.get("devices") or {}).get(hostname, {}).get("protocol")
    return proto in (
        TelemetryProtocol.GNMI.value,
        TelemetryProtocol.PROMETHEUS_EXPORTER.value,
        TelemetryProtocol.SNMP.value,
    )


def clear_device_protocol_override(hostname: str) -> dict[str, str]:
    sm = get_state_manager()
    overrides = get_protocol_overrides()
    overrides.pop(hostname, None)
    sm.set_json(PROTOCOL_OVERRIDES_KEY, overrides)
    sm.set_db_state(PROTOCOL_OVERRIDES_KEY, overrides)
    invalidate_sd_targets_cache()
    return overrides


def resolve_device_protocol(
    device_or_host: Any, toolchain: Toolchain | None = None, settings: Settings | None = None
) -> dict[str, Any]:
    from netops.settings import get_settings
    from netops.toolchain import build_toolchain

    if hasattr(device_or_host, "hostname") and hasattr(device_or_host, "platform"):
        cfg = settings or get_settings()
        return _probe_device(device_or_host, toolchain or build_toolchain(cfg), cfg, detect_optimal_telemetry_protocol)
    plat = str(device_or_host.get("platform", "huawei_vrp") if isinstance(device_or_host, dict) else device_or_host)
    item = detect_optimal_telemetry_protocol("127.0.0.1", plat, timeout=0.2)
    item["protocol"] = str(getattr(item.get("protocol"), "value", item.get("protocol", "ssh_cli")))
    return _sanitize_huawei_snmp(dict(item), plat, 22)


class _CollectorMatrix(dict):
    def get(self, key: Any, default: Any = None) -> Any:
        if super().__contains__(key):
            return super().get(key, default)
        devs = super().get("devices")
        return devs.get(key, default) if isinstance(devs, dict) else default

    def __getitem__(self, key: Any) -> Any:
        if super().__contains__(key):
            return super().__getitem__(key)
        devs = super().get("devices")
        if isinstance(devs, dict) and key in devs:
            return devs[key]
        raise KeyError(key)


def build_collector_matrix(
    session: Session | None = None, toolchain: Toolchain | None = None, settings: Settings | None = None
) -> dict[str, Any]:
    plan_doc = last_collector_plan() or {"refreshed_at": None, "protocols": {}, "devices": {}}
    devices_map = {
        h: _sanitize_huawei_snmp(dict(v), str(v.get("platform", "linux_server")), int(v.get("port") or 22))
        for h, v in (plan_doc.get("devices") or {}).items()
        if isinstance(v, dict)
    }
    if session is not None:
        for d in session.scalars(select(Device).order_by(Device.hostname)).all():
            if d.hostname not in devices_map:
                devices_map[d.hostname] = resolve_device_protocol(d, toolchain, settings)
    return _CollectorMatrix({**plan_doc, "devices": devices_map, "overrides": get_protocol_overrides()})


def set_device_protocol_override(
    session: Session,
    toolchain: Toolchain,
    settings: Settings,
    hostname: str,
    protocol: str,
) -> dict[str, Any]:
    sm = get_state_manager()
    overrides = get_protocol_overrides()
    norm = (
        TelemetryProtocol.PROMETHEUS_EXPORTER.value
        if protocol == "node_exporter"
        else protocol.strip().lower()
    )
    if norm in ("auto", ""):
        overrides.pop(hostname, None)
    else:
        overrides[hostname] = norm
    sm.set_json(PROTOCOL_OVERRIDES_KEY, overrides)
    sm.set_db_state(PROTOCOL_OVERRIDES_KEY, overrides)

    device = session.scalar(select(Device).where(Device.hostname == hostname))
    plan_doc = last_collector_plan() or {"refreshed_at": utcnow().isoformat(), "protocols": {}, "devices": {}}
    devices_map = dict(plan_doc.get("devices") or {})
    is_live = toolchain.health_probe.__class__.__name__ == "ScrapliNetworkDriver"
    if hostname in devices_map and isinstance(devices_map[hostname], dict) and norm not in ("auto", ""):
        base = dict(devices_map[hostname])
        plat = str(device.platform.value if device else base.get("platform", "linux_server"))
        mport = int(device.management_port if device else (base.get("port") or 22))
        devices_map[hostname] = _apply_protocol_override(base, norm, plat, mport)
    elif device is not None and is_live:
        devices_map[hostname] = _probe_device(device, toolchain, settings, detect_optimal_telemetry_protocol)
    elif device is not None:
        base = {
            "protocol": TelemetryProtocol.SSH_CLI.value,
            "port": device.management_port,
            "host": str(device.management_ip),
            "platform": device.platform.value,
            "fabric_id": device.fabric_id or "default",
        }
        devices_map[hostname] = _apply_protocol_override(
            base, norm, device.platform.value, device.management_port
        )
    return _persist_collector_plan(devices_map, settings, overrides=overrides)


def refresh_telemetry_targets(session: Any, container: Any) -> None:
    with contextlib.suppress(Exception):
        invalidate_collector_targets_cache()
        toolchain = build_toolchain(container.settings)
        refresh_collector_targets(session, toolchain, container.settings)

