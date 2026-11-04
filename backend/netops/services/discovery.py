"""Device SSH probing, Brownfield YAML intent extraction, and SoT binding service.

Extracted from `api/routes/devices.py` so the HTTP controller stays thin and declarative.
"""
# ruff: noqa: F401

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException, status

from netops.constants import default_username, fallback_password, unset_password_marker
from netops.enums import DeviceRole, Platform
from netops.intent.models import DeviceIntent, InventoryDevice
from netops.models import Device
from netops.network.base import Credentials, DeviceTarget
from netops.network.scrapli_driver import _ParamikoConnAdapter
from netops.schemas.devices import (
    DeviceCreate,
    DeviceEmergencyAction,
    DeviceProbeRequest,
    DeviceProbeResult,
)
from netops.services.discovery_brownfield import (
    _SOT_INV_LOCK,
    _build_default_device_intent,
    _parse_acl_endpoint,
    _parse_bgp_announced_prefixes,
    _parse_brownfield_intent_from_config,
    _parse_running_acls,
    _persist_custom_auth,
    _sot_inventory_remove,
    _sot_inventory_upsert,
    _wildcard_to_cidr,
    sync_stand_inventory_and_devices,
)
from netops.services.discovery_parsers import (
    _flush_parsed_iface,
    _parse_linux_interfaces,
    _parse_running_interfaces,
    _parse_ssh_hardware_specs,
    _parse_ssh_topology,
)
from netops.services.discovery_profiles import (
    _PLATFORMS_DIR,
    _REPO_ROOT,
    _is_protected_repo_path,
    _normalize_probe_endpoint,
    _probe_tcp_banner,
    _resolve_domain,
    _resolve_fabric_id,
    _simulate_offline_probe,
    detect_platform_from_banner,
    detect_platform_from_version,
    load_platform_profiles,
)
from netops.toolchain import build_toolchain

logger = logging.getLogger(__name__)


def _infer_probe_role(
    platform: Platform, hostname: str, default_role: str = "leaf"
) -> DeviceRole:
    if platform == Platform.LINUX_SERVER:
        return DeviceRole.SERVER
    if "spine" in hostname.lower() or default_role == "spine":
        return DeviceRole.SPINE
    return DeviceRole.LEAF


def run_device_probe(req: DeviceProbeRequest, container: Any) -> DeviceProbeResult:
    clean_ip, clean_port = _normalize_probe_endpoint(req.management_ip, req.management_port)
    if container.settings.network_driver == "offline":
        return _simulate_offline_probe(clean_ip, clean_port)

    reachable, banner = _probe_tcp_banner(clean_ip, clean_port)
    if not reachable and not req.proxy_jump:
        return DeviceProbeResult(
            reachable=False,
            detected_platform=Platform.CISCO_IOSXE,
            detected_role=DeviceRole.LEAF,
            hostname=f"node-{clean_port}",
            os_version="Unreachable",
            interfaces=[],
            lldp_neighbors=[],
            message=f"Узел {clean_ip}:{clean_port} недоступен по сети.",
        )

    profiles = load_platform_profiles(container.settings.intent_repo_path)
    detected_platform = detect_platform_from_banner(banner, profiles)
    plat_cfg = profiles.get(detected_platform.value) or {}
    default_pw = str(plat_cfg.get("default_password") or fallback_password())
    req_pw = req.password if (req.password and req.password != unset_password_marker()) else default_pw
    target = DeviceTarget(
        hostname=f"probe-{clean_port}",
        platform=detected_platform,
        host=clean_ip,
        port=clean_port,
        credentials=Credentials(req.username or default_username(), req_pw),
    )
    hostname, os_ver = f"node-{clean_port}", (banner or detected_platform.value)
    try:
        from netops.services.telemetry import collect_metrics_from_cli

        with _ParamikoConnAdapter(target, timeout=12, proxy_jump=req.proxy_jump) as conn:
            detected_platform, os_ver, cpu_cores, ram_gb, disk_gb = _parse_ssh_hardware_specs(
                conn, detected_platform, os_ver
            )
            hostname, interfaces, lldp_neighbors = _parse_ssh_topology(conn, detected_platform, hostname)
            collect_metrics_from_cli(hostname, detected_platform.value, lambda c: conn.send_command(c).result)
    except Exception as exc:
        return DeviceProbeResult(
            reachable=False,
            detected_platform=detected_platform,
            detected_role=_infer_probe_role(detected_platform, hostname),
            hostname=hostname,
            os_version=os_ver,
            interfaces=[],
            lldp_neighbors=[],
            message=f"TCP-порт открыт ({banner or 'passthrough'}), но SSH-сессия не установлена: {exc}",
        )

    return DeviceProbeResult(
        reachable=True,
        detected_platform=detected_platform,
        detected_role=_infer_probe_role(detected_platform, hostname),
        hostname=hostname,
        os_version=os_ver,
        cpu_cores=cpu_cores,
        ram_gb=ram_gb,
        disk_gb=disk_gb,
        interfaces=interfaces,
        lldp_neighbors=lldp_neighbors,
        message=f"Успешно: подключено по SSH к {hostname} ({detected_platform.value})",
    )


