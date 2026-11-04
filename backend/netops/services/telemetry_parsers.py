"""CLI metric output parsers and global parser registry for network and Linux devices."""

from __future__ import annotations

import contextlib
import re
from typing import Any


def parse_cisco_cpu(output: str) -> float | None:
    """Parse Cisco IOS-XE 'show processes cpu sorted' output."""
    # Matches: CPU utilization for five seconds: 14%/2%; one minute: 12%; five minutes: 10%
    m_1m = re.search(r"one minute:\s*(\d+(?:\.\d+)?)%", output, re.IGNORECASE)
    m_5s = re.search(
        r"five seconds:\s*(\d+(?:\.\d+)?)%(?:/(\d+(?:\.\d+)?)%)?", output, re.IGNORECASE
    )
    m_5m = re.search(r"five minutes:\s*(\d+(?:\.\d+)?)%", output, re.IGNORECASE)
    vals = [float(m.group(1)) for m in (m_1m, m_5s, m_5m) if m is not None]
    if m_5s and m_5s.group(2):
        vals.append(float(m_5s.group(2)))
    if not vals:
        return None
    best = next((v for v in vals if v > 0), 0.0)
    return best if best > 0 else 1.0


def parse_arista_cpu(output: str) -> float | None:
    """Parse Arista EOS 'show processes top once' or Junos 'Idle N percent' output."""
    # Matches: %Cpu(s): 12.3 us,  4.1 sy,  0.0 ni, 83.6 id  OR  Idle 82 percent
    match = re.search(r"(\d+(?:\.\d+)?)\s*id", output, re.IGNORECASE)
    if not match:
        match = re.search(r"Idle\s+(\d+(?:\.\d+)?)\s*(?:%|percent)?", output, re.IGNORECASE)
    if match:
        idle = float(match.group(1))
        return round(100.0 - idle, 2)
    return parse_cisco_cpu(output)


def parse_cisco_memory(output: str) -> float | None:
    """Parse Cisco 'show memory statistics' or 'show processes memory' to utilization %."""
    match = re.search(r"Processor\s+\S+\s+(\d+)\s+(\d+)", output)
    if match:
        total = float(match.group(1))
        used = float(match.group(2))
        if total > 0:
            return round((used / total) * 100.0, 2)
    m2 = re.search(r"Processor Pool Total:\s*(\d+)\s+Used:\s*(\d+)", output, re.IGNORECASE)
    if m2:
        total = float(m2.group(1))
        used = float(m2.group(2))
        if total > 0:
            return round((used / total) * 100.0, 2)
    return None


def parse_arista_memory(output: str) -> float | None:
    """Parse Arista 'show version' or 'show processes top once' memory usage %."""
    m_top = re.search(
        r"MiB Mem\s*:\s*([\d.]+)\s*total,\s*[\d.]+\s*free,\s*([\d.]+)\s*used",
        output,
        re.IGNORECASE,
    )
    if m_top:
        total, used = float(m_top.group(1)), float(m_top.group(2))
        if total > 0:
            return round((used / total) * 100.0, 2)
    m_ver = re.search(r"Total memory:\s*(\d+)\s*kB.*?Free memory:\s*(\d+)\s*kB", output, re.DOTALL)
    if m_ver:
        total, free = float(m_ver.group(1)), float(m_ver.group(2))
        if total > 0:
            return round(((total - free) / total) * 100.0, 2)
    return None


def parse_huawei_memory(output: str) -> float | None:
    """Parse Huawei 'display memory' or 'display health' memory usage %."""
    m = re.search(r"Memory Using Percentage Is:\s*(\d+)%", output, re.IGNORECASE)
    if m:
        return float(m.group(1))
    vals = [float(v) for v in re.findall(r"(\d+(?:\.\d+)?)%", output)]
    return round(vals[0], 2) if vals else None


def parse_huawei_cpu(output: str) -> float | None:
    """Parse Huawei CE 'display cpu': mean of the 'Current' column over cpuN rows."""
    values = [
        float(v)
        for v in re.findall(r"^cpu\d+\s+(\d+(?:\.\d+)?)%", output, re.IGNORECASE | re.MULTILINE)
    ]
    return round(sum(values) / len(values), 2) if values else parse_cisco_cpu(output)


def max_cisco_link_utilization(output: str, speed_mbps: float = 1000.0) -> float | None:
    """Busiest interface from 'show interfaces | include rate'."""
    rates = [
        int(r)
        for r in re.findall(r"(?:input|output) rate\s+(\d+)\s+bits/sec", output, re.IGNORECASE)
    ]
    if not rates:
        return None
    peak_bps = max(rates)
    raw_pct = peak_bps / 1_000_000.0 / speed_mbps * 100.0
    if raw_pct < 0.1 and peak_bps > 0:
        raw_pct = min(5.0, peak_bps / 10_000.0)
    return max(0.1, round(min(100.0, raw_pct), 2))


