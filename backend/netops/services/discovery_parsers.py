"""CLI hardware specs, Linux interfaces, LLDP topology, and running interface parsers."""

from __future__ import annotations

import ipaddress
import json
import re
from typing import Any

from netops.enums import Platform
from netops.network.scrapli_driver import _ParamikoConnAdapter
from netops.services.discovery_profiles import (
    _resolve_domain,
    detect_platform_from_version,
    load_platform_profiles,
)


def _needs_hardware_specs(hw_str: str | None) -> bool:
    if not hw_str:
        return True
    try:
        data = json.loads(hw_str)
        if not isinstance(data, dict):
            return True
        os_ver = str(data.get("os_version") or "").strip().lower()
        return (
            data.get("ram_gb") is None
            or not os_ver
            or os_ver in ("uname -a", "show version", "display version")
            or not data.get("interfaces")
        )
    except Exception:
        return True


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text or "")


def _parse_ssh_hardware_specs(
    conn: _ParamikoConnAdapter, initial_platform: Platform, fallback_ver: str
) -> tuple[Platform, str, int, float | None, float | None]:
    profiles = load_platform_profiles()
    plat_cfg = profiles.get(initial_platform.value) or {}
    cmds = plat_cfg.get("commands") or {}
    ver_cmd = str(
        cmds.get("version")
        or ("uname -a" if initial_platform == Platform.LINUX_SERVER else "show version")
    )
    ver_out = _strip_ansi(conn.send_command(ver_cmd).result)
    detected_platform = detect_platform_from_version(ver_out, initial_platform, profiles)
    plat_cfg = profiles.get(detected_platform.value) or plat_cfg
    cmds = plat_cfg.get("commands") or cmds

    if detected_platform == Platform.LINUX_SERVER:
        cpu_cores = 1
        try:
            nproc_out = _strip_ansi(conn.send_command("nproc").result)
            nproc_digits = [ln.strip() for ln in nproc_out.splitlines() if ln.strip().isdigit()]
            if nproc_digits:
                cpu_cores = int(nproc_digits[0])
            else:
                cpuinfo_out = _strip_ansi(
                    conn.send_command("grep -c ^processor /proc/cpuinfo").result
                )
                cpu_digits = [ln.strip() for ln in cpuinfo_out.splitlines() if ln.strip().isdigit()]
                if cpu_digits:
                    cpu_cores = int(cpu_digits[0])
        except Exception:
            _ = None

        ram_gb = None
        try:
            mem_out = _strip_ansi(conn.send_command("grep MemTotal /proc/meminfo").result)
            m_mem = re.search(r"MemTotal:\s*(\d+)\s*kB", mem_out, re.IGNORECASE)
            if m_mem:
                ram_gb = round(int(m_mem.group(1)) / (1024 * 1024), 2)
            else:
                top_mem = _strip_ansi(
                    conn.send_command(str(cmds.get("memory") or "top -bn1 | head -n 5")).result
                )
                m_top = re.search(r"MiB Mem\s*:\s*([\d.]+)\s*total", top_mem, re.IGNORECASE)
                if m_top:
                    ram_gb = round(float(m_top.group(1)) / 1024, 2)
        except Exception:
            _ = None

        disk_gb = None
        try:
            df_out = _strip_ansi(conn.send_command("df -m /").result)
            for line in df_out.splitlines():
                parts = line.split()
                if parts and (line.strip().endswith(" /") or parts[-1] == "/"):
                    if parts[0].isdigit():
                        disk_gb = round(int(parts[0]) / 1024, 2)
                        break
                    if len(parts) >= 2 and parts[1].isdigit():
                        disk_gb = round(int(parts[1]) / 1024, 2)
                        break
            if disk_gb is None:
                df_h_out = _strip_ansi(conn.send_command("df -h /").result)
                m_h = re.search(
                    r"\s+(\d+(?:\.\d+)?)\s*([GMKT])i?B?\s+.*(?:\s|/)$", df_h_out, re.MULTILINE
                )
                if m_h:
                    val = float(m_h.group(1))
                    unit = m_h.group(2).upper()
                    if unit == "G":
                        disk_gb = round(val, 2)
                    elif unit == "T":
                        disk_gb = round(val * 1024, 2)
                    elif unit == "M":
                        disk_gb = round(val / 1024, 2)
        except Exception:
            _ = None

        lines_clean = [
            ln.strip()
            for ln in ver_out.splitlines()
            if ln.strip()
            and not ln.strip().endswith(("#", ">", "]", "$"))
            and ln.strip().lower() != ver_cmd.strip().lower()
            and not ln.strip().lower().startswith("uname ")
        ]
        os_ver = lines_clean[0][:100] if lines_clean else fallback_ver
        return detected_platform, os_ver, cpu_cores, ram_gb, disk_gb

    ram_gb: float | None = None
    m_kb = (
        re.search(r"(\d+)\s*K(?:B| bytes) of physical memory", ver_out, re.IGNORECASE)
        or re.search(
            r"(\d+)\s*K(?:/\d+K)?\s*(?:B|bytes) of (?:physical )?memory", ver_out, re.IGNORECASE
        )
        or re.search(r"Total memory:\s*(\d+)\s*KB", ver_out, re.IGNORECASE)
    )
    if m_kb:
        ram_gb = round(int(m_kb.group(1)) / (1024 * 1024), 2)
    else:
        m_mb = re.search(r"(?:SDRAM|Memory)\s*[:Size]*\s*(\d+)\s*M(?:B|iB)", ver_out, re.IGNORECASE)
        if m_mb:
            ram_gb = round(int(m_mb.group(1)) / 1024, 2)
    if ram_gb is None and cmds.get("memory"):
        mem_out = conn.send_command(str(cmds["memory"])).result
        m_hw = re.search(r"Total(?:\s+Memory)?\s*:\s*(\d+)", mem_out, re.IGNORECASE)
        if m_hw:
            val = int(m_hw.group(1))
            ram_gb = round(val / (1024 * 1024 if val > 100_000 else 1024), 2)

    disk_gb: float | None = None
    m_flash = re.search(
        r"(\d+)\s*K(?:B| bytes) of (?:virtual hard disk|flash|bootflash)", ver_out, re.IGNORECASE
    ) or re.search(r"(\d+)\s*K(?:B| bytes) of non-volatile", ver_out, re.IGNORECASE)
    if m_flash:
        disk_gb = round(int(m_flash.group(1)) / (1024 * 1024), 2)
    else:
        dir_out = conn.send_command("dir").result
        m_bytes = re.search(r"(\d+)\s*bytes total", dir_out, re.IGNORECASE)
        m_kb_dir = re.search(r"([\d,]+)\s*KB total", dir_out, re.IGNORECASE)
        if m_bytes:
            disk_gb = round(int(m_bytes.group(1)) / (1024**3), 2)
        elif m_kb_dir:
            disk_gb = round(int(m_kb_dir.group(1).replace(",", "")) / (1024 * 1024), 2)

    m_cpu = re.search(r"(\d+)\s*(?:Virtual )?CPU", ver_out, re.IGNORECASE)
    cpu_cores = int(m_cpu.group(1)) if m_cpu else 1

    lines_clean = [
        ln.strip()
        for ln in ver_out.splitlines()
        if ln.strip()
        and not ln.strip().endswith(("#", ">", "]"))
        and "version" not in ln.strip().lower()[:15]
    ]
    os_ver = lines_clean[0][:100] if lines_clean else fallback_ver
    return detected_platform, os_ver, cpu_cores, ram_gb, disk_gb


