"""Platform profile loader, banner/version signature detection, and offline probe helpers."""

from __future__ import annotations

import contextlib
import logging
import os
import socket
from pathlib import Path
from typing import Any

import yaml

from netops.constants import const
from netops.enums import DeviceRole, Platform
from netops.schemas.devices import DeviceProbeResult

logger = logging.getLogger(__name__)
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLATFORMS_DIR = _REPO_ROOT / "intent" / "platforms"


def _is_protected_repo_path(path: Path) -> bool:
    """Prevent pytest runs using default settings from wiping tracked repo intent/lab files."""
    if "PYTEST_CURRENT_TEST" not in os.environ:
        return False
    try:
        return path.resolve().is_relative_to(_REPO_ROOT.resolve())
    except Exception:
        return False


def _normalize_probe_endpoint(raw_ip: str, default_port: int) -> tuple[str, int]:
    clean_ip = raw_ip.strip().removeprefix("http://").removeprefix("https://")
    clean_port = default_port
    if ":" in clean_ip:
        parts = clean_ip.split(":", 1)
        clean_ip = parts[0]
        with contextlib.suppress(ValueError):
            clean_port = int(parts[1])
    return clean_ip, clean_port


def load_platform_profiles(intent_repo_path: Path | None = None) -> dict[str, Any]:
    """Load declarative platform rules (SSH banners, version signatures, CLI commands).

    Read from intent/platforms/*.yaml.
    """
    from netops.intent.models import PlatformProfile

    if intent_repo_path is None:
        with contextlib.suppress(Exception):
            from netops.settings import get_settings

            intent_repo_path = get_settings().intent_repo_path

    profiles_dir = (
        intent_repo_path / "platforms"
        if intent_repo_path is not None and (intent_repo_path / "platforms").is_dir()
        else (
            Path("/data/intent/platforms")
            if Path("/data/intent/platforms").is_dir()
            else _PLATFORMS_DIR
        )
    )
    profiles: dict[str, Any] = {}
    if profiles_dir.is_dir():
        for yml_file in sorted(profiles_dir.glob("*.yaml")):
            try:
                raw = yaml.safe_load(yml_file.read_text(encoding="utf-8")) or {}
                if isinstance(raw, dict):
                    profile = PlatformProfile.model_validate(raw)
                    plat_key = str(getattr(profile.platform, "value", profile.platform)).lower()
                    profiles[plat_key] = profile
            except Exception:
                continue
    return profiles


def detect_platform_from_banner(
    banner: str, profiles: dict[str, dict[str, Any]] | None = None
) -> Platform:
    """Resolve Platform enum from TCP SSH banner using declarative ssh_banners."""
    low = (banner or "").lower()
    if not low:
        return Platform.CISCO_IOSXE
    active_profiles = profiles if profiles is not None else load_platform_profiles()
    for plat_name, cfg in active_profiles.items():
        for sig in cfg.get("ssh_banners") or []:
            if str(sig).lower() in low:
                try:
                    return Platform(plat_name)
                except ValueError:
                    continue
    return Platform.CISCO_IOSXE


def detect_platform_from_version(
    ver_out: str,
    initial_platform: Platform,
    profiles: dict[str, dict[str, Any]] | None = None,
) -> Platform:
    """Resolve Platform enum from version command output using version_signatures."""
    active_profiles = profiles if profiles is not None else load_platform_profiles()
    for plat_name, cfg in active_profiles.items():
        for sig in cfg.get("version_signatures") or []:
            if str(sig).lower() in ver_out.lower():
                try:
                    return Platform(plat_name)
                except ValueError:
                    continue
    return initial_platform


def _iter_inventory_devices(settings: Any) -> list[dict[str, Any]]:
    repo = getattr(settings, "intent_repo_path", None)
    if repo is not None:
        inv_path = Path(repo) / "inventory.yaml"
        if inv_path.is_file():
            with contextlib.suppress(Exception):
                raw = yaml.safe_load(inv_path.read_text(encoding="utf-8")) or {}
                return list(raw.get("devices") or [])
    return []