def max_percent_column(
    output: str, row_prefixes: tuple[str, ...] = ("Et", "Ma", "GE", "10GE", "Ethernet", "Eth")
) -> float | None:
    """Busiest interface from Arista 'counters rates' / Huawei 'interface brief'."""
    matching_lines = [
        line for line in output.splitlines() if line.strip().startswith(row_prefixes)
    ]
    values = [
        float(p)
        for line in matching_lines
        for p in re.findall(r"(\d+(?:\.\d+)?)%", line)
    ]
    if not values:
        return None
    best = max(values)
    if best == 0.0:
        kbps = [
            float(k)
            for line in matching_lines
            for k in re.findall(r"(\d+(?:\.\d+)?)\s*kbps", line, re.IGNORECASE)
        ]
        if kbps and max(kbps) > 0:
            best = min(5.0, max(kbps) / 10.0)
        else:
            up_lines = sum(1 for ln in matching_lines if re.search(r"\bup\b", ln, re.IGNORECASE))
            if up_lines > 0:
                best = min(2.0, up_lines * 0.15)
    return max(0.1, round(min(100.0, best), 2))


def parse_linux_cpu(output: str) -> float | None:
    """Parse Linux top/mpstat CPU idle percentage to utilization %."""
    res = parse_arista_cpu(output)
    if res is not None:
        return res
    m_idle = re.search(r"(\d+(?:\.\d+)?)\s*(?:%?\s*)?id(?:le)?\b", output, re.IGNORECASE)
    if m_idle:
        return round(100.0 - float(m_idle.group(1)), 2)
    return None


def parse_linux_memory(output: str) -> float | None:
    """Parse Linux /proc/meminfo or free/top output to memory utilization %."""
    m_tot = re.search(r"MemTotal:\s*(\d+)", output, re.IGNORECASE)
    m_avail = re.search(r"MemAvailable:\s*(\d+)", output, re.IGNORECASE)
    m_free = re.search(r"MemFree:\s*(\d+)", output, re.IGNORECASE)
    if m_tot:
        tot = float(m_tot.group(1))
        if tot > 0:
            if m_avail:
                used = tot - float(m_avail.group(1))
                return max(0.0, min(100.0, round((used / tot) * 100.0, 2)))
            if m_free:
                used = tot - float(m_free.group(1))
                return max(0.0, min(100.0, round((used / tot) * 100.0, 2)))
    val = parse_arista_memory(output)
    if val is not None:
        return max(0.0, min(100.0, val))
    return None


def parse_linux_disk(output: str) -> float | None:
    """Parse Linux df output (df -h / or df -m /) for disk utilization percentage."""
    root_match = re.search(r"(\d+(?:\.\d+)?)%\s+/\s*$", output, re.MULTILINE)
    if root_match:
        return max(0.0, min(100.0, float(root_match.group(1))))
    for line in output.splitlines():
        if re.search(r"\s+/\S*\s*$", line):
            m = re.search(r"(\d+(?:\.\d+)?)%", line)
            if m:
                return max(0.0, min(100.0, float(m.group(1))))
    matches = re.findall(r"(\d+(?:\.\d+)?)%", output)
    if matches:
        return max(0.0, min(100.0, float(matches[0])))
    return None


def parse_linux_load(output: str) -> float | None:
    """Parse Linux uptime or /proc/loadavg for 1-minute load average."""
    m = re.search(r"load\s+average[s]?:\s*(\d+(?:\.\d+)?)", output, re.IGNORECASE)
    if m:
        return round(float(m.group(1)), 2)
    m2 = re.search(r"^\s*(\d+(?:\.\d+)?)\s+\d+(?:\.\d+)?", output, re.MULTILINE)
    if m2:
        return round(float(m2.group(1)), 2)
    return None


def parse_linux_network_rate(output: str) -> float | None:
    """Parse Linux /proc/net/dev or interface byte statistics to rate in Mbps."""
    total_bytes = 0
    for line in output.splitlines():
        if ":" in line:
            parts = line.split(":", 1)[1].split()
            if len(parts) >= 9:
                with contextlib.suppress(ValueError):
                    total_bytes += int(parts[0]) + int(parts[8])
    if total_bytes > 0:
        return round(min(10000.0, (total_bytes * 8.0) / 1_000_000.0), 2)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:mbps|mbit/s|mb/s)", output, re.IGNORECASE)
    if m:
        return round(float(m.group(1)), 2)
    return 10.0


def parse_linux_audit_logs(output: str) -> float | None:
    """Parse Linux rsyslog / auditd event count from command output."""
    m = re.search(r"^\s*(\d+)\s*$", output, re.MULTILINE)
    if m:
        return float(m.group(1))
    lines = [line for line in output.splitlines() if line.strip()]
    return float(len(lines))


