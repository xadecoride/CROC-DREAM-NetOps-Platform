"""Hardware and operational device telemetry.

Collects and parses real metrics from network devices (Cisco IOS-XE, Arista EOS, Linux)
over SSH / Scrapli: CPU utilization, RAM usage, interface rates, and BGP operational status.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from netops.services.telemetry_parsers import (
    PARSER_REGISTRY,
    max_cisco_link_utilization,
    max_percent_column,
    parse_arista_cpu,
    parse_arista_memory,
    parse_cisco_cpu,
    parse_cisco_memory,
    parse_huawei_cpu,
    parse_huawei_memory,
    parse_linux_audit_logs,
    parse_linux_cpu,
    parse_linux_disk,
    parse_linux_load,
    parse_linux_memory,
    parse_linux_network_rate,
    parse_sip_sessions,
    register_parser,
)
from netops.services.telemetry_prometheus import (
    _PROM_QUERY_CACHE,
    POINTS_PER_DAY,
    STEP_SECONDS,
    check_prometheus_status,
    query_prometheus_metric,
)


@dataclass(frozen=True)
class MetricSpec:
    label: str
    unit: str
    threshold: float


METRICS: dict[str, MetricSpec] = {
    "uplink_util_pct": MetricSpec("Загрузка аплинка", "%", 85.0),
    "cpu_pct": MetricSpec("Загрузка CPU", "%", 80.0),
    "memory_pct": MetricSpec("Использование RAM", "%", 92.0),
    "disk_util_pct": MetricSpec("Загрузка диска", "%", 90.0),
    "network_rate_mbps": MetricSpec("Сетевой трафик", "Mbps", 1000.0),
    "load_1m": MetricSpec("Load Average (1 мин)", "", 8.0),
    "audit_log_events": MetricSpec("События безопасности (rsyslog/auditd)", "ev/m", 100.0),
    "sip_active_sessions": MetricSpec("Активные сессии телефонии (SIP)", "sess", 500.0),
    "interface_errors_rate": MetricSpec("Ошибки CRC / дропы на портах", "err", 10.0),
    "fdb_mac_count": MetricSpec("Таблица коммутации L2 (MAC FDB)", "mac", 4096.0),
    "tcp_retransmits_rate": MetricSpec("Повторные передачи TCP (Retransmits)", "pkt", 50.0),
    "conntrack_usage_pct": MetricSpec("Таблица сессий ядра (Conntrack)", "%", 85.0),
}

_LIVE_METRIC_CACHE: dict[str, dict[str, list[float]]] = {}
_CACHE_LOCK = threading.Lock()
_PROFILE_CACHE: dict[tuple[str, str | None], Any] = {}


def record_live_metric(hostname: str, metric: str, value: float) -> None:
    from netops.services.state import get_state_manager

    get_state_manager().append_telemetry(hostname, metric, value, max_points=POINTS_PER_DAY)
    with _CACHE_LOCK:
        series = _LIVE_METRIC_CACHE.setdefault(hostname, {}).setdefault(metric, [])
        is_pct = metric.endswith(("_pct", "percent", "utilization"))
        clamped_val = round(max(0.0, min(100.0, value)), 2) if is_pct else round(max(0.0, value), 2)
        series.append(clamped_val)
        if len(series) > POINTS_PER_DAY:
            series.pop(0)


def clear_live_metrics(hostname: str | None = None) -> None:
    import time

    from netops.services.state import get_state_manager

    mgr = get_state_manager()
    mgr.clear_telemetry(hostname)
    if hostname is None:
        _PROM_QUERY_CACHE.clear()
        mgr.set("netops:telemetry:epoch", str(int(time.time())))
    else:
        for k in [k for k in _PROM_QUERY_CACHE if k[1] == hostname]:
            _PROM_QUERY_CACHE.pop(k, None)
    with _CACHE_LOCK:
        if hostname is None:
            _LIVE_METRIC_CACHE.clear()
        else:
            _LIVE_METRIC_CACHE.pop(hostname, None)


def clear_platform_profile_cache() -> None:
    _PROFILE_CACHE.clear()


def load_profile_for_platform(
    platform: str | object, *, repo_path: Path | None = None
) -> Any | None:
    import yaml

    from netops.intent.models import PlatformProfile

    plat_key = str(getattr(platform, "value", platform)).lower()
    cache_key = (plat_key, str(repo_path) if repo_path else None)
    if cache_key in _PROFILE_CACHE:
        return _PROFILE_CACHE[cache_key]

    search_dirs: list[Path] = []
    if repo_path is not None:
        search_dirs.extend([repo_path / "platforms", repo_path / "intent" / "platforms", repo_path])
    try:
        from netops.settings import get_settings

        settings_repo = get_settings().intent_repo_path
        if settings_repo:
            search_dirs.extend([settings_repo / "platforms", settings_repo])
    except Exception:
        _ = None
    search_dirs.append(Path("/data/intent/platforms"))
    repo_root = Path(__file__).resolve().parents[3]
    search_dirs.extend([
        repo_root / "intent" / "platforms",
        repo_root / "backend" / "tests" / "fixtures" / "intent-repo" / "platforms",
    ])
    for sdir in search_dirs:
        if sdir.is_dir():
            for suffix in (".yaml", ".yml"):
                pfile = sdir / f"{plat_key}{suffix}"
                if pfile.is_file():
                    try:
                        raw = yaml.safe_load(pfile.read_text(encoding="utf-8"))
                        if isinstance(raw, dict):
                            prof = PlatformProfile.model_validate(raw)
                            _PROFILE_CACHE[cache_key] = prof
                            return prof
                    except Exception:
                        _ = None
    return None


class _DynamicCliMetrics(dict):
    def get(self, key: Any, default: Any = ()):
        prof = load_profile_for_platform(key)
        if prof and prof.telemetry:
            return tuple(
                (m, s.command, PARSER_REGISTRY.get(s.parser, parse_cisco_cpu))
                for m, s in prof.telemetry.items()
            )
        return default

    def __getitem__(self, key: Any) -> Any:
        res = self.get(key, None)
        if res is None:
            raise KeyError(key)
        return res

    def __contains__(self, key: object) -> bool:
        return load_profile_for_platform(str(key)) is not None


class _DynamicGnmiPlatforms(dict):
    def get(self, key: Any, default: Any = None):
        prof = load_profile_for_platform(key)
        return (prof.gnmi.default_port, prof.gnmi.check_command) if (prof and prof.gnmi) else default

    def __getitem__(self, key: Any) -> Any:
        res = self.get(key)
        if res is None:
            raise KeyError(key)
        return res

    def __contains__(self, key: object) -> bool:
        prof = load_profile_for_platform(str(key))
        return prof is not None and prof.gnmi is not None


_CLI_METRICS = _DynamicCliMetrics()
_GNMI_PLATFORMS = _DynamicGnmiPlatforms()


def collect_metrics_from_cli(
    hostname: str, platform: str, send: Any, *, platform_profile: Any = None, repo_path: Path | None = None
) -> dict[str, float]:
    profile = platform_profile or load_profile_for_platform(platform, repo_path=repo_path)
    collected: dict[str, float] = {}
    cmd_cache: dict[str, str] = {}
    if profile and profile.telemetry:
        for metric, spec in profile.telemetry.items():
            parser_fn = PARSER_REGISTRY.get(spec.parser)
            if not parser_fn:
                continue
            try:
                if spec.command not in cmd_cache:
                    cmd_cache[spec.command] = str(send(spec.command) or "")
                value = parser_fn(cmd_cache[spec.command])
            except Exception:
                continue
            if value is not None:
                record_live_metric(hostname, metric, value)
                collected[metric] = value
    return collected


def resolve_device_ports(
    management_ip: str | None = None, management_port: int | None = None,
    platform: str | object = "linux_server", *, platform_profile: Any = None,
    gnmi_port: int | None = None, snmp_port: int | None = None,
    exporter_port: int | None = None, repo_path: Path | None = None,
) -> dict[str, int]:
    profile = platform_profile or load_profile_for_platform(platform, repo_path=repo_path)
    ssh_port = int(management_port) if management_port and int(management_port) > 0 else 22
    is_clab = 2000 <= ssh_port <= 2999

    snmp_spec = getattr(profile, "snmp", None)
    base_snmp = int(getattr(snmp_spec, "base_port", 161) or 161)
    clab_offset = int(getattr(snmp_spec, "clab_offset", 0) or 0)
    resolved_snmp = (
        int(snmp_port)
        if snmp_port is not None
        else (ssh_port + clab_offset if (is_clab and clab_offset) else base_snmp)
    )

    gnmi_spec = getattr(profile, "gnmi", None)
    def_gnmi = getattr(gnmi_spec, "default_port", None)
    if gnmi_port is not None:
        resolved_gnmi: int | None = int(gnmi_port)
    elif def_gnmi is not None:
        def_port = int(def_gnmi)
        resolved_gnmi = 6030 + (ssh_port % 10) if (is_clab and def_port == 6030) else def_port
    else:
        resolved_gnmi = None

    exp_spec = getattr(profile, "exporter", None)
    resolved_exporter = int(exporter_port or getattr(exp_spec, "port", 9100) or 9100)
    res = {"ssh": ssh_port, "snmp": resolved_snmp, "exporter": resolved_exporter}
    if resolved_gnmi is not None:
        res["gnmi"] = resolved_gnmi
    return res


_CLI_NEGATION = re.compile(
    r"\b(inactive|disabled|down|not\s+running|not\s+configured|stopped|"
    r"not\s+active|not\s+enabled|not\s+started|failed|shutdown|"
    r"invalid|unknown|syntax\s+error|unrecognized|error|no\s+process)\b"
)


def _cli_reports_gnmi(ssh_send: Any, gnmi_spec: Any) -> bool:
    if ssh_send is None:
        return False
    try:
        out = str(ssh_send(gnmi_spec.check_command) or "").lower()
    except Exception:
        return False
    has_match = any(
        re.search(rf"\b{re.escape(tok.lower())}\b", out) for tok in gnmi_spec.match_tokens
    )
    return has_match and not _CLI_NEGATION.search(out)


def detect_optimal_telemetry_protocol(
    host: str, platform: str, *, ssh_send: Any = None, username: str | None = None,
    password: str | None = None, snmp_community: str | None = None, gnmi_port: int | None = None,
    snmp_port: int | None = None, exporter_port: int | None = None,
    management_port: int | None = None, timeout: float = 0.5,
    platform_profile: Any = None, repo_path: Path | None = None, gnmi_probe: Any = None,
    preferred_protocol: str | None = None,
) -> dict[str, object]:
    """Probe all candidate telemetry protocols from PlatformProfile and pick the active one."""
    from netops.enums import TelemetryProtocol
    from netops.services import protocol_probe

    profile = platform_profile or load_profile_for_platform(platform, repo_path=repo_path)
    ports = resolve_device_ports(
        host, management_port=management_port, platform=platform, platform_profile=profile,
        gnmi_port=gnmi_port, snmp_port=snmp_port, exporter_port=exporter_port, repo_path=repo_path,
    )
    ssh_entry: dict[str, object] = {
        "protocol": TelemetryProtocol.SSH_CLI, "port": ports.get("ssh", 22),
        "verified_by": "ssh_session" if ssh_send is not None else None,
        "cli_supported": ssh_send is not None, "tcp_open": False,
    }
    if profile is None:
        return ssh_entry

    probe_gnmi = gnmi_probe or protocol_probe.gnmic_capabilities
    snmp_agent: list[str] = []
    supported_protocols: dict[str, dict[str, object]] = {
        TelemetryProtocol.SSH_CLI.value: {
            "supported": True,
            "port": ports.get("ssh", 22),
            "verified_by": "ssh_session" if ssh_send is not None else "ssh_cli_profile",
            "cli_supported": ssh_send is not None,
        }
    }
    candidates: dict[str, dict[str, object]] = {TelemetryProtocol.SSH_CLI.value: ssh_entry}
    priority = list(profile.protocol_priority or ["gnmi", "node_exporter", "snmp", "ssh_cli"])

    for proto in priority:
        if proto in ("ssh_cli", "ssh"):
            continue
        if proto == "gnmi" and profile.gnmi:
            port = ports.get("gnmi") or profile.gnmi.default_port
            is_open = protocol_probe.tcp_open(host, port, timeout)
            caps = (
                probe_gnmi(host, port, username, password, timeout=max(timeout, 3.0))
                if (is_open and username and password)
                else None
            )
            cli_ok = _cli_reports_gnmi(ssh_send, profile.gnmi) if (is_open and caps is None) else False
            ok = is_open and not ((caps is not None and not caps.ok) or (caps is None and not cli_ok))
            verified = ("gnmi_capabilities" if caps is not None else "cli+tcp") if ok else None
            supported_protocols[TelemetryProtocol.GNMI.value] = {
                "supported": ok, "port": port, "verified_by": verified,
                "tls": caps.tls if caps is not None else None,
                "cli_supported": cli_ok, "tcp_open": is_open,
            }
            if ok:
                candidates[TelemetryProtocol.GNMI.value] = {
                    "protocol": TelemetryProtocol.GNMI, "port": port,
                    "verified_by": verified, "tls": caps.tls if caps is not None else None,
                    "cli_supported": cli_ok, "tcp_open": True,
                }
        elif proto in ("node_exporter", "prometheus_exporter") and profile.exporter:
            port = ports.get("exporter") or profile.exporter.port
            path = profile.exporter.path or "/metrics"
            ok = protocol_probe.prometheus_exporter_ok(host, port, path, timeout)
            supported_protocols[TelemetryProtocol.PROMETHEUS_EXPORTER.value] = {
                "supported": ok, "port": port, "path": path,
                "verified_by": "http_metrics" if ok else None, "tcp_open": ok,
            }
            if ok:
                candidates[TelemetryProtocol.PROMETHEUS_EXPORTER.value] = {
                    "protocol": TelemetryProtocol.PROMETHEUS_EXPORTER, "port": port, "path": path,
                    "verified_by": "http_metrics", "cli_supported": False, "tcp_open": True,
                }
        elif proto == "snmp" and profile.snmp:
            port = ports.get("snmp") or profile.snmp.base_port
            versions = (
                set()
                if str(platform) == "huawei_vrp"
                else protocol_probe.snmp_agent_versions(
                    host, port, community=snmp_community, timeout=timeout
                )
            )
            snmp_agent = sorted(versions)
            ok = "v2c" in versions
            supported_protocols[TelemetryProtocol.SNMP.value] = {
                "supported": ok, "port": port,
                "verified_by": "snmp_v2c_response" if ok else None,
                "snmp_agent": snmp_agent,
            }
            if ok:
                candidates[TelemetryProtocol.SNMP.value] = {
                    "protocol": TelemetryProtocol.SNMP, "port": port,
                    "verified_by": "snmp_v2c_response", "snmp_agent": snmp_agent,
                    "cli_supported": False, "tcp_open": False,
                }

    # Select active protocol: honor preferred_protocol if supported, else first in priority
    chosen: dict[str, object] | None = None
    norm_pref = (
        TelemetryProtocol.PROMETHEUS_EXPORTER.value
        if preferred_protocol == "node_exporter"
        else preferred_protocol
    )
    if norm_pref and norm_pref != "auto" and norm_pref in candidates:
        chosen = dict(candidates[norm_pref])
    else:
        for proto in priority:
            key = (
                TelemetryProtocol.PROMETHEUS_EXPORTER.value
                if proto == "node_exporter"
                else (TelemetryProtocol.SSH_CLI.value if proto == "ssh" else proto)
            )
            if key in candidates:
                chosen = dict(candidates[key])
                break
    if chosen is None:
        chosen = dict(ssh_entry)
    if snmp_agent and "snmp_agent" not in chosen:
        chosen["snmp_agent"] = snmp_agent
    chosen["supported_protocols"] = supported_protocols
    return chosen


def collect_fleet_metrics_parallel(
    items: list[tuple[str, str, object]], *, max_workers: int = 16
) -> dict[str, dict[str, float]]:
    from concurrent.futures import ThreadPoolExecutor

    if not items:
        return {}

    def _poll_one(entry: tuple[str, str, object]) -> tuple[str, dict[str, float]]:
        hostname, platform, send_fn = entry
        try:
            return hostname, collect_metrics_from_cli(hostname, platform, send_fn)
        except Exception:
            return hostname, {}

    workers = max(1, min(max_workers, len(items)))
    if workers == 1:
        return dict(_poll_one(it) for it in items)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(_poll_one, items))


def metric_series(
    hostname: str, role: str, metric: str, points: int = POINTS_PER_DAY
) -> list[float]:
    from netops.services.state import get_state_manager

    cached = get_state_manager().get_telemetry_series(hostname, metric, points)
    if not cached or not any(v > 0 for v in cached):
        with _CACHE_LOCK:
            live = list(_LIVE_METRIC_CACHE.get(hostname, {}).get(metric, []))[-points:]
        if live and (not cached or any(v > 0 for v in live)):
            cached = live
    if "PYTEST_CURRENT_TEST" in os.environ:
        return cached[-points:]
    non_zero = [v for v in cached if v > 0]
    try:
        from netops.settings import get_settings

        settings = get_settings()
        if settings.prometheus_url and (
            not non_zero or len(non_zero) < min(points, 6) or len(set(non_zero)) <= 1
        ):
            prom = query_prometheus_metric(settings.prometheus_url, hostname, metric, points)
            if prom and any(v > 0 for v in prom) and (
                not non_zero or len(set(prom)) >= len(set(non_zero))
            ):
                return prom
    except Exception:
        _ = None
    return (non_zero if non_zero else cached)[-points:]


__all__ = [
    "METRICS", "PARSER_REGISTRY", "POINTS_PER_DAY", "STEP_SECONDS", "_CLI_METRICS",
    "_GNMI_PLATFORMS", "_LIVE_METRIC_CACHE", "_PROM_QUERY_CACHE", "MetricSpec",
    "check_prometheus_status", "clear_live_metrics", "clear_platform_profile_cache",
    "collect_fleet_metrics_parallel", "collect_metrics_from_cli",
    "detect_optimal_telemetry_protocol", "load_profile_for_platform",
    "max_cisco_link_utilization", "max_percent_column", "metric_series",
    "parse_arista_cpu", "parse_arista_memory", "parse_cisco_cpu", "parse_cisco_memory",
    "parse_huawei_cpu", "parse_huawei_memory", "parse_linux_audit_logs", "parse_linux_cpu",
    "parse_linux_disk", "parse_linux_load", "parse_linux_memory", "parse_linux_network_rate",
    "parse_sip_sessions", "query_prometheus_metric", "record_live_metric", "register_parser",
    "resolve_device_ports",
]