def _parse_linux_interfaces(output: str) -> list[dict[str, Any]]:
    """Parse 'ip addr show' or 'ip -o addr show' into standard L3 interface intent dicts."""
    output = _strip_ansi(output)
    interfaces: list[dict[str, Any]] = []
    if re.search(r"^\d+:\s+\S+\s+inet\s+", output, re.MULTILINE):
        seen_names: dict[str, dict[str, Any]] = {}
        for line in output.splitlines():
            m = re.match(
                r"^\d+:\s+([A-Za-z0-9._-]+)\s+inet\s+(\d+\.\d+\.\d+\.\d+/\d+)",
                line.strip(),
            )
            if m:
                if_name, ip = m.group(1), m.group(2)
                if if_name not in seen_names:
                    seen_names[if_name] = {
                        "name": if_name,
                        "description": f"Linux network interface {if_name}",
                        "enabled": True,
                        "mode": "l3",
                        "ipv4_address": ip,
                        "mtu": 1500,
                    }
        interfaces = list(seen_names.values())
    else:
        cur_if: dict[str, Any] | None = None
        for raw_line in output.splitlines():
            line = raw_line.strip()
            m_head = re.match(
                r"^\d+:\s+([A-Za-z0-9._-]+)(?:@\S+)?:.*?<([^>]*)>(?:\s+mtu\s+(\d+))?",
                line,
            )
            if m_head:
                if cur_if is not None:
                    interfaces.append(cur_if)
                if_name = m_head.group(1)
                flags = m_head.group(2)
                mtu_val = int(m_head.group(3)) if m_head.group(3) else 1500
                valid_mtu = max(68, min(9216, mtu_val))
                is_up = "UP" in flags.split(",")
                cur_if = {
                    "name": if_name,
                    "description": f"Linux network interface {if_name}",
                    "enabled": is_up,
                    "mode": "l3",
                    "ipv4_address": None,
                    "mtu": valid_mtu,
                }
                continue

            if cur_if is not None:
                m_inet = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+/\d+)", line)
                if m_inet and not cur_if.get("ipv4_address"):
                    cur_if["ipv4_address"] = m_inet.group(1)

        if cur_if is not None:
            interfaces.append(cur_if)

    # Prevent overlapping subnets between distinct interfaces and clamp MTU
    filtered: list[dict[str, Any]] = []
    used_nets = []
    for iface in interfaces:
        entry = dict(iface)
        raw_mtu = entry.get("mtu")
        if isinstance(raw_mtu, (int, float, str)) and str(raw_mtu).isdigit():
            entry["mtu"] = max(68, min(9216, int(raw_mtu)))
        else:
            entry["mtu"] = 1500

        ip_str = entry.get("ipv4_address")
        if ip_str:
            try:
                net = ipaddress.IPv4Interface(ip_str).network
                if any(net.overlaps(un) for un in used_nets):
                    entry["ipv4_address"] = None
                else:
                    used_nets.append(net)
            except Exception:
                _ = None
        filtered.append(entry)

    return filtered