def discover_live_switch_via_ssh(
    host: str,
    port: int,
    username: str,
    password: str,
    settings: Any,
    default_hostname: str = "",
    default_role: str = "",
    candidate_passwords: list[str] | None = None,
    passwords_by_platform: dict[str, str] | None = None,
    initial_platform: Platform | None = None,
    fabric_id: str | None = None,
) -> dict[str, Any] | None:
    """Probe TCP banner, connect via SSH, extract live identity, running-config, and intent."""
    profiles = load_platform_profiles(getattr(settings, "intent_repo_path", None))
    if initial_platform is not None:
        reachable, banner, detected_platform = True, f"SSH-2.0-{initial_platform.value}", initial_platform
    else:
        reachable, banner = _probe_tcp_banner(host, port)
        if not reachable or not banner:
            return None
        detected_platform = detect_platform_from_banner(banner, profiles)

    plat_cfg = profiles.get(detected_platform.value) or {}
    default_pw = str(plat_cfg.get("default_password") or fallback_password())
    plat_pw = (passwords_by_platform or {}).get(detected_platform.value, "")
    non_admin_cands = [p for p in (candidate_passwords or []) if p and p != unset_password_marker()]
    pw_candidates: list[str] = []
    for pw in (
        *([plat_pw] if plat_pw and plat_pw != unset_password_marker() else []),
        *([password] if password and password != unset_password_marker() else []),
        *non_admin_cands,
        plat_pw,
        password,
        *(candidate_passwords or []),
        default_pw,
        fallback_password(),
    ):
        if pw and pw not in pw_candidates:
            pw_candidates.append(pw)

    fallback_host = default_hostname or f"node-{port}.{_resolve_domain(settings)}"
    os_ver, running_config = (banner or detected_platform.value), ""
    used_pw = pw_candidates[0] if pw_candidates else fallback_password()
    connected = False
    cpu_cores: int | None = None
    ram_gb: float | None = None
    disk_gb: float | None = None
    interfaces: list[str] = []
    lldp_neighbors: list[dict[str, Any]] = []
    hostname = fallback_host

    from netops.services.telemetry import collect_metrics_from_cli

    for pw in pw_candidates:
        target = DeviceTarget(
            hostname=fallback_host,
            platform=detected_platform,
            host=host,
            port=port,
            credentials=Credentials(username or default_username(), pw),
        )
        try:
            with _ParamikoConnAdapter(target, timeout=8) as conn:
                detected_platform, os_ver, cpu_cores, ram_gb, disk_gb = _parse_ssh_hardware_specs(
                    conn, detected_platform, os_ver
                )
                hostname, interfaces, lldp_neighbors = _parse_ssh_topology(
                    conn, detected_platform, fallback_host
                )
                plat_cfg = profiles.get(detected_platform.value) or plat_cfg
                run_cmd = str((plat_cfg.get("commands") or {}).get("running_config") or "show running-config")
                running_config = conn.send_command(run_cmd).result
                collect_metrics_from_cli(hostname, detected_platform.value, lambda c: conn.send_command(c).result)
                used_pw = getattr(conn, "authenticated_password", None) or pw
                connected = True
                break
        except Exception:
            continue

    if not connected:
        return None

    role = _infer_probe_role(detected_platform, hostname, default_role).value
    auth_profile = f"{hostname.split('.')[0].replace('-', '')}_auth"
    _persist_custom_auth(settings, auth_profile, username or default_username(), used_pw)
    fab_slug = (fabric_id or _resolve_fabric_id(settings)).strip()

    if (
        settings.network_driver == "offline"
        and not _is_protected_repo_path(settings.offline_lab_path)
        and running_config.strip()
    ):
        cfg_file = settings.offline_lab_path / f"{hostname}.cfg"
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(running_config, encoding="utf-8")

    if not _is_protected_repo_path(settings.intent_repo_path):
        inv_path = settings.intent_repo_path / "inventory.yaml"
        in_root_inv = True
        if inv_path.is_file():
            raw_inv = yaml.safe_load(inv_path.read_text(encoding="utf-8")) or {}
            inv_hosts = {
                str(d.get("hostname"))
                for d in (raw_inv.get("devices") or [])
                if isinstance(d, dict) and d.get("hostname")
            }
            in_root_inv = hostname in inv_hosts
        live_stand = settings.network_driver != "offline"
        use_stand_dir = live_stand or not in_root_inv
        devices_dir = (
            settings.intent_repo_path / "stands" / fab_slug / "devices"
            if use_stand_dir
            else settings.intent_repo_path / "devices"
        )
        devices_dir.mkdir(parents=True, exist_ok=True)
        intent_file = devices_dir / f"{hostname}.yaml"
        if not intent_file.is_file() or use_stand_dir:
            intent_data = _parse_brownfield_intent_from_config(hostname, running_config, detected_platform)
            if intent_data is not None:
                try:
                    DeviceIntent.model_validate(intent_data)
                except Exception:
                    intent_data = None
            if intent_data is None and not intent_file.is_file():
                dummy_dev = Device(
                    id=port % 100,
                    hostname=hostname,
                    management_ip=host,
                    management_port=port,
                    platform=detected_platform,
                    role=DeviceRole(role),
                    auth_profile=auth_profile,
                    fabric_id=fab_slug,
                )
                intent_data = _build_default_device_intent(dummy_dev)
            if intent_data is not None:
                intent_file.write_text(
                    yaml.safe_dump(intent_data, sort_keys=False, allow_unicode=True), encoding="utf-8"
                )

    return {
        "hostname": hostname,
        "platform": detected_platform.value,
        "role": role,
        "management_ip": host,
        "management_port": port,
        "endpoint": f"{host}:{port}",
        "auth_profile": auth_profile,
        "fabric_id": fab_slug,
        "reachable": True,
        "banner": banner,
        "running_config": running_config,
        "hardware_specs": {
            "os_version": os_ver,
            "cpu_cores": cpu_cores,
            "ram_gb": ram_gb,
            "disk_gb": disk_gb,
            "interfaces": interfaces,
            "lldp_neighbors": lldp_neighbors,
        },
    }


