"""ACL, BGP, brownfield YAML intent extraction, and SoT inventory helpers."""

from __future__ import annotations

import contextlib
import fcntl
import ipaddress
import re
import threading
from pathlib import Path
from typing import Any

import yaml
from pydantic import SecretStr

from netops.enums import DeviceRole, Platform
from netops.models import Device
from netops.services.discovery_parsers import _parse_linux_interfaces, _parse_running_interfaces
from netops.services.discovery_profiles import _resolve_fabric_id, load_platform_profiles
from netops.settings import AuthProfile

_SOT_INV_LOCK = threading.RLock()

_PORT_MAP = {
    "bgp": 179,
    "ssh": 22,
    "telnet": 23,
    "snmp": 161,
    "www": 80,
    "https": 443,
    "domain": 53,
}


def _wildcard_to_cidr(ip_str: str, wc_str: str) -> str:
    try:
        mask_octets = [str(255 - int(o)) for o in wc_str.split(".")]
        net = ipaddress.IPv4Network(f"{ip_str}/{'.'.join(mask_octets)}", strict=False)
        return str(net)
    except Exception:
        return "any"


def _parse_acl_endpoint(tokens: list[str], idx: int) -> tuple[str, int]:
    if idx >= len(tokens):
        return "any", idx
    tok = tokens[idx].lower()
    if tok == "any":
        return "any", idx + 1
    if tok == "host" and idx + 1 < len(tokens):
        return f"{tokens[idx + 1]}/32", idx + 2
    if "/" in tokens[idx]:
        return tokens[idx], idx + 1
    if idx + 1 < len(tokens) and re.match(r"^\d+\.\d+\.\d+\.\d+$", tokens[idx + 1]):
        return _wildcard_to_cidr(tokens[idx], tokens[idx + 1]), idx + 2
    return "any", idx + 1


_RE_ACL_HEAD = re.compile(
    r"^(?:ip\s+access-list\s+(?:extended\s+)?(\S+)|acl\s+name\s+(\S+)\s+advance)",
    re.IGNORECASE,
)
_RE_ACL_SECTION_END = re.compile(
    r"^(?:interface|router|bgp|ospf|line|snmp-server)\b",
    re.IGNORECASE,
)
_RE_ACL_HW_RULE = re.compile(
    r"^rule\s+(\d+)\s+(permit|deny)\s+(ip|tcp|udp|icmp|ospf)\b(.*)$",
    re.IGNORECASE,
)
_RE_ACL_HW_SRC = re.compile(r"source\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)")
_RE_ACL_HW_DST = re.compile(r"destination\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+)")
_RE_ACL_HW_SPORT = re.compile(r"source-port\s+eq\s+(\S+)")
_RE_ACL_HW_DPORT = re.compile(r"destination-port\s+eq\s+(\S+)")
_RE_ACL_CISCO_RULE = re.compile(
    r"^(\d+)\s+(permit|deny)\s+(ip|tcp|udp|icmp|ospf)\s+(.+)$",
    re.IGNORECASE,
)


def _parse_port_token(raw: str) -> int | str:
    low = raw.lower()
    return int(low) if low.isdigit() else low