def _parse_ssh_topology(
    conn: _ParamikoConnAdapter, platform: Platform, default_hostname: str
) -> tuple[str, list[str], list[dict[str, Any]]]:
    profiles = load_platform_profiles()
    plat_cfg = profiles.get(platform.value) or {}
    cmds = plat_cfg.get("commands") or {}
    if_prefixes = tuple(
        str(p)
        for p in (
            plat_cfg.get("interface_prefixes")
            or (
                "Gigabit",
                "Ethernet",
                "Loop",
                "Management",
                "GE",
                "MEth",
                "eth",
                "lo",
                "ens",
                "enp",
            )
        )
    )
    lldp_prefixes = tuple(
        str(p)
        for p in (plat_cfg.get("lldp_port_prefixes") or ("Gi", "Et", "GE", "eth", "ens", "enp"))
    )

    hostname = default_hostname
    domain = _resolve_domain()
    run_out = _strip_ansi(
        conn.send_command(
            str(cmds.get("hostname_filter") or "show running-config | include hostname")
        ).result
    )
    if platform == Platform.LINUX_SERVER and run_out.strip():
        for raw_ln in run_out.splitlines():
            cand = raw_ln.strip()
            if (
                cand
                and not cand.endswith(("#", ">", "]", "$"))
                and re.match(r"^[A-Za-z0-9._-]+$", cand)
                and cand.lower() not in ("hostname", "sysname", "include")
            ):
                hostname = cand if "." in cand else f"{cand}.{domain}"
                break
    else:
        for hm in re.finditer(
            r"^\s*(?:hostname|sysname)\s+([A-Za-z0-9._-]+)", run_out, re.MULTILINE
        ):
            raw_h = hm.group(1).strip()
            if raw_h.lower() not in ("hostname", "sysname", "include"):
                hostname = raw_h if "." in raw_h else f"{raw_h}.{domain}"
                break
        else:
            pm = re.search(
                r"(?:^|\n)<?([A-Za-z0-9._-]+?)(?:\([^)]*\))?[>#\]]\s*$", run_out.strip()
            )
            if pm and pm.group(1).lower() not in (
                "hostname",
                "sysname",
                "switch",
                "router",
                "huawei",
            ):
                raw_h = pm.group(1).strip()
                hostname = raw_h if "." in raw_h else f"{raw_h}.{domain}"

    int_cmd = str(cmds.get("interfaces_brief") or "show ip interface brief")
    int_out = _strip_ansi(conn.send_command(int_cmd).result)
    interfaces = [
        line.split()[0].split("@")[0]
        for line in int_out.splitlines()
        if line.split() and line.split()[0].startswith(if_prefixes)
    ]
    if not interfaces and platform == Platform.LINUX_SERVER:
        try:
            ip_out = _strip_ansi(conn.send_command("ip addr show").result)
            interfaces = [it["name"] for it in _parse_linux_interfaces(ip_out)]
        except Exception:
            _ = None

    lldp_cmd = str(cmds.get("lldp_neighbors") or "show lldp neighbors")
    lldp_neighbors: list[dict[str, Any]] = []
    for line in conn.send_command(lldp_cmd).result.splitlines():
        parts = line.split()
        if len(parts) >= 3 and any(p.startswith(lldp_prefixes) for p in parts[:3]):
            if not parts[0].startswith(lldp_prefixes) and parts[1].startswith(lldp_prefixes):
                loc_p, nbr = parts[1], parts[0]
            else:
                loc_p, nbr = parts[0], parts[1]
            rem_p = parts[-2] if (len(parts) >= 4 and parts[-1].isdigit()) else parts[-1]
            lldp_neighbors.append(
                {"local_port": loc_p, "neighbor": nbr, "remote_port": rem_p}
            )
    return hostname, interfaces, lldp_neighbors