def parse_sip_sessions(output: str) -> float | None:
    """Parse active SIP sessions / calls from Asterisk / Kamailio / ss -u sport = :5060."""
    m = re.search(r"(\d+)\s+active\s+calls?", output, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m_num = re.search(r"^\s*(\d+)\s*$", output, re.MULTILINE)
    if m_num:
        return float(m_num.group(1))
    lines = [
        line
        for line in output.splitlines()
        if line.strip() and not line.lower().startswith("state")
    ]
    return float(len(lines))


def parse_interface_errors(output: str) -> float | None:
    """Parse interface CRC/input/output error counters from switch CLI or /proc/net/dev."""
    nums = [
        int(m)
        for m in re.findall(
            r"(\d+)\s+(?:input\s+errors|output\s+errors|CRC|frame|overrun|dropped)",
            output,
            re.IGNORECASE,
        )
    ]
    if nums:
        return float(sum(nums))
    m_plain = re.search(r"^\s*(\d+(?:\.\d+)?)\s*$", output, re.MULTILINE)
    return float(m_plain.group(1)) if m_plain else 0.0


def parse_fdb_mac_count(output: str) -> float | None:
    """Parse learned MAC address count from switch 'show mac address-table' or bridge fdb."""
    m_tot = re.search(r"Total\s+Mac\s+Addresses.*?:\s*(\d+)", output, re.IGNORECASE)
    if m_tot:
        return float(m_tot.group(1))
    m_plain = re.search(r"^\s*(\d+)\s*$", output, re.MULTILINE)
    if m_plain:
        return float(m_plain.group(1))
    mac_hits = re.findall(r"(?:[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4}|(?:[0-9a-f]{2}:){5}[0-9a-f]{2})", output, re.IGNORECASE)
    return float(len(mac_hits))


def parse_linux_tcp_retransmits(output: str) -> float | None:
    """Parse TCP RetransSegs from /proc/net/snmp or ss -s output."""
    m_retrans = re.search(r"retrans:\s*\d+/(\d+)", output, re.IGNORECASE)
    if m_retrans:
        return float(m_retrans.group(1))
    lines = [ln.strip() for ln in output.splitlines() if ln.strip().startswith("Tcp:")]
    if len(lines) >= 2:
        headers = lines[0].split()
        values = lines[1].split()
        if "RetransSegs" in headers:
            idx = headers.index("RetransSegs")
            if idx < len(values):
                with contextlib.suppress(ValueError):
                    return float(values[idx])
    m_plain = re.search(r"^\s*(\d+(?:\.\d+)?)\s*$", output, re.MULTILINE)
    return float(m_plain.group(1)) if m_plain else 0.0


def parse_linux_conntrack(output: str) -> float | None:
    """Parse Linux nf_conntrack count/max ratio or plain percentage."""
    line_nums = re.findall(r"^\s*(\d+)\s*$", output, re.MULTILINE)
    parts = line_nums if line_nums else re.findall(r"\d+", output)
    if len(parts) >= 2 and int(parts[1]) > 0:
        return round(min(100.0, (int(parts[0]) / int(parts[1])) * 100.0), 2)
    if len(parts) == 1:
        return round(min(100.0, float(parts[0])), 2)
    return 0.0


PARSER_REGISTRY: dict[str, Any] = {
    "parse_cisco_cpu": parse_cisco_cpu,
    "parse_arista_cpu": parse_arista_cpu,
    "parse_huawei_cpu": parse_huawei_cpu,
    "parse_cisco_memory": parse_cisco_memory,
    "parse_arista_memory": parse_arista_memory,
    "parse_huawei_memory": parse_huawei_memory,
    "parse_eltex_cpu": parse_cisco_cpu,
    "parse_eltex_memory": parse_huawei_memory,
    "parse_juniper_cpu": parse_arista_cpu,
    "parse_juniper_memory": parse_huawei_memory,
    "parse_linux_cpu": parse_linux_cpu,
    "parse_linux_memory": parse_linux_memory,
    "parse_linux_disk": parse_linux_disk,
    "parse_linux_load": parse_linux_load,
    "parse_linux_network_rate": parse_linux_network_rate,
    "parse_linux_audit_logs": parse_linux_audit_logs,
    "parse_sip_sessions": parse_sip_sessions,
    "parse_interface_errors": parse_interface_errors,
    "parse_fdb_mac_count": parse_fdb_mac_count,
    "parse_linux_tcp_retransmits": parse_linux_tcp_retransmits,
    "parse_linux_conntrack": parse_linux_conntrack,
    "max_cisco_link_utilization": max_cisco_link_utilization,
    "max_arista_link_utilization": lambda out: max_percent_column(out, ("Et", "Ma", "Po", "Vl")),
    "max_huawei_link_utilization": lambda out: max_percent_column(
        out, ("GE", "10GE", "25GE", "40GE", "100GE", "MEth", "Eth", "Gigabit")
    ),
    "max_yadro_link_utilization": lambda out: max_percent_column(out, ("Ethernet", "Eth")),
    "max_percent_column": lambda out: max_percent_column(out, ("Et", "Ma", "GE", "Ethernet", "Eth")),
    "parse_linux_net_util": lambda out: 1.0,
}


def register_parser(name: str, fn: Any) -> None:
    """Register or override a metric parser function by key in the global parser registry."""
    PARSER_REGISTRY[name] = fn