def _parse_running_acls(cfg: str) -> list[dict[str, Any]]:
    acls: list[dict[str, Any]] = []
    cur_acl: dict[str, Any] | None = None
    for raw_line in cfg.splitlines():
        line = raw_line.strip()
        m_head = _RE_ACL_HEAD.match(line)
        if m_head:
            if cur_acl and cur_acl["rules"]:
                acls.append(cur_acl)
            cur_acl = {"name": m_head.group(1) or m_head.group(2), "rules": []}
            continue
        if cur_acl is None:
            continue
        if line.startswith(("!", "#")) or _RE_ACL_SECTION_END.match(line):
            if cur_acl["rules"]:
                acls.append(cur_acl)
            cur_acl = None
            continue
        m_hw = _RE_ACL_HW_RULE.match(line)
        if m_hw:
            seq, act, proto, rest = (
                int(m_hw.group(1)),
                m_hw.group(2).lower(),
                m_hw.group(3).lower(),
                m_hw.group(4),
            )
            m_src = _RE_ACL_HW_SRC.search(rest)
            m_dst = _RE_ACL_HW_DST.search(rest)
            rule: dict[str, Any] = {
                "sequence": seq,
                "action": act,
                "protocol": proto,
                "source": _wildcard_to_cidr(m_src.group(1), m_src.group(2)) if m_src else "any",
                "destination": _wildcard_to_cidr(m_dst.group(1), m_dst.group(2)) if m_dst else "any",
            }
            m_sp = _RE_ACL_HW_SPORT.search(rest)
            if m_sp:
                rule["src_port"] = _parse_port_token(m_sp.group(1))
            m_dp = _RE_ACL_HW_DPORT.search(rest)
            if m_dp:
                rule["dst_port"] = _parse_port_token(m_dp.group(1))
            cur_acl["rules"].append(rule)
            continue

        m_cisco = _RE_ACL_CISCO_RULE.match(line)
        if m_cisco:
            seq, act, proto = (
                int(m_cisco.group(1)),
                m_cisco.group(2).lower(),
                m_cisco.group(3).lower(),
            )
            toks = m_cisco.group(4).split()
            src, idx = _parse_acl_endpoint(toks, 0)
            src_port = None
            if idx + 1 < len(toks) and toks[idx].lower() == "eq":
                src_port = _parse_port_token(toks[idx + 1])
                idx += 2
            dst, idx = _parse_acl_endpoint(toks, idx)
            dst_port = None
            if idx + 1 < len(toks) and toks[idx].lower() == "eq":
                dst_port = _parse_port_token(toks[idx + 1])
            rule = {
                "sequence": seq,
                "action": act,
                "protocol": proto,
                "source": src,
                "destination": dst,
            }
            if src_port:
                rule["src_port"] = src_port
            if dst_port:
                rule["dst_port"] = dst_port
            cur_acl["rules"].append(rule)
    if cur_acl and cur_acl["rules"]:
        acls.append(cur_acl)
    return acls


def _parse_bgp_announced_prefixes(cfg: str, router_id: str) -> list[str]:
    prefixes: list[str] = []
    in_bgp = False
    for raw_line in cfg.splitlines():
        line = raw_line.strip()
        if re.match(r"^(?:router\s+bgp|bgp)\s+\d+", line, re.IGNORECASE):
            in_bgp = True
            continue
        if in_bgp and re.match(
            r"^(?:ip\s+access-list|acl\s+name|ospf|router\s+ospf|line)\b", line, re.IGNORECASE
        ):
            in_bgp = False
        if not in_bgp:
            continue
        m_cidr = re.match(r"^network\s+(\d+\.\d+\.\d+\.\d+/\d+)", line, re.IGNORECASE)
        if m_cidr:
            if m_cidr.group(1) not in prefixes:
                prefixes.append(m_cidr.group(1))
            continue
        m_mask = re.match(
            r"^network\s+(\d+\.\d+\.\d+\.\d+)\s+(?:mask\s+)?(\d+\.\d+\.\d+\.\d+)",
            line,
            re.IGNORECASE,
        )
        if m_mask and m_mask.group(2) != "0.0.0.0":
            with contextlib.suppress(ValueError):
                net = ipaddress.IPv4Network(f"{m_mask.group(1)}/{m_mask.group(2)}", strict=False)
                cidr = str(net)
                if cidr not in prefixes:
                    prefixes.append(cidr)
    return prefixes or [f"{router_id}/32"]