def extract_brownfield_intent(device: Device, data: DeviceCreate, settings: Any) -> dict[str, Any] | None:
    """Try to pull live running-config via SSH and extract real interfaces & BGP intent."""
    plat_cfg = load_platform_profiles(settings.intent_repo_path).get(device.platform.value) or {}
    cmds = plat_cfg.get("commands") or {}
    profile = settings.auth_profiles.get(device.auth_profile)
    user = data.username or (profile.username if profile else default_username())
    default_pw = str(plat_cfg.get("default_password") or fallback_password())
    pw = (
        data.password
        if (data.password and data.password != unset_password_marker())
        else (profile.password.get_secret_value() if profile else default_pw)
    )
    target = DeviceTarget(
        hostname=device.hostname,
        platform=device.platform,
        host=str(device.management_ip),
        port=int(device.management_port),
        credentials=Credentials(user, pw),
        proxy_jump=data.proxy_jump,
    )
    try:
        with _ParamikoConnAdapter(target, timeout=10, proxy_jump=data.proxy_jump) as conn:
            default_cmd = "ip addr show" if device.platform == Platform.LINUX_SERVER else "show running-config"
            cfg = conn.send_command(str(cmds.get("running_config") or default_cmd)).result
    except Exception:
        return None
    return _parse_brownfield_intent_from_config(device.hostname, cfg, device.platform)


