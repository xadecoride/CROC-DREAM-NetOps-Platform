from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from netops.db import utcnow
from netops.enums import DeviceRole, DeviceStatus, JobStatus, JobType, Platform, TelemetryProtocol
from netops.intent import IntentValidationError, InventoryDevice
from netops.models import Device, Job
from netops.network.base import HealthExpectations
from netops.services.state import get_state_manager
from netops.services.telemetry import record_live_metric, resolve_device_ports
from netops.telemetry import metrics
from netops.telemetry.metrics import Sample
from netops.toolchain import Toolchain

logger = logging.getLogger(__name__)

LATEST_SNAPSHOT_KEY = "netops:telemetry:latest"
HOST_SNAPSHOT_PREFIX = "netops:telemetry:host:"
FABRIC_SNAPSHOT_PREFIX = "netops:telemetry:fabric:"


# Один проход сбора: счётчики из базы и опрос стенда. Все замеры пишутся с одним
# collected_at, поэтому /metrics отдаёт срез последнего сбора.
def collect(
    session: Session,
    toolchain: Toolchain | None = None,
    *,
    include_fabric_label: bool = False,
    max_workers: int = 16,
) -> list[Sample]:
    import contextlib

    with contextlib.suppress(Exception):
        from netops.services.retention import pre_collect_cleanup

        pre_collect_cleanup(session)
    dev_samples = (
        device_samples(
            session,
            toolchain,
            max_workers=max_workers,
            include_fabric_label=include_fabric_label,
        )
        if toolchain is not None
        else []
    )
    samples = platform_samples(session)
    samples.extend(dev_samples)
    return samples


def _decode_snapshot(data: dict[str, Any] | None) -> tuple[list[Sample], datetime | None]:
    if not data:
        return [], None
    samples = [Sample(name, float(value), dict(labels)) for name, value, labels in data["samples"]]
    return samples, datetime.fromisoformat(data["collected_at"])


# Публикует срез последнего сбора в Redis (StateManager), шардирует по хостам/фабрикам и пушит в VictoriaMetrics.
# merge=True: срез одного шарда (merge_fabric; None — платформенные ряды без fabric_id) заменяет
# в общем снимке только свои ряды; live_fabrics отбрасывает ряды удалённых стендов.
def publish(
    samples: Sequence[Sample],
    *,
    ttl_seconds: int | None = None,
    merge: bool = False,
    merge_fabric: str | None = None,
    live_fabrics: set[str] | None = None,
) -> datetime:
    collected_at = utcnow()
    ts_iso = collected_at.isoformat()
    sm = get_state_manager()
    by_host: dict[str, list[list[Any]]] = {}
    by_fabric: dict[str, list[list[Any]]] = {}
    raw_all: list[list[Any]] = []
    for s in samples:
        lbls = dict(s.labels)
        row = [s.name, s.value, lbls]
        raw_all.append(row)
        if host := lbls.get("hostname"):
            by_host.setdefault(host, []).append(row)
        if fab := lbls.get("fabric_id"):
            by_fabric.setdefault(fab, []).append(row)

    if merge:
        import time

        holder = f"publish:{merge_fabric}"
        deadline = time.monotonic() + 5.0
        while not sm.acquire_sync_lock("telemetry:latest", holder, ttl=10) and (
            time.monotonic() < deadline
        ):
            time.sleep(0.05)
        try:
            old = (sm.get_json(LATEST_SNAPSHOT_KEY) or {}).get("samples", [])
            kept = [
                r
                for r in old
                if r[2].get("fabric_id") != merge_fabric
                and (live_fabrics is None or r[2].get("fabric_id") in live_fabrics | {None})
            ]
            sm.set_json(
                LATEST_SNAPSHOT_KEY,
                {"collected_at": ts_iso, "samples": kept + raw_all},
                ex=ttl_seconds,
            )
        finally:
            sm.release_sync_lock("telemetry:latest", holder)
    else:
        sm.set_json(
            LATEST_SNAPSHOT_KEY, {"collected_at": ts_iso, "samples": raw_all}, ex=ttl_seconds
        )
    for host, host_rows in by_host.items():
        sm.set_json(
            f"{HOST_SNAPSHOT_PREFIX}{host}",
            {"collected_at": ts_iso, "samples": host_rows},
            ex=ttl_seconds,
        )
    for fab, fab_rows in by_fabric.items():
        sm.set_json(
            f"{FABRIC_SNAPSHOT_PREFIX}{fab}",
            {"collected_at": ts_iso, "samples": fab_rows},
            ex=ttl_seconds,
        )
    return collected_at


def latest() -> tuple[list[Sample], datetime | None]:
    return _decode_snapshot(get_state_manager().get_json(LATEST_SNAPSHOT_KEY))


