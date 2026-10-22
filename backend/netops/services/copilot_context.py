"""Read-only multi-subsystem diagnostic context builder for AI Copilot.

Aggregates sanitized, RBAC-scoped read-only telemetry across:
1. L2/L3/BGP Topology & SPOF resilience graph
2. Recent CI/CD Jobs (Dry-Run, Deploy, Drift Scan) and DriftRecord line diffs
3. Monitoring stack health (Prometheus/VictoriaMetrics, gNMIc, SNMP, Git SoT, Freeze/Locks)
4. Live hardware & operational metrics (CPU, RAM, Uplink, Load 1m, LLDP neighbors)
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from netops.models import DriftRecord, Job, Stand
from netops.services.freeze import get_freeze_state
from netops.services.git_sot import git_repo_metadata
from netops.services.llm import sanitize_for_llm
from netops.services.state import get_state_manager
from netops.services.telemetry import check_prometheus_status, metric_series
from netops.services.topology import build_topology_graph
from netops.settings import Settings


def build_copilot_system_context(
    session: Session,
    settings: Settings,
    devices: list[Any],
    rag_devices: list[Any],
    plan_devices: dict[str, Any],
) -> dict[str, Any]:
    """Collect read-only diagnostic context across all platform subsystems for allowed devices."""
    allowed_ids = {d.id for d in devices if getattr(d, "id", None) is not None}
    allowed_hosts = {d.hostname for d in devices}

    # 1. Live metrics & hardware specs per RAG device
    device_lines: list[str] = []
    metrics_summary: dict[str, dict[str, float]] = {}
    for d in rag_devices:
        p_info = plan_devices.get(d.hostname) or {}
        active_proto = p_info.get("protocol", "ssh_cli")
        sup_map = p_info.get("supported_protocols") or {}
        sup_list = [
            k for k, v in sup_map.items() if isinstance(v, dict) and v.get("supported")
        ] or [active_proto]

        dev_metrics: dict[str, float] = {}
        for m_key in ("cpu_pct", "memory_pct", "uplink_util_pct", "load_1m"):
            series = metric_series(d.hostname, str(d.role), m_key, 4)
            if series:
                dev_metrics[m_key] = series[-1]
        if dev_metrics:
            metrics_summary[d.hostname] = dev_metrics

        m_str = (
            ", ".join(f"{k}={v:g}{'%' if k.endswith('_pct') else ''}" for k, v in dev_metrics.items())
            if dev_metrics
            else "нет свежих метрик"
        )
        lldp_count = 0
        os_ver = ""
        if getattr(d, "hardware_specs", None):
            try:
                hw = json.loads(d.hardware_specs)
                lldp_count = len(hw.get("lldp_neighbors") or [])
                os_ver = str(hw.get("os_version") or "")[:40]
            except Exception:
                _ = None
        extra_hw = f", OS={os_ver}, LLDP-соседей={lldp_count}" if (os_ver or lldp_count) else ""
        device_lines.append(
            f"- {d.hostname} (id={d.id}): {d.role}, {d.platform}, статус={d.status}, "
            f"протокол={active_proto} (доступны: {', '.join(sup_list)}), метрики=[{m_str}]{extra_hw}"
        )

    # 2. Recent Jobs & DriftRecord summary (strictly scoped to allowed devices)
    job_lines: list[str] = []
    recent_errors: list[str] = []
    try:
        recent_jobs = list(
            session.scalars(select(Job).order_by(Job.created_at.desc()).limit(6)).all()
        )
        for j in recent_jobs:
            targets = [
                t
                for t in (j.targets or [])
                if t.device_id in allowed_ids or t.hostname in allowed_hosts
            ]
            if not targets and j.targets:
                continue
            j_type = str(getattr(j.type, "value", j.type))
            j_status = str(getattr(j.status, "value", j.status))
            short_id = str(j.id)[:8]
            failed_t = [
                f"{t.hostname}: {t.error}"
                for t in targets
                if t.error and str(getattr(t.status, "value", t.status)) in ("FAILED", "ROLLED_BACK")
            ]
            err_suffix = f" | ошибки: {'; '.join(failed_t[:2])}" if failed_t else (f" | {j.error}" if j.error else "")
            job_lines.append(f"- Job #{short_id} ({j_type}): {j_status}{err_suffix}")
            if failed_t:
                recent_errors.extend(failed_t[:2])
    except Exception:
        _ = None

    drift_lines: list[str] = []
    try:
        if allowed_ids:
            drifts = list(
                session.scalars(
                    select(DriftRecord)
                    .where(DriftRecord.device_id.in_(allowed_ids))
                    .order_by(DriftRecord.checked_at.desc())
                    .limit(12)
                ).all()
            )
            seen_dev: set[int] = set()
            for dr in drifts:
                if dr.device_id in seen_dev:
                    continue
                seen_dev.add(dr.device_id)
                st = str(getattr(dr.status, "value", dr.status))
                if st == "DRIFT_DETECTED":
                    u_cnt = len(dr.unauthorized_lines or [])
                    m_cnt = len(dr.missing_lines or [])
                    sample = ", ".join((dr.unauthorized_lines or dr.missing_lines or [])[:2])
                    drift_lines.append(
                        f"- {dr.hostname}: DRIFT (+{u_cnt} лишних / -{m_cnt} недостающих строк; пример: {sample[:80]})"
                    )
    except Exception:
        _ = None

    # 3. Topology & SPOF resilience summary
    topo_summary: dict[str, Any] = {}
    topo_lines: list[str] = []
    try:
        topo = build_topology_graph(devices, session, settings)
        links = topo.get("links") or []
        down_links = [
            f"{lk.get('source_hostname')} <-> {lk.get('target_hostname')} ({lk.get('layer')})"
            for lk in links
            if lk.get("oper_status", "UP") != "UP" or lk.get("admin_state") == "SHUTDOWN"
        ]
        summary_meta = topo.get("summary") or {}
        drained = summary_meta.get("drained_nodes") or []
        topo_summary = {
            "total_links": len(links),
            "down_links": down_links,
            "drained_nodes": drained,
            "core_healthy": summary_meta.get("core_healthy", True),
        }
        topo_lines.append(
            f"Топология фабрики: узлов={len(devices)}, каналов связи={len(links)}, "
            f"отключено/аварийно={len(down_links)}, в режиме Drain={len(drained)}."
        )
        if down_links:
            topo_lines.append(f"Отключенные линки: {', '.join(down_links[:5])}")
        if drained:
            topo_lines.append(f"Узлы на обслуживании (Graceful Drain): {', '.join(drained)}")
    except Exception:
        _ = None

    # 4. Infrastructure & Governance Status (Freeze, Sync Locks, Git SoT, Prometheus/VM, Stands)
    infra_lines: list[str] = []
    freeze_info = get_freeze_state()
    is_frozen = bool(freeze_info.get("frozen", False))
    state_mgr = get_state_manager()
    locked_hosts = [
        d.hostname
        for d in devices
        if (d.id is not None and state_mgr.is_sync_locked(f"device:{d.id}"))
        or state_mgr.is_sync_locked(f"device:{d.hostname}")
    ]
    if is_frozen:
        infra_lines.append(
            f"ВНИМАНИЕ: Фабрика заморожена (Kill Switch активен, причина: {freeze_info.get('reason')})."
        )
    if locked_hosts:
        infra_lines.append(f"Активна защитная блокировка sync_lock на узлах: {', '.join(locked_hosts)}.")

    try:
        git_meta = git_repo_metadata(settings.intent_repo_path, settings.github_repo_url)
        infra_lines.append(
            f"Git SoT: ветка {git_meta.get('branch', 'main')}@{git_meta.get('commit_short', 'HEAD')} "
            f"({git_meta.get('commit_message', '')[:40]})."
        )
    except Exception:
        _ = None

    try:
        prom_st = check_prometheus_status(settings.prometheus_url)
        infra_lines.append(
            f"Мониторинг TSDB (Prometheus/VictoriaMetrics): reachable={prom_st.get('reachable')}, "
            f"активных таргетов={prom_st.get('up_targets')}/{prom_st.get('active_targets')}."
        )
    except Exception:
        _ = None

    try:
        stands = list(session.scalars(select(Stand)).all())
        if stands:
            stand_desc = ", ".join(f"{s.slug} ({s.status})" for s in stands[:5])
            infra_lines.append(f"Подключенные стенды: {stand_desc}.")
    except Exception:
        _ = None

    all_lines: list[str] = []
    all_lines.extend(device_lines)
    if topo_lines:
        all_lines.extend(topo_lines)
    if drift_lines:
        all_lines.append("Детали дрейфа конфигураций:")
        all_lines.extend(drift_lines)
    if job_lines:
        all_lines.append("Последние задачи пайплайна (CI/CD Jobs):")
        all_lines.extend(job_lines)
    if infra_lines:
        all_lines.extend(infra_lines)

    sanitized_text = sanitize_for_llm("\n".join(all_lines))
    return {
        "context_text": sanitized_text,
        "metrics_summary": metrics_summary,
        "topo_summary": topo_summary,
        "recent_errors": recent_errors,
        "drift_lines": drift_lines,
        "job_lines": job_lines,
        "is_frozen": is_frozen,
        "freeze_reason": str(freeze_info.get("reason") or ""),
        "locked_hosts": locked_hosts,
    }
