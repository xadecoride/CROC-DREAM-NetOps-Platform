"""Stand node synchronization and discovery helpers."""

from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import Any

import yaml

from netops.enums import DeviceRole, DeviceStatus, Platform
from netops.models import Device

_CAT_TOKEN_RE = re.compile(r"(?:^|[-_.])cat(?:[0-9]|alyst|[-_.]|$)")


def infer_role_from_name(hname: str) -> DeviceRole:
    low = hname.lower()
    if "spine" in low:
        return DeviceRole.SPINE
    if "border" in low or "fw" in low:
        return DeviceRole.BORDER
    if "srv" in low or "host" in low or "server" in low:
        return DeviceRole.SERVER
    return DeviceRole.LEAF


def infer_platform_from_name(hname: str) -> Platform:
    low = hname.lower()
    if any(k in low for k in ("cisco", "iosxe", "ios_xe", "ios-xe", "catalyst", "csr")) or _CAT_TOKEN_RE.search(low):
        return Platform.CISCO_IOSXE
    if any(k in low for k in ("huawei", "vrp", "ce68", "ce128", "ne40")):
        return Platform.HUAWEI_VRP
    if any(k in low for k in ("arista", "eos", "veos")):
        return Platform.ARISTA_EOS
    if any(
        k in low
        for k in ("srv", "host", "server", "linux", "ubuntu", "rocky", "debian", "node")
    ):
        return Platform.LINUX_SERVER
    return Platform.ARISTA_EOS


def parse_platform_value(val: Any) -> Platform:
    if isinstance(val, Platform):
        return val
    norm = str(val).strip().lower().replace("-", "_")
    if "cisco" in norm or "iosxe" in norm:
        return Platform.CISCO_IOSXE
    if "huawei" in norm or "vrp" in norm:
        return Platform.HUAWEI_VRP
    if "arista" in norm or "eos" in norm:
        return Platform.ARISTA_EOS
    if "linux" in norm or "server" in norm or "srv" in norm:
        return Platform.LINUX_SERVER
    try:
        return Platform(norm)
    except Exception:
        return Platform.ARISTA_EOS


def _find_stand_device(
    session: Any, stand_slug: str, hname: str, mip: str, mport: int
) -> tuple[Device | None, Device | None, Device | None]:
    by_ep = (
        session.query(Device)
        .filter(
            (Device.fabric_id == stand_slug)
            & (Device.management_ip == mip)
            & (Device.management_port == mport)
        )
        .first()
    )
    by_host = session.query(Device).filter(Device.hostname == hname).first()
    return by_ep, by_host, (by_ep or by_host)


def sync_stand_devices_from_port_map(
    session: Any,
    stand: Any,
    port_map: dict[str, Any],
) -> list[dict[str, str]]:
    """Synchronize devices from a stand's port map, inferring platform or honoring overrides."""
    nodes: list[dict[str, str]] = []
    explicit_default_plat = port_map.get("platform")

    for hname, entry in sorted(port_map.items()):
        if str(hname).strip().isdigit() or str(hname).strip().lower() == "platform":
            continue

        raw_endpoint: str = ""
        entry_plat: Any = explicit_default_plat

        if isinstance(entry, dict):
            raw_endpoint = str(entry.get("endpoint") or entry.get("host") or "")
            if entry.get("platform"):
                entry_plat = entry.get("platform")
        else:
            raw_endpoint = str(entry)

        host_part, _, port_part = raw_endpoint.rpartition(":")
        mip = host_part or stand.jump_host or str(hname)
        mport = (
            int(port_part)
            if port_part.isdigit()
            else int(getattr(stand, "jump_port", None) or 22)
        )

        if entry_plat:
            plat = parse_platform_value(entry_plat)
        else:
            plat = infer_platform_from_name(str(hname))

        role = infer_role_from_name(str(hname))
        if plat == Platform.LINUX_SERVER and role == DeviceRole.LEAF:
            role = DeviceRole.SERVER

        by_ep, by_host, existing = _find_stand_device(
            session, stand.slug, str(hname), mip, mport
        )
        if existing is not None:
            if existing.fabric_id == stand.slug or by_ep is not None:
                if (by_host is None or by_host.id == existing.id) and existing.hostname.isdigit():
                    existing.hostname = str(hname)
                if by_ep is None or by_ep.id == existing.id:
                    existing.management_ip, existing.management_port = mip, mport
                existing.platform, existing.role = plat, role
                existing.fabric_id, existing.site = stand.slug, stand.site or stand.slug
                session.flush()
        else:
            session.add(
                Device(
                    hostname=str(hname),
                    management_ip=mip,
                    management_port=mport,
                    platform=plat,
                    role=role,
                    auth_profile="lab",
                    fabric_id=stand.slug,
                    site=stand.site or stand.slug,
                    status=DeviceStatus.UNKNOWN,
                )
            )
            session.flush()

        nodes.append({"hostname": str(hname), "endpoint": raw_endpoint or f"{mip}:{mport}"})

    return nodes