def _parse_brownfield_intent_from_config(
    hostname: str, cfg: str, platform: Platform | str | None = None
) -> dict[str, Any] | None:
    plat_str = str(getattr(platform, "value", platform or "")).lower()
    if (
        plat_str == "linux_server"
        or "link/ether" in cfg
        or "link/loopback" in cfg
        or re.search(r"^\d+:\s+\S+:\s+<", cfg, re.MULTILINE)
    ):
        linux_ifaces = _parse_linux_interfaces(cfg)
        if linux_ifaces:
            return {"hostname": hostname, "interfaces": linux_ifaces, "bgp": None, "acls": []}

    parsed_ifaces = _parse_running_interfaces(cfg)
    m_asn = re.search(r"^(?:router\s+bgp|bgp)\s+(\d+)", cfg, re.IGNORECASE | re.MULTILINE)
    if not parsed_ifaces or not m_asn:
        return None

    asn = int(m_asn.group(1))
    m_rid = re.search(r"(?:bgp\s+router-id|router-id)\s+(\d+\.\d+\.\d+\.\d+)", cfg, re.IGNORECASE)
    l3_ips = [str(i["ipv4_address"]).split("/")[0] for i in parsed_ifaces if i.get("ipv4_address")]
    router_id = m_rid.group(1) if m_rid else (l3_ips[0] if l3_ips else None)
    if router_id is None:
        return None

    announced = _parse_bgp_announced_prefixes(cfg, router_id)
    peer_descriptions: dict[str, str] = {}
    for m_desc in re.finditer(
        r"(?:neighbor|peer)\s+(\d+\.\d+\.\d+\.\d+)\s+description\s+([^\r\n]+)",
        cfg,
        re.IGNORECASE,
    ):
        peer_descriptions[m_desc.group(1)] = m_desc.group(2).strip().strip('"')

    neighbors: list[dict[str, Any]] = []
    seen_peers: set[str] = set()
    for m_nb in re.finditer(
        r"(?:neighbor|peer)\s+(\d+\.\d+\.\d+\.\d+)\s+(?:remote-as|as-number)\s+(\d+)",
        cfg,
        re.IGNORECASE,
    ):
        peer_ip = m_nb.group(1)
        if peer_ip not in seen_peers:
            seen_peers.add(peer_ip)
            neighbors.append({
                "peer_ip": peer_ip,
                "remote_asn": int(m_nb.group(2)),
                "description": peer_descriptions.get(peer_ip, "fabric-peer"),
                "announced_prefixes": list(announced),
            })
    if not neighbors:
        return None

    parsed_acls = _parse_running_acls(cfg)
    known_acl_names = {a["name"] for a in parsed_acls}
    for iface in parsed_ifaces:
        if iface.get("acl_in") and iface["acl_in"] not in known_acl_names:
            iface.pop("acl_in", None)

    bgp_dict: dict[str, Any] = {"asn": asn, "router_id": router_id, "neighbors": neighbors}
    if re.search(r"(?:address-family\s+(?:l2vpn\s+)?evpn|l2vpn-family\s+evpn)", cfg, re.IGNORECASE):
        bgp_dict["evpn"] = True
    m_vni = re.search(
        r"(?:\bvni\s+(\d+)(?:\s+head-end)?|\bvxlan\s+vni\s+(\d+))", cfg, re.IGNORECASE
    )
    if m_vni:
        bgp_dict["vxlan_vni"] = int(m_vni.group(1) or m_vni.group(2))

    return {"hostname": hostname, "interfaces": parsed_ifaces, "bgp": bgp_dict, "acls": parsed_acls}


def _build_default_device_intent(device: Device) -> dict[str, Any]:
    if device.role == DeviceRole.SERVER or device.platform == Platform.LINUX_SERVER:
        plat_cfg = load_platform_profiles().get(device.platform.value) or {}
        if_name = str(plat_cfg.get("default_interface") or "eth0")
        return {
            "hostname": device.hostname,
            "interfaces": [
                {"name": "lo", "description": "Loopback interface", "enabled": True, "mode": "l3"},
                {
                    "name": if_name,
                    "description": "Primary server network interface",
                    "enabled": True,
                    "mode": "l3",
                },
            ],
            "bgp": None,
            "acls": [],
        }
    return {"hostname": device.hostname, "interfaces": [], "bgp": None, "acls": []}