def _resolve_domain(settings: Any = None) -> str:
    if settings is None:
        with contextlib.suppress(Exception):
            from netops.settings import get_settings

            settings = get_settings()
    dom = str(getattr(settings, "default_domain", "") or "").strip().lstrip(".")
    if dom:
        return dom
    for dev in _iter_inventory_devices(settings):
        h = str(dev.get("hostname") or "")
        if "." in h:
            return h.split(".", 1)[1]
    return "croc.lab"


def _resolve_fabric_id(settings: Any = None) -> str:
    if settings is None:
        with contextlib.suppress(Exception):
            from netops.settings import get_settings

            settings = get_settings()
    db_url = getattr(settings, "database_url", None)
    if isinstance(db_url, str) and db_url:
        with contextlib.suppress(Exception):
            from netops.services.state import get_state_manager

            rt = get_state_manager(settings).get_db_state(
                "runtime_settings", {}, database_url=db_url
            )
            if isinstance(rt, dict) and rt.get("active_stand_slug"):
                return str(rt["active_stand_slug"]).strip()
    fab = str(getattr(settings, "default_fabric_id", "") or "").strip()
    if fab:
        return fab
    for dev in _iter_inventory_devices(settings):
        f_id = str(dev.get("fabric_id") or "").strip()
        if f_id:
            return f_id
    return "croc-dc1-pod1"


def _simulate_offline_probe(clean_ip: str, clean_port: int) -> DeviceProbeResult:
    profiles = load_platform_profiles()
    domain = _resolve_domain()
    platform = Platform.CISCO_IOSXE
    matched = False
    for plat_name, cfg in profiles.items():
        if clean_port in (cfg.get("offline_ports") or []):
            try:
                platform = Platform(plat_name)
                matched = True
                break
            except ValueError:
                continue
    if not matched and clean_port >= int(const("discovery.offline_port_family_min")):
        for plat_name, cfg in profiles.items():
            if any(int(p) // 10 == clean_port // 10 for p in (cfg.get("offline_ports") or [])):
                try:
                    platform = Platform(plat_name)
                    break
                except ValueError:
                    continue
    plat_cfg = profiles.get(platform.value) or {}
    is_spine = platform in (Platform.CISCO_IOSXE, Platform.ARISTA_EOS) and (
        clean_port % 10 in (1, 2)
    )
    if platform == Platform.LINUX_SERVER:
        role = DeviceRole.SERVER
        hname = f"server-{max(1, clean_port % 10)}.{domain}"
    elif is_spine:
        role = DeviceRole.SPINE
        hname = f"spine-{clean_port % 10}.{domain}"
    else:
        role = DeviceRole.LEAF
        hname = f"leaf-{max(1, clean_port % 10)}.{domain}"

    default_ifaces = (
        ["eth0", "eth1"]
        if platform == Platform.LINUX_SERVER
        else ["GigabitEthernet1", "GigabitEthernet2"]
    )
    ifaces = list(plat_cfg.get("offline_interfaces") or default_ifaces)
    local_p = ifaces[-1] if ifaces else "eth1"
    peer_host = (
        f"leaf-1.{hname.split('.', 1)[1]}" if is_spine else f"spine-1.{hname.split('.', 1)[1]}"
    )
    return DeviceProbeResult(
        reachable=True,
        detected_platform=platform,
        detected_role=role,
        hostname=hname,
        os_version=f"Offline Emulation ({platform.value})",
        cpu_cores=2,
        ram_gb=4.0,
        disk_gb=8.0,
        interfaces=ifaces,
        lldp_neighbors=[{"local_port": local_p, "neighbor": peer_host, "remote_port": local_p}],
        message=f"Офлайн-режим: эмуляция узла {clean_ip}:{clean_port}",
    )


def _probe_tcp_banner(clean_ip: str, clean_port: int) -> tuple[bool, str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3.0)
    banner = ""
    try:
        sock.connect((clean_ip, clean_port))
        sock.settimeout(2.0)
        with contextlib.suppress(Exception):
            banner = sock.recv(1024).decode("utf-8", errors="ignore").strip()
        sock.close()
        return True, banner
    except Exception as exc:
        logger.info("Probe TCP connection failed for %s:%s: %s", clean_ip, clean_port, exc)
        sock.close()
        return False, ""