def latest_for_host(hostname: str) -> tuple[list[Sample], datetime | None]:
    return _decode_snapshot(get_state_manager().get_json(f"{HOST_SNAPSHOT_PREFIX}{hostname}"))


def latest_for_fabric(fabric_id: str) -> tuple[list[Sample], datetime | None]:
    return _decode_snapshot(get_state_manager().get_json(f"{FABRIC_SNAPSHOT_PREFIX}{fabric_id}"))


def platform_samples(session: Session) -> list[Sample]:
    samples: list[Sample] = []
    devices = session.scalars(select(Device).order_by(Device.hostname)).all()
    counts = dict.fromkeys(DeviceStatus, 0)
    for device in devices:
        counts[device.status] += 1
        for status in DeviceStatus:
            samples.append(
                Sample(
                    metrics.DEVICE_STATUS,
                    float(device.status is status),
                    {
                        "hostname": device.hostname,
                        "platform": device.platform.value,
                        "role": device.role.value,
                        "status": status.value,
                    },
                )
            )
    samples.extend(
        Sample(metrics.DEVICES_TOTAL, float(count), {"status": status.value})
        for status, count in counts.items()
    )
    samples.append(Sample(metrics.DRIFT_DEVICES, float(counts[DeviceStatus.DRIFT_DETECTED])))
    samples.append(Sample(metrics.DRIFT_DEVICES_TOTAL, float(counts[DeviceStatus.DRIFT_DETECTED])))

    from netops.telemetry import fallback, targets

    samples.extend(fallback.metric_samples((targets.last_collector_plan() or {}).get("devices") or {}))

    job_counts = session.execute(
        select(Job.type, Job.status, func.count()).group_by(Job.type, Job.status)
    ).all()
    seen = {(job_type, status) for job_type, status, _ in job_counts}
    samples.extend(
        Sample(metrics.JOBS_TOTAL, float(count), {"type": job_type.value, "status": status.value})
        for job_type, status, count in job_counts
    )
    samples.extend(
        Sample(metrics.JOBS_TOTAL, 0.0, {"type": job_type.value, "status": status.value})
        for job_type in JobType
        for status in JobStatus
        if (job_type, status) not in seen
    )

    finished = session.execute(
        select(Job.type, func.max(Job.finished_at))
        .where(Job.finished_at.is_not(None))
        .group_by(Job.type)
    ).all()
    samples.extend(
        Sample(metrics.JOB_LAST_FINISHED, finished_at.timestamp(), {"type": job_type.value})
        for job_type, finished_at in finished
        if finished_at is not None
    )
    return samples


_SERVER_METRICS: tuple[tuple[str, str], ...] = (
    ("cpu_pct", metrics.SERVER_CPU_UTIL),
    ("memory_pct", metrics.SERVER_MEMORY_UTIL),
    ("uplink_util_pct", metrics.DEVICE_UPLINK_UTIL),
    ("disk_util_pct", metrics.SERVER_DISK_UTIL),
    ("network_rate_mbps", metrics.SERVER_NETWORK_RATE),
    ("load_1m", metrics.SERVER_LOAD_1M),
    ("tcp_retransmits_rate", metrics.SERVER_TCP_RETRANSMITS),
    ("conntrack_usage_pct", metrics.SERVER_CONNTRACK_UTIL),
)


# Отдаём только то, что сервер реально вернул: пропуск лучше выдуманного значения.
def _emit_server_samples(health: Any, labels: dict[str, str], hostname: str) -> list[Sample]:
    res: list[Sample] = []
    for attr, metric_name in _SERVER_METRICS:
        raw = getattr(health, attr, None)
        if raw is None:
            continue
        val = float(raw)
        record_live_metric(hostname, attr, val)
        res.append(Sample(metric_name, val, labels))
        if attr == "cpu_pct":
            res.append(Sample(metrics.DEVICE_CPU_UTIL, val, labels))
        elif attr == "memory_pct":
            res.append(Sample(metrics.DEVICE_MEMORY_UTIL, val, labels))
    return res