def sync_stand_nodes_from_sot_or_portmap(
    session: Any,
    container: Any,
    stand: Any,
    pmap: dict[str, Any],
    *,
    seed_default_pod: bool = False,
) -> list[dict[str, str]]:
    """Synchronize devices for a stand from port_map, Git SoT inventory, or clab topology.

    Newly added devices are initialized with DeviceStatus.UNKNOWN to ensure honest metrics.
    """
    nodes: list[dict[str, str]] = []

    def _ensure_device(
        hname: str,
        mip: str,
        mport: int,
        platform: Platform,
        role: DeviceRole,
        auth_profile: str = "lab",
    ) -> None:
        if hname.strip().isdigit():
            return
        by_ep, by_host, existing = _find_stand_device(
            session, stand.slug, hname, mip, mport
        )
        if existing is not None:
            if existing.fabric_id == stand.slug or by_ep is not None:
                if (by_host is None or by_host.id == existing.id) and existing.hostname.isdigit():
                    existing.hostname = hname
                if by_ep is None or by_ep.id == existing.id:
                    existing.management_ip, existing.management_port = mip, mport
                if existing.platform != Platform.LINUX_SERVER and existing.role != DeviceRole.SERVER:
                    existing.platform, existing.role = platform, role
                if not existing.auth_profile or existing.auth_profile == "lab":
                    existing.auth_profile = auth_profile
                existing.fabric_id, existing.site = stand.slug, stand.site or stand.slug
                session.flush()
            return
        session.add(
            Device(
                hostname=hname,
                management_ip=mip,
                management_port=mport,
                platform=platform,
                role=role,
                auth_profile=auth_profile,
                fabric_id=stand.slug,
                site=stand.site or stand.slug,
                status=DeviceStatus.UNKNOWN,
            )
        )
        session.flush()

    # 1. Sync nodes declared in stand's port_map using heuristic & explicit platform inference
    portmap_nodes = sync_stand_devices_from_port_map(session, stand, pmap)
    nodes.extend(portmap_nodes)

    # 2. Check stand-specific inventory file in Git SoT if present
    subpath = stand.intent_subpath or f"stands/{stand.slug}"
    stand_inv_path = Path(container.settings.intent_repo_path) / subpath / "inventory.yaml"
    if stand_inv_path.is_file():
        raw = yaml.safe_load(stand_inv_path.read_text(encoding="utf-8")) or {}
        for item in raw.get("devices") or []:
            if not isinstance(item, dict) or not item.get("hostname"):
                continue
            hname = str(item["hostname"])
            mip = str(item.get("management_ip") or stand.jump_host or hname)
            mport = int(item.get("management_port") or 22)
            _ensure_device(
                hname,
                mip,
                mport,
                Platform(item.get("platform", infer_platform_from_name(hname).value)),
                DeviceRole(item.get("role", DeviceRole.LEAF.value)),
                str(item.get("auth_profile") or "lab"),
            )
            if not any(n["hostname"] == hname for n in nodes):
                nodes.append({"hostname": hname, "endpoint": f"{mip}:{mport}"})

    # 3. Check Containerlab topology_path (.clab.yaml) if configured
    if stand.topology_path:
        tpath = Path(stand.topology_path)
        if not (tpath.is_absolute() if hasattr(tpath, "is_absolute") else False):
            tpath = Path(container.settings.intent_repo_path).parent / stand.topology_path
        if tpath.is_file():
            with contextlib.suppress(Exception):
                raw_clab = yaml.safe_load(tpath.read_text(encoding="utf-8")) or {}
                clab_nodes = (raw_clab.get("topology") or {}).get("nodes") or {}
                if isinstance(clab_nodes, dict):
                    for idx, (nname, ncfg) in enumerate(clab_nodes.items(), start=1):
                        hname = f"{nname}.croc.lab" if "." not in str(nname) else str(nname)
                        mip = str(
                            (ncfg or {}).get("mgmt-ipv4")
                            or stand.jump_host
                            or f"172.20.30.{10 + idx}"
                        )
                        _ensure_device(
                            hname, mip, 22, infer_platform_from_name(hname), infer_role_from_name(hname)
                        )
                        if not any(n["hostname"] == hname for n in nodes):
                            nodes.append({"hostname": hname, "endpoint": f"{mip}:22"})

    # 4. If a new stand has 0 devices anywhere and seed_default_pod is True, seed a 4-node Leaf-Spine pod
    # only in offline emulation mode to prevent discovery on real stands from seeding phantom switches
    existing_count = session.query(Device).filter(Device.fabric_id == stand.slug).count()
    eff_driver = (
        getattr(stand, "driver", None)
        or getattr(getattr(container, "settings", None), "network_driver", None)
        or "offline"
    )
    is_live = eff_driver in ("scrapli", "live") or (
        getattr(getattr(container, "settings", None), "network_driver", None) in ("scrapli", "live")
    )
    is_offline = not is_live and eff_driver == "offline"
    if seed_default_pod and is_offline and existing_count == 0 and not nodes:
        pod_prefix = stand.slug.lower().replace("_", "-")
        default_pod = [
            (f"{pod_prefix}-spine-1.croc.lab", "172.20.30.11", Platform.ARISTA_EOS, DeviceRole.SPINE),
            (f"{pod_prefix}-spine-2.croc.lab", "172.20.30.12", Platform.CISCO_IOSXE, DeviceRole.SPINE),
            (f"{pod_prefix}-leaf-1.croc.lab", "172.20.30.21", Platform.HUAWEI_VRP, DeviceRole.LEAF),
            (f"{pod_prefix}-leaf-2.croc.lab", "172.20.30.22", Platform.HUAWEI_VRP, DeviceRole.LEAF),
        ]
        sot_devices = []
        for hname, mip, plat, role in default_pod:
            _ensure_device(hname, mip, int(stand.jump_port or 22), plat, role)
            nodes.append({"hostname": hname, "endpoint": f"{mip}:{stand.jump_port or 22}"})
            sot_devices.append({
                "hostname": hname,
                "management_ip": mip,
                "management_port": int(stand.jump_port or 22),
                "platform": plat.value,
                "role": role.value,
                "auth_profile": "lab",
                "fabric_id": stand.slug,
                "site": stand.site or stand.slug,
                "status": "UNKNOWN",
            })
        with contextlib.suppress(OSError):
            Path(container.settings.intent_repo_path, subpath).mkdir(parents=True, exist_ok=True)
            stand_inv_path.write_text(
                yaml.safe_dump({"devices": sot_devices}, sort_keys=False),
                encoding="utf-8",
            )

    session.commit()
    return nodes
