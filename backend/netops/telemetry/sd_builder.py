"""Prometheus HTTP Service Discovery and gNMIc target generation helpers.

Extracts dynamic target file creation and HTTP SD responses to maintain strict
modularity limits (<= 520 lines) and enable instant target updates on mutation.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import os
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import yaml

from netops.constants import const
from netops.enums import TelemetryProtocol
from netops.services.state import get_state_manager
from netops.services.telemetry import load_profile_for_platform, resolve_device_ports

logger = logging.getLogger(__name__)

GNMIC_TARGETS_FILE = "gnmic_targets.yaml"
SNMP_TARGETS_FILE = "prometheus_snmp_targets"
EXPORTER_TARGETS_FILE = "prometheus_exporter_targets"
PROTOCOLS_STATE_KEY = "netops:telemetry:protocols"
PROTOCOL_OVERRIDES_KEY = "netops:telemetry:overrides"

SNMP_RELAY_BASE_PORT = int(os.environ.get("SNMP_RELAY_BASE_PORT", "16000"))
GNMI_RELAY_BASE_PORT = int(os.environ.get("GNMI_RELAY_BASE_PORT", "17000"))

_SNMP_YML_COMM: str | None = None


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(content, encoding="utf-8")
    try:
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(Exception):
            tmp.unlink(missing_ok=True)
        raise


def _get_snmp_yml_community() -> str | None:
    global _SNMP_YML_COMM
    if _SNMP_YML_COMM is not None:
        return _SNMP_YML_COMM or None
    for sp in (
        Path("/data/monitoring/snmp.yml"),
        Path("/app/monitoring/snmp.yml"),
        Path("monitoring/snmp.yml"),
    ):
        if sp.is_file():
            raw_snmp = yaml.safe_load(sp.read_text(encoding="utf-8")) or {}
            comm = ((raw_snmp.get("auths") or {}).get("public_v2") or {}).get("community")
            if comm:
                _SNMP_YML_COMM = str(comm)
                return _SNMP_YML_COMM
    _SNMP_YML_COMM = ""
    return None


def _sanitize_huawei_snmp(
    item: dict[str, Any], platform: str, management_port: int = 22
) -> dict[str, Any]:
    if platform != "huawei_vrp":
        return item
    out = dict(item)
    supported = dict(out.get("supported_protocols") or {})
    snmp_spec = (
        dict(supported.get("snmp") or {})
        if isinstance(supported.get("snmp"), dict)
        else {}
    )
    snmp_spec["supported"] = False
    snmp_spec["verified_by"] = None
    supported["snmp"] = snmp_spec
    if "ssh_cli" not in supported:
        supported["ssh_cli"] = {
            "supported": True,
            "port": management_port or 22,
            "verified_by": "ssh_cli_profile",
        }
    out["supported_protocols"] = supported
    proto_val = str(getattr(out.get("protocol"), "value", out.get("protocol", "ssh_cli")))
    if proto_val == TelemetryProtocol.SNMP.value:
        out["protocol"] = TelemetryProtocol.SSH_CLI.value
        out["port"] = int(management_port or 22)
        out["verified_by"] = "ssh_cli_profile"
    return out


def _apply_protocol_override(
    item: dict[str, Any], override: str | None, platform: str, management_port: int
) -> dict[str, Any]:
    cleaned = _sanitize_huawei_snmp(
        {k: v for k, v in item.items() if k != "override_rejected"},
        platform,
        management_port,
    )
    cleaned["selected_mode"] = override or "auto"
    if not override or override == "auto":
        return cleaned

    norm = (
        TelemetryProtocol.PROMETHEUS_EXPORTER.value
        if override == "node_exporter"
        else override
    )

    # Huawei VRP does not support SNMP
    if platform == "huawei_vrp" and norm == TelemetryProtocol.SNMP.value:
        return {**cleaned, "override_rejected": "snmp_not_supported"}

    supported = (
        cleaned.get("supported_protocols")
        if isinstance(cleaned.get("supported_protocols"), dict)
        else {}
    )
    ports = resolve_device_ports(
        cleaned.get("host"), management_port=management_port, platform=platform
    )
    is_exp = norm in (
        TelemetryProtocol.PROMETHEUS_EXPORTER.value,
        "prometheus_exporter",
        "node_exporter",
    )
    spec = supported.get(norm) or (
        (supported.get("prometheus_exporter") or supported.get("node_exporter"))
        if is_exp
        else None
    )

    port_map = {
        TelemetryProtocol.GNMI.value: ports.get("gnmi", 6030),
        TelemetryProtocol.SNMP.value: ports.get("snmp", 161),
        TelemetryProtocol.PROMETHEUS_EXPORTER.value: ports.get("exporter", 9100),
        "prometheus_exporter": ports.get("exporter", 9100),
        "node_exporter": ports.get("exporter", 9100),
        TelemetryProtocol.SSH_CLI.value: ports.get("ssh", management_port or 22),
    }
    resolved_port = (
        spec.get("port") if isinstance(spec, dict) else None
    ) or port_map.get(norm, 22)

    return {
        **cleaned,
        "protocol": norm,
        "port": int(resolved_port),
        "verified_by": f"manual_override:{norm}",
        "selected_mode": norm,
        "manual_override": True,
    }


def build_target_files(plan: dict[str, dict[str, Any]]) -> dict[str, Any]:
    gnmic: dict[str, Any] = {}
    snmp: list[dict[str, Any]] = []
    exporter: list[dict[str, Any]] = []

    for hostname, item in sorted(plan.items()):
        host = item.get("host")
        if not host:
            continue
        proto = item.get("protocol")
        supported = (
            item.get("supported_protocols")
            if isinstance(item.get("supported_protocols"), dict)
            else {}
        )
        labels = {
            "hostname": hostname,
            "fabric_id": item.get("fabric_id", "default"),
            "platform": item.get("platform", "linux_server"),
        }

        gnmi_spec = (
            supported.get("gnmi") if isinstance(supported.get("gnmi"), dict) else {}
        )
        if proto == TelemetryProtocol.GNMI.value:
            gnmi_port = gnmi_spec.get("port") or item.get("port")
            if gnmi_port:
                tls = gnmi_spec.get("tls") if "tls" in gnmi_spec else item.get("tls")
                entry: dict[str, Any] = {
                    "address": f"{host}:{gnmi_port}",
                    "event-tags": labels,
                }
                if tls is True:
                    entry["skip-verify"] = True
                else:
                    entry["insecure"] = True
                gnmic[hostname] = entry

        snmp_spec = (
            supported.get("snmp") if isinstance(supported.get("snmp"), dict) else {}
        )
        if labels["platform"] != "huawei_vrp" and proto == TelemetryProtocol.SNMP.value:
            snmp_port = snmp_spec.get("port") or item.get("port")
            if snmp_port:
                profile = load_profile_for_platform(labels["platform"])
                snmp_mod = (
                    profile.snmp.hardware_module if profile else "host_resources"
                )
                snmp_labels = labels | ({"snmp_module": snmp_mod} if snmp_mod else {})
                snmp.append(
                    {"targets": [f"{host}:{snmp_port}"], "labels": snmp_labels}
                )

        exp_raw = (
            supported.get("prometheus_exporter")
            or supported.get("node_exporter")
            or supported.get(TelemetryProtocol.PROMETHEUS_EXPORTER.value)
        )
        exp_spec = exp_raw if isinstance(exp_raw, dict) else {}
        if proto in (
            TelemetryProtocol.PROMETHEUS_EXPORTER.value,
            "prometheus_exporter",
            "node_exporter",
        ):
            exp_port = exp_spec.get("port") or item.get("port") or 9100
            path = exp_spec.get("path") or item.get("path") or "/metrics"
            exporter_labels = (
                labels | ({"__metrics_path__": path} if path != "/metrics" else {})
            )
            exporter.append(
                {"targets": [f"{host}:{exp_port}"], "labels": exporter_labels}
            )

    return {
        GNMIC_TARGETS_FILE: gnmic,
        SNMP_TARGETS_FILE: snmp,
        EXPORTER_TARGETS_FILE: exporter,
    }


def sync_grafana_dashboard_to_db(
    grafana_url: str | None = None,
) -> dict[str, Any] | None:
    rel = "grafana/dashboards/croc-dream-noc.yaml"
    candidates = (
        Path(f"/data/monitoring/{rel}"),
        Path(f"/app/monitoring/{rel}"),
        Path(__file__).resolve().parents[3] / f"monitoring/{rel}",
    )
    yaml_path = next((p for p in candidates if p.is_file()), None)
    sm = get_state_manager()
    dash: dict[str, Any] | None = None
    if yaml_path is not None:
        loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            dash = loaded
            sm.set_db_state("grafana_noc_dashboard", dash)
    if dash is None:
        raw = sm.get_db_state("grafana_noc_dashboard")
        dash = raw if isinstance(raw, dict) else None
    if not dash:
        return None

    target_url = (
        grafana_url
        or os.environ.get("GRAFANA_URL")
        or const("services.grafana_url")
    ).rstrip("/")
    user = os.environ.get("GRAFANA_ADMIN_USER") or const("services.grafana_admin_user")
    password = os.environ.get("GRAFANA_ADMIN_PASSWORD") or const(
        "services.grafana_admin_password"
    )
    hdrs = {
        "Content-Type": "application/json",
        "Authorization": "Basic "
        + base64.b64encode(f"{user}:{password}".encode()).decode("ascii"),
    }
    with contextlib.suppress(Exception):
        for path_s, method, body in (
            ("/api/dashboards/db", "POST", {"dashboard": dash, "overwrite": True}),
            (
                "/api/org/preferences",
                "PUT",
                {"homeDashboardUID": dash.get("uid", "croc-dream-noc")},
            ),
        ):
            req = urllib.request.Request(
                f"{target_url}{path_s}",
                data=json.dumps(body).encode("utf-8"),
                headers=hdrs,
                method=method,
            )
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                resp.read()
    return dash


def invalidate_sd_targets_cache() -> None:
    sm = get_state_manager()
    sm.set_json(f"netops:db_state:{SNMP_TARGETS_FILE}", None)
    sm.set_json(f"netops:db_state:{EXPORTER_TARGETS_FILE}", None)
    sm.set_db_state("sd_targets_dirty", True)


def _get_sd_targets(
    state_key: str, session: Any = None
) -> list[dict[str, Any]]:
    sm = get_state_manager()
    dirty = sm.get_db_state("sd_targets_dirty")

    if not dirty:
        raw = sm.get_db_state(state_key, [])
        if isinstance(raw, list) and raw:
            return raw

    from sqlalchemy import select

    from netops.models import Device
    from netops.settings import get_settings

    settings = get_settings()
    devices: list[Device] = []

    if session is not None:
        devices = list(session.scalars(select(Device).order_by(Device.hostname)).all())
    else:
        engine = sm._get_db_engine(settings.database_url)
        if engine is not None:
            try:
                from sqlalchemy.orm import Session as SASession

                with SASession(engine) as sess:
                    devices = list(
                        sess.scalars(select(Device).order_by(Device.hostname)).all()
                    )
            except Exception as exc:
                logger.debug("Failed querying devices for SD targets: %s", exc)

    cached_plan = sm.get_json(PROTOCOLS_STATE_KEY) or sm.get_db_state(
        PROTOCOLS_STATE_KEY, {}
    )
    plan_devs: dict[str, dict[str, Any]] = (
        dict(cached_plan.get("devices") or {})
        if isinstance(cached_plan, dict)
        else {}
    )

    active_hostnames = {d.hostname for d in devices}
    plan_devs = {h: v for h, v in plan_devs.items() if h in active_hostnames}

    if devices:
        raw_overrides = sm.get_json(PROTOCOL_OVERRIDES_KEY) or sm.get_db_state(
            PROTOCOL_OVERRIDES_KEY, {}
        )
        overrides = (
            {str(k): str(v) for k, v in raw_overrides.items()}
            if isinstance(raw_overrides, dict)
            else {}
        )

        for d in devices:
            if d.hostname not in plan_devs:
                plat = (
                    d.platform.value
                    if hasattr(d.platform, "value")
                    else str(d.platform)
                )
                override = overrides.get(d.hostname)
                proto = TelemetryProtocol.SSH_CLI.value
                port = d.management_port or 22

                base_item = {
                    "protocol": proto,
                    "port": port,
                    "host": str(d.management_ip),
                    "platform": plat,
                    "fabric_id": d.fabric_id or "default",
                }
                plan_devs[d.hostname] = _apply_protocol_override(
                    base_item, override, plat, d.management_port
                )

    built = build_target_files(plan_devs)
    sm.set_db_state(SNMP_TARGETS_FILE, built[SNMP_TARGETS_FILE])
    sm.set_db_state(EXPORTER_TARGETS_FILE, built[EXPORTER_TARGETS_FILE])
    sm.set_db_state("sd_targets_dirty", False)

    with contextlib.suppress(Exception):
        _atomic_write(
            settings.offline_lab_path / GNMIC_TARGETS_FILE,
            yaml.safe_dump(built[GNMIC_TARGETS_FILE], sort_keys=True, allow_unicode=True),
        )

    res = built.get(state_key, [])
    return res if isinstance(res, list) else []


def get_snmp_sd_targets(session: Any = None) -> list[dict[str, Any]]:
    return _get_sd_targets(SNMP_TARGETS_FILE, session=session)


def get_exporter_sd_targets(session: Any = None) -> list[dict[str, Any]]:
    return _get_sd_targets(EXPORTER_TARGETS_FILE, session=session)