def _emit_switch_samples(
    health: Any,
    labels: dict[str, str],
    hostname: str,
) -> list[Sample]:
    res: list[Sample] = []
    if health.cpu_pct is not None:
        val = float(health.cpu_pct)
        res.append(Sample(metrics.DEVICE_CPU_UTIL, val, labels))
        record_live_metric(hostname, "cpu_pct", val)
    if health.memory_pct is not None:
        val = float(health.memory_pct)
        res.append(Sample(metrics.DEVICE_MEMORY_UTIL, val, labels))
        record_live_metric(hostname, "memory_pct", val)
    if health.uplink_util_pct is not None:
        val = float(health.uplink_util_pct)
        res.append(Sample(metrics.DEVICE_UPLINK_UTIL, val, labels))
        record_live_metric(hostname, "uplink_util_pct", val)
    for attr, m_name in (
        ("interface_errors_rate", metrics.INTERFACE_ERRORS),
        ("fdb_mac_count", metrics.FDB_MAC_COUNT),
    ):
        raw_val = getattr(health, attr, None)
        if raw_val is not None:
            fval = float(raw_val)
            res.append(Sample(m_name, fval, labels))
            record_live_metric(hostname, attr, fval)

    for peer, session_state in health.bgp_sessions.items():
        peer_labels = labels | {"peer": peer, "state": session_state.state}
        res.append(Sample(metrics.BGP_SESSION_UP, float(session_state.established), peer_labels))
        if session_state.prefixes_accepted is not None:
            res.append(
                Sample(
                    metrics.BGP_PREFIXES_ACCEPTED,
                    float(session_state.prefixes_accepted),
                    labels | {"peer": peer},
                )
            )
    res.extend(
        Sample(
            metrics.INTERFACE_UP,
            float(state.is_up),
            labels | {"interface": name, "state": str(state)},
        )
        for name, state in health.interfaces.items()
    )
    res.extend(
        Sample(metrics.PING_LOSS, loss, labels | {"peer": peer})
        for peer, loss in health.ping_loss_percent.items()
    )
    return res


def _is_server_device(device: Device) -> bool:
    return (
        device.platform == Platform.LINUX_SERVER
        or getattr(device, "role", None) == DeviceRole.SERVER
        or "server" in str(device.platform).lower()
        or "server" in str(device.role).lower()
    )


def _build_probe_tasks(
    devices: list[Device],
    snapshot: Any,
    toolchain: Toolchain,
    include_fabric_label: bool,
) -> list[
    tuple[Device, dict[str, str], HealthExpectations, Any, Exception | None, dict[str, int]]
]:
    probe_tasks = []
    for device in devices:
        intent = snapshot.intent_for(device.hostname) if snapshot is not None else None
        expectations = (
            HealthExpectations.from_intent(intent)
            if intent is not None
            else HealthExpectations(interfaces={}, bgp_peers=())
        )
        labels = {"hostname": device.hostname}
        if include_fabric_label:
            labels["fabric_id"] = getattr(device, "fabric_id", "default") or "default"
        resolved_ports = resolve_device_ports(
            str(device.management_ip),
            management_port=device.management_port,
            platform=device.platform,
        )
        try:
            target = toolchain.target_for(InventoryDevice.model_validate(device))
            probe_tasks.append((device, labels, expectations, target, None, resolved_ports))
        except Exception as exc:
            probe_tasks.append((device, labels, expectations, None, exc, resolved_ports))
    return probe_tasks


def _probe_external_server(dev: Device, tgt: Any) -> bool:
    from netops.services import protocol_probe
    from netops.telemetry import targets

    if not targets.is_externally_collected(dev.hostname):
        return False
    dev_info = ((targets.last_collector_plan() or {}).get("devices") or {}).get(dev.hostname, {})
    proto = dev_info.get("protocol")
    port = dev_info.get("port")
    host = tgt.host or dev.management_ip
    try:
        if proto == TelemetryProtocol.PROMETHEUS_EXPORTER.value:
            return protocol_probe.prometheus_exporter_ok(
                host, int(port or 9100), dev_info.get("path") or "/metrics", timeout=1.5
            )
        if proto == TelemetryProtocol.GNMI.value:
            return protocol_probe.tcp_open(host, int(port or 6030), timeout=1.5)
        if proto == TelemetryProtocol.SNMP.value:
            return bool(protocol_probe.snmp_agent_versions(host, int(port or 161), timeout=1.5))
        return protocol_probe.tcp_open(host, int(tgt.port or 22), timeout=1.5)
    except Exception:
        return False