def _persist_custom_auth(settings: Any, profile_name: str, username: str, password: str) -> None:
    settings.auth_profiles[profile_name] = AuthProfile(
        username=username, password=SecretStr(password)
    )
    with contextlib.suppress(Exception):
        from netops.services.state import get_state_manager

        state = get_state_manager(settings)
        db_url = getattr(settings, "database_url", None)
        loaded = state.get_db_state("custom_auth_profiles", {}, database_url=db_url)
        existing_profiles = dict(loaded) if isinstance(loaded, dict) else {}
        existing_profiles[profile_name] = {"username": username, "password": password}
        state.set_db_state("custom_auth_profiles", existing_profiles, database_url=db_url)


@contextlib.contextmanager
def _locked_inventory_file(
    intent_repo_path: Path, rel_path: str = "inventory.yaml", *, create_if_missing: bool = False
) -> Any:
    with _SOT_INV_LOCK:
        inv_path = intent_repo_path / rel_path
        if not inv_path.is_file():
            if not create_if_missing:
                yield None, None
                return
            inv_path.parent.mkdir(parents=True, exist_ok=True)
            inv_path.write_text("devices: []\n", encoding="utf-8")
        with contextlib.suppress(Exception), open(
            inv_path.with_suffix(".lock"), "a+", encoding="utf-8"
        ) as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                raw_inv = yaml.safe_load(inv_path.read_text(encoding="utf-8")) or {}
                yield inv_path, raw_inv
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def _upsert_device_in_list(
    dev_list: list[dict[str, Any]], entry: dict[str, Any], default_fab: str
) -> list[dict[str, Any]]:
    target_host = str(entry["hostname"]).lower()
    target_key = (
        str(entry.get("fabric_id") or default_fab),
        str(entry["management_ip"]),
        int(entry.get("management_port") or 22),
    )
    updated = False
    result: list[dict[str, Any]] = []
    for d in dev_list:
        if not isinstance(d, dict):
            continue
        d_host = str(d.get("hostname") or "").lower()
        d_fab = str(d.get("fabric_id") or d.get("site") or default_fab)
        d_key = (d_fab, str(d.get("management_ip") or ""), int(d.get("management_port") or 22))
        if d_host == target_host or d_key == target_key:
            if not updated:
                result.append({**d, **entry})
                updated = True
        else:
            result.append(d)
    if not updated:
        result.append(entry)
    return result


def _device_to_inv_entry(device: Device, dev_fabric: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "hostname": device.hostname,
        "management_ip": str(device.management_ip),
        "management_port": int(device.management_port),
        "platform": device.platform.value,
        "role": device.role.value,
        "auth_profile": device.auth_profile,
        "fabric_id": dev_fabric,
        "site": getattr(device, "site", None) or dev_fabric,
    }
    if getattr(device, "rack", None):
        entry["rack"] = device.rack
    if getattr(device, "unit", None) is not None:
        entry["unit"] = int(device.unit)
    return entry