def bind_device_to_sot(data: DeviceCreate, device: Device, container: Any) -> None:
    """Persist custom SSH credentials and generate baseline Git SoT intent if missing."""
    settings = container.settings
    plat_cfg = load_platform_profiles(settings.intent_repo_path).get(data.platform.value) or {}
    default_pw = str(plat_cfg.get("default_password") or fallback_password())
    eff_pw = data.password if (data.password and data.password != unset_password_marker()) else default_pw
    if data.username or default_pw != unset_password_marker():
        _persist_custom_auth(settings, data.auth_profile, data.username or default_username(), eff_pw)
    if _is_protected_repo_path(settings.intent_repo_path):
        return

    default_fab = _resolve_fabric_id(settings)
    dev_fabric = str(getattr(device, "fabric_id", default_fab) or default_fab).strip()
    stand_dir = settings.intent_repo_path / "stands" / dev_fabric
    devices_dir = (
        stand_dir / "devices"
        if dev_fabric != default_fab and stand_dir.is_dir()
        else settings.intent_repo_path / "devices"
    )
    devices_dir.mkdir(parents=True, exist_ok=True)
    intent_file = devices_dir / f"{device.hostname}.yaml"
    if not intent_file.is_file():
        baseline_intent = (
            extract_brownfield_intent(device, data, settings)
            if str(getattr(settings, "network_driver", "offline")) == "scrapli"
            else None
        )
        if baseline_intent is not None:
            try:
                DeviceIntent.model_validate(baseline_intent)
                existing_rids = {
                    str(((yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("bgp") or {}).get("router_id"))
                    for f in devices_dir.glob("*.yaml")
                    if f.name != intent_file.name
                } - {"None", ""}
                rid_val = ((baseline_intent.get("bgp") or {}) if baseline_intent else {}).get("router_id")
                if rid_val and str(rid_val) in existing_rids:
                    baseline_intent = None
            except Exception:
                baseline_intent = None
        if baseline_intent is None:
            baseline_intent = _build_default_device_intent(device)
            DeviceIntent.model_validate(baseline_intent)
        intent_file.write_text(
            yaml.safe_dump(baseline_intent, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
    _sot_inventory_upsert(device, settings)


def remove_device_from_sot(
    hostname: str, intent_repo_path: Path, offline_lab_path: Path | None = None
) -> None:
    from netops.services.telemetry import clear_live_metrics

    clear_live_metrics(hostname)
    if _is_protected_repo_path(intent_repo_path):
        return
    stand_device_files = (
        list((intent_repo_path / "stands").glob(f"*/devices/{hostname}.yaml"))
        if (intent_repo_path / "stands").is_dir()
        else []
    )
    for extra_file in (
        intent_repo_path / "devices" / f"{hostname}.yaml",
        *stand_device_files,
        *(
            [offline_lab_path / f"{hostname}.cfg"]
            if offline_lab_path is not None and not _is_protected_repo_path(offline_lab_path)
            else []
        ),
    ):
        if extra_file.is_file():
            extra_file.unlink(missing_ok=True)
    _sot_inventory_remove(hostname, intent_repo_path)


def _emergency_cli_command(platform: Platform, action: str) -> str:
    plat_cfg = load_platform_profiles().get(platform.value) or {}
    em_cmds = plat_cfg.get("emergency_commands") or {}
    return str(em_cmds.get(action) or em_cmds.get("default") or "show version")


def execute_device_emergency_action(
    device: Device, data: DeviceEmergencyAction, container: Any, actor_username: str
) -> dict[str, Any]:
    from netops.api.routes.emergency import record_audit
    from netops.services.ai_guard import AiTelemetryGuard

    AiTelemetryGuard.inject_anomaly(device.hostname, "clear")
    cmd = _emergency_cli_command(device.platform, data.action)
    if container.settings.network_driver == "offline":
        toolchain = build_toolchain(container.settings)
        if data.action == "rollback_last_commit" and not _is_protected_repo_path(
            container.settings.offline_lab_path
        ):
            try:
                intent = toolchain.intents.load().intent_for(device.hostname)
                if intent is not None:
                    rendered = toolchain.renderer.render(InventoryDevice.model_validate(device), intent)
                    cfg_file = container.settings.offline_lab_path / f"{device.hostname}.cfg"
                    cfg_file.parent.mkdir(parents=True, exist_ok=True)
                    cfg_file.write_text(rendered, encoding="utf-8")
                    output = f"Restored {device.hostname}.cfg to Git SoT baseline ({len(rendered.splitlines())} lines)"
                else:
                    output = f"Offline rollback skipped: no Git intent found for {device.hostname}"
            except Exception as exc:
                output = f"Offline rollback completed ({exc})"
        else:
            output = f"[Offline Lab {device.platform.value}] Executed '{cmd}' on {device.hostname}"
    else:
        toolchain = build_toolchain(container.settings)
        target = toolchain.target_for(InventoryDevice.model_validate(device))
        try:
            with _ParamikoConnAdapter(target, timeout=20) as conn:
                output = conn.send_command(cmd).result
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"SSH ошибка при выполнении '{data.action}' на {device.hostname}: {exc}",
            ) from exc

    entry = record_audit(
        user=actor_username,
        action=data.action,
        device=device.hostname,
        payload=cmd,
        reason=data.reason,
        success=True,
        output=output,
        settings=container.settings,
    )
    return {
        "status": "SUCCESS",
        "action": data.action,
        "device": device.hostname,
        "message": f"Action '{data.action}' executed on {device.hostname}",
        "entry": entry,
    }