# Опрос стенда: что заявлено в intent, то и проверяем. Устройство, которое не ответило,
# отмечаем недоступным и идём дальше — сбор метрик не должен падать из-за одной ноды.
def device_samples(
    session: Session,
    toolchain: Toolchain,
    *,
    max_workers: int = 4,
    include_fabric_label: bool = False,
    fabric_id: str | None = None,
) -> list[Sample]:
    from concurrent.futures import ThreadPoolExecutor

    snapshot = None
    try:
        snapshot = toolchain.intents.load()
    except IntentValidationError as exc:
        logger.warning(
            "Intent содержит предупреждения (%s) — продолжаем сбор базовой телеметрии", exc
        )

    query = select(Device).order_by(Device.hostname)
    if fabric_id is not None:
        query = query.where(Device.fabric_id == fabric_id)
    devices = list(session.scalars(query).all())
    if not devices:
        return []

    probe_tasks = _build_probe_tasks(devices, snapshot, toolchain, include_fabric_label)
    # Закрываем транзакцию до SSH-прохода: не держим idle in transaction (expire_on_commit=False).
    session.commit()

    def _run_probe(
        item: tuple[
            Device, dict[str, str], HealthExpectations, Any, Exception | None, dict[str, int]
        ],
    ):
        dev, lbls, exp, tgt, prep_err, ports = item
        if prep_err is not None or tgt is None:
            return dev, lbls, None, prep_err, ports
        is_scrapli = toolchain.health_probe.__class__.__name__ == "ScrapliNetworkDriver"
        try:
            from netops.services.discovery_parsers import _needs_hardware_specs

            if (
                is_scrapli
                and _is_server_device(dev)
                and not _needs_hardware_specs(dev.hardware_specs)
                and _probe_external_server(dev, tgt)
            ):
                from netops.network.base import HealthSnapshot

                return dev, lbls, HealthSnapshot(bgp_sessions={}, interfaces={}, ping_loss_percent={}), None, ports, None
            if is_scrapli:
                exp = HealthExpectations(bgp_peers=(), interfaces=exp.interfaces)
            snap = toolchain.health_probe.snapshot(tgt, exp)
            hw_json: str | None = None
            if is_scrapli and _needs_hardware_specs(dev.hardware_specs):
                try:
                    import json

                    from netops.services.discovery_parsers import (
                        _parse_ssh_hardware_specs,
                        _parse_ssh_topology,
                    )

                    with toolchain.health_probe._get_connection(tgt) as conn:
                        _, os_ver, cpu_cores, ram_gb, disk_gb = _parse_ssh_hardware_specs(
                            conn, dev.platform, dev.platform.value
                        )
                        _, ifaces, lldp = _parse_ssh_topology(conn, dev.platform, dev.hostname)
                        hw_json = json.dumps(
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
                except Exception as hw_exc:
                    logger.debug("Hardware specs probe skipped for %s: %s", dev.hostname, hw_exc)
            return dev, lbls, snap, None, ports, hw_json
        except Exception as ssh_exc:
            if is_scrapli and _is_server_device(dev) and _probe_external_server(dev, tgt):
                from netops.network.base import HealthSnapshot

                return dev, lbls, HealthSnapshot(bgp_sessions={}, interfaces={}, ping_loss_percent={}), None, ports, None
            return dev, lbls, None, ssh_exc, ports, None

    workers = max(1, min(max_workers, len(probe_tasks)))
    if workers == 1:
        results = [_run_probe(item) for item in probe_tasks]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_run_probe, probe_tasks))

    samples: list[Sample] = []
    status_changed = False
    is_live_driver = toolchain.health_probe.__class__.__name__ == "ScrapliNetworkDriver"
    for item_res in results:
        device, labels, health, err, _ports = item_res[:5]
        hw_json = item_res[5] if len(item_res) > 5 else None
        if err is not None or health is None:
            if "SSH-замок" in str(err):
                prev, _ = latest_for_host(device.hostname)
                samples.extend(prev or [Sample(metrics.DEVICE_REACHABLE, 1.0, labels)])
                continue
            from netops.services.telemetry import clear_live_metrics

            logger.info("Телеметрия с %s не снята: %s", device.hostname, err)
            if device.status != DeviceStatus.UNREACHABLE:
                device.status = DeviceStatus.UNREACHABLE
                status_changed = True
            clear_live_metrics(device.hostname)
            samples.append(Sample(metrics.DEVICE_REACHABLE, 0.0, labels))
            continue

        from netops.services.vitality import clear_connection_failure

        clear_connection_failure(device.hostname)
        if device.status in (DeviceStatus.UNREACHABLE, DeviceStatus.UNKNOWN):
            from netops.models.drift import DriftRecord

            dr = session.scalars(select(DriftRecord).where(DriftRecord.device_id == device.id).order_by(DriftRecord.id.desc()).limit(1)).first()
            dr_val = getattr(getattr(dr, "status", None), "value", None)
            if dr_val in (DeviceStatus.IN_SYNC.value, DeviceStatus.DRIFT_DETECTED.value):
                device.status = DeviceStatus(dr_val)
            elif is_live_driver or _is_server_device(device):
                device.status = DeviceStatus.IN_SYNC
            elif device.status == DeviceStatus.UNREACHABLE:
                device.status = DeviceStatus.UNKNOWN
            status_changed = True
        if hw_json:
            device.hardware_specs = hw_json
            status_changed = True
        device.last_checked_at = utcnow()
        status_changed = True
        samples.append(Sample(metrics.DEVICE_REACHABLE, 1.0, labels))
        samples.extend(_emit_server_samples(health, labels, device.hostname) if _is_server_device(device) else _emit_switch_samples(health, labels, device.hostname))

    if status_changed:
        session.commit()
    return samples