def _sot_inventory_upsert(device: Device, settings: Any) -> None:
    default_fab = _resolve_fabric_id(settings)
    dev_fabric = str(getattr(device, "fabric_id", default_fab) or default_fab).strip()
    entry = _device_to_inv_entry(device, dev_fabric)
    mip, mport = entry["management_ip"], entry["management_port"]
    repo_path = Path(settings.intent_repo_path)
    has_declarative_intent = (repo_path / "devices" / f"{device.hostname}.yaml").is_file() or not (
        repo_path.parent / "lab" / "running"
    ).is_dir()
    cross_fabric_conflict = False
    with _locked_inventory_file(repo_path) as (inv_path, raw_inv):
        if inv_path is not None and raw_inv is not None:
            dev_list = list(raw_inv.get("devices") or [])
            cross_fabric_conflict = any(
                isinstance(d, dict)
                and str(d.get("fabric_id") or d.get("site") or default_fab) != dev_fabric
                and str(d.get("management_ip") or "") == mip
                and int(d.get("management_port") or 22) == mport
                for d in dev_list
            )
            if not cross_fabric_conflict and has_declarative_intent:
                raw_inv["devices"] = _upsert_device_in_list(dev_list, entry, default_fab)
                inv_path.write_text(
                    yaml.safe_dump(raw_inv, sort_keys=False, allow_unicode=True), encoding="utf-8"
                )

    stand_dir = repo_path / "stands" / dev_fabric
    if (
        (dev_fabric != default_fab and stand_dir.is_dir())
        or cross_fabric_conflict
        or not has_declarative_intent
    ):
        with _locked_inventory_file(
            repo_path, f"stands/{dev_fabric}/inventory.yaml", create_if_missing=True
        ) as (s_path, s_raw):
            if s_path is not None and s_raw is not None:
                s_raw["devices"] = _upsert_device_in_list(
                    list(s_raw.get("devices") or []), entry, dev_fabric
                )
                s_path.write_text(
                    yaml.safe_dump(s_raw, sort_keys=False, allow_unicode=True), encoding="utf-8"
                )


def _sot_inventory_remove(hostname: str, intent_repo_path: Path) -> None:
    targets = ["inventory.yaml"]
    stands_dir = intent_repo_path / "stands"
    if stands_dir.is_dir():
        targets.extend(
            f"stands/{d.name}/inventory.yaml"
            for d in sorted(stands_dir.iterdir())
            if d.is_dir() and (d / "inventory.yaml").is_file()
        )
    for rel_path in targets:
        with _locked_inventory_file(intent_repo_path, rel_path) as (inv_path, raw_inv):
            if inv_path is not None and raw_inv is not None:
                dev_list = raw_inv.get("devices") or []
                filtered = [d for d in dev_list if d.get("hostname") != hostname]
                if len(filtered) != len(dev_list):
                    raw_inv["devices"] = filtered
                    inv_path.write_text(
                        yaml.safe_dump(raw_inv, sort_keys=False, allow_unicode=True),
                        encoding="utf-8",
                    )


def sync_stand_inventory_and_devices(
    session: Any,
    container: Any,
    stand: Any,
    pmap: dict[str, str],
    *,
    seed_default_pod: bool = False,
) -> list[dict[str, str]]:
    """Synchronize stand devices and YAML inventory with (fabric_id, ip, port) deduplication."""
    from netops.services.discovery_profiles import _is_protected_repo_path
    from netops.services.stand_sync import sync_stand_nodes_from_sot_or_portmap

    subpath = stand.intent_subpath or f"stands/{stand.slug}"
    repo_path = Path(container.settings.intent_repo_path)
    stand_inv_path = repo_path / subpath / "inventory.yaml"
    nodes = sync_stand_nodes_from_sot_or_portmap(
        session, container, stand, pmap, seed_default_pod=seed_default_pod
    )
    stand_devs = (
        session.query(Device)
        .filter((Device.fabric_id == stand.slug) | (Device.site == stand.slug))
        .limit(5000)
        .all()
    )
    if stand_devs and not _is_protected_repo_path(repo_path) and (
        (repo_path / subpath).is_dir() or stand_inv_path.is_file()
    ):
        with contextlib.suppress(OSError), _locked_inventory_file(
            repo_path, f"{subpath}/inventory.yaml", create_if_missing=True
        ) as (s_path, s_raw):
            if s_path is not None and s_raw is not None:
                s_devs = list(s_raw.get("devices") or [])
                for dev in stand_devs:
                    s_devs = _upsert_device_in_list(
                        s_devs, _device_to_inv_entry(dev, stand.slug), stand.slug
                    )
                s_raw["devices"] = s_devs
                s_path.write_text(
                    yaml.safe_dump(s_raw, sort_keys=False, allow_unicode=True), encoding="utf-8"
                )
    return nodes