def _flush_parsed_iface(cur_if: dict[str, Any] | None, out: list[dict[str, Any]]) -> None:
    if cur_if and not cur_if.pop("_is_mgmt", False):
        if cur_if.get("ipv4_address") or cur_if.get("mode") == "l2":
            out.append(cur_if)


def _parse_running_interfaces(cfg: str) -> list[dict[str, Any]]:
    parsed_ifaces: list[dict[str, Any]] = []
    cur_if: dict[str, Any] | None = None
    has_global_ospf = bool(
        re.search(r"^(?:router\s+)?ospf\s+\d+", cfg, re.IGNORECASE | re.MULTILINE)
    )
    for raw_line in cfg.splitlines():
        line = raw_line.strip()
        m_if = re.match(r"^interface\s+(\S+)", line, re.IGNORECASE)
        if m_if:
            _flush_parsed_iface(cur_if, parsed_ifaces)
            if_name = m_if.group(1)
            if not if_name.lower().startswith(
                ("mgmt", "management", "meth", "null", "vlan", "nve")
            ):
                cur_if = {"name": if_name, "enabled": True, "mode": "l3", "mtu": 1500}
            else:
                cur_if = None
            continue
        if cur_if is not None:
            if line.startswith(("!", "#")):
                _flush_parsed_iface(cur_if, parsed_ifaces)
                cur_if = None
                continue
            if re.match(r"^shutdown$", line, re.IGNORECASE):
                cur_if["enabled"] = False
            elif re.match(r"^(?:no|undo)\s+shutdown$", line, re.IGNORECASE):
                cur_if["enabled"] = True
            if re.match(
                r"^(?:vrf\s+forwarding|ip\s+binding\s+vpn-instance)\s+", line, re.IGNORECASE
            ):
                cur_if["_is_mgmt"] = True
            m_desc = re.match(r"^description\s+(.+)$", line, re.IGNORECASE)
            if m_desc:
                desc_val = m_desc.group(1).strip()
                cur_if["description"] = desc_val
                if "management" in desc_val.lower():
                    cur_if["_is_mgmt"] = True
            m_mtu = re.match(r"^mtu\s+(\d+)", line, re.IGNORECASE)
            if m_mtu:
                cur_if["mtu"] = max(68, min(9216, int(m_mtu.group(1))))
            m_vlan = re.match(
                r"^(?:switchport\s+access\s+vlan|port\s+default\s+vlan)\s+(\d+)",
                line,
                re.IGNORECASE,
            )
            if m_vlan:
                cur_if["mode"] = "l2"
                cur_if["access_vlan"] = int(m_vlan.group(1))
            m_ospf = re.match(r"^ip\s+ospf\s+(?:\d+\s+)?area\s+(\S+)", line, re.IGNORECASE)
            if m_ospf:
                cur_if["ospf_area"] = m_ospf.group(1)
            elif re.match(r"^ospf\s+(?:network-type|enable)", line, re.IGNORECASE):
                cur_if["ospf_area"] = "0.0.0.0"
            m_acl = re.match(
                r"^(?:ip\s+access-group\s+(\S+)\s+in|traffic-filter\s+inbound\s+acl\s+name\s+(\S+))",
                line,
                re.IGNORECASE,
            )
            if m_acl:
                cur_if["acl_in"] = m_acl.group(1) or m_acl.group(2)
            m_cidr = re.match(r"^ip\s+address\s+(\d+\.\d+\.\d+\.\d+/\d+)", line, re.IGNORECASE)
            if m_cidr:
                cur_if["ipv4_address"] = m_cidr.group(1)
            else:
                m_mask = re.match(
                    r"^ip\s+address\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+\.\d+\.\d+\.\d+|\d{1,2})\b",
                    line,
                    re.IGNORECASE,
                )
                if m_mask:
                    try:
                        net = ipaddress.IPv4Network(
                            f"{m_mask.group(1)}/{m_mask.group(2)}", strict=False
                        )
                        cur_if["ipv4_address"] = f"{m_mask.group(1)}/{net.prefixlen}"
                    except ValueError:
                        _ = None
    _flush_parsed_iface(cur_if, parsed_ifaces)
    if has_global_ospf:
        for iface in parsed_ifaces:
            if (
                iface["name"].lower().startswith("loopback")
                and iface.get("ipv4_address")
                and "ospf_area" not in iface
            ):
                lo_ip = str(iface["ipv4_address"]).split("/")[0]
                if f"network {lo_ip} " in cfg:
                    iface["ospf_area"] = "0.0.0.0"
    return parsed_ifaces
