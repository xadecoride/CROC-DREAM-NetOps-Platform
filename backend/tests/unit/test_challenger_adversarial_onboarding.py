"""Adversarial stress-testing suite for Linux server onboarding and POSIX parsers.

Authored by Challenger 1 (Milestone 3 / R3).
Evaluates edge cases, malformed outputs, MTU boundaries, degraded POSIX specs,
protocol cascade false positives, and multi-server concurrent onboarding.
"""

from __future__ import annotations

import concurrent.futures
import shutil
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

from netops.enums import DeviceRole, Platform, TelemetryProtocol
from netops.intent.models import DeviceIntent
from netops.models import Device
from netops.schemas.devices import DeviceCreate
from netops.services.discovery import (
    _parse_linux_interfaces,
    _parse_ssh_hardware_specs,
    bind_device_to_sot,
)
from netops.services.telemetry import (
    detect_optimal_telemetry_protocol,
    parse_linux_cpu,
    parse_linux_memory,
)


@dataclass
class FakeCmdResult:
    result: str


class MockSSHAdapter:
    def __init__(self, responses: dict[str, str] | None = None, default: str = "") -> None:
        self.responses = dict(responses or {})
        self.default = default
        self.executed_commands: list[str] = []

    def __enter__(self) -> MockSSHAdapter:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def send_command(self, cmd: str) -> FakeCmdResult:
        self.executed_commands.append(cmd)
        return FakeCmdResult(self.responses.get(cmd, self.default))


# ==============================================================================
# Suite 1: Malformed and Edge-Case Linux Interface Parsing
# ==============================================================================


def test_interface_down_state_detection() -> None:
    """Interfaces with DOWN state are parsed with enabled=False."""
    raw = """
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN
    inet 127.0.0.1/8 scope host lo
2: eth0: <BROADCAST,MULTICAST> mtu 1500 qdisc noop state DOWN group default qlen 1000
    link/ether 52:54:00:12:34:56 brd ff:ff:ff:ff:ff:ff
"""
    ifaces = _parse_linux_interfaces(raw)
    assert len(ifaces) == 2
    lo = next(i for i in ifaces if i["name"] == "lo")
    eth0 = next(i for i in ifaces if i["name"] == "eth0")
    assert lo["enabled"] is True
    assert eth0["enabled"] is False
    assert eth0["ipv4_address"] is None


def test_interface_ipv6_only_no_ipv4() -> None:
    """Interfaces with only IPv6 do not crash and set ipv4_address=None."""
    raw = """
2: eth1: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP
    link/ether 52:54:00:ab:cd:ef brd ff:ff:ff:ff:ff:ff
    inet6 2001:db8::1/64 scope global
    inet6 fe80::5054:ff:feab:cdef/64 scope link
"""
    ifaces = _parse_linux_interfaces(raw)
    assert len(ifaces) == 1
    assert ifaces[0]["name"] == "eth1"
    assert ifaces[0]["enabled"] is True
    assert ifaces[0]["ipv4_address"] is None
    # Conforms to DeviceIntent
    dev = DeviceIntent.model_validate({"hostname": "srv-1.croc.lab", "interfaces": ifaces})
    assert dev.interfaces[0].ipv4_address is None


def test_virtual_bridges_and_veth_pairs() -> None:
    """Virtual switches (docker0, br-lan, virbr0) and veth peer interfaces (@if4) parse cleanly."""
    raw = """
3: docker0: <NO-CARRIER,BROADCAST,MULTICAST,UP> mtu 1500 qdisc noqueue state DOWN group default
    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0
4: br-lan: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default
    inet 192.168.100.1/24 brd 192.168.100.255 scope global br-lan
5: veth123@if4: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default
    inet 10.200.0.2/24 brd 10.200.0.255 scope global veth123
6: virbr0: <NO-CARRIER,BROADCAST,MULTICAST,UP> mtu 1500 qdisc noqueue state DOWN group default
    inet 192.168.122.1/24 brd 192.168.122.255 scope global virbr0
"""
    ifaces = _parse_linux_interfaces(raw)
    names = [i["name"] for i in ifaces]
    assert names == ["docker0", "br-lan", "veth123", "virbr0"]
    dev = DeviceIntent.model_validate({"hostname": "srv-1.croc.lab", "interfaces": ifaces})
    assert len(dev.interfaces) == 4


def test_alias_interface_eth0_1_behavior() -> None:
    """Inspect handling of secondary IP / legacy alias interface eth0:1."""
    raw = """
2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP
    link/ether 52:54:00:12:34:56 brd ff:ff:ff:ff:ff:ff
    inet 192.168.1.10/24 brd 192.168.1.255 scope global eth0
    inet 192.168.1.11/24 brd 192.168.1.255 scope global secondary eth0:1
"""
    ifaces = _parse_linux_interfaces(raw)
    # The primary IP is captured; secondary alias IP is currently omitted (1 IP per interface model)
    assert len(ifaces) == 1
    assert ifaces[0]["name"] == "eth0"
    assert ifaces[0]["ipv4_address"] == "192.168.1.10/24"


def test_single_line_ip_o_addr_show_behavior() -> None:
    """Single-line output from ip -o addr show without newline returns a list."""
    raw_single = "2: eth0    inet 192.168.1.10/24 brd 192.168.1.255 scope global eth0"
    ifaces = _parse_linux_interfaces(raw_single)
    # Note: Requires newline to activate the multiline regex branch
    assert isinstance(ifaces, list)


def test_ip_o_overlapping_subnets_causes_device_intent_failure() -> None:
    """Verify that ip -o addr show passes through used_nets overlap filter,

    clearing overlapping IPv4 addresses and allowing DeviceIntent validation to succeed.
    """
    raw_ipo = (
        "1: lo    inet 127.0.0.1/8 scope host lo\n"
        "2: eth0    inet 192.168.1.10/24 brd 192.168.1.255 scope global eth0\n"
        "3: eth1    inet 192.168.1.20/24 brd 192.168.1.255 scope global eth1\n"
    )
    ifaces = _parse_linux_interfaces(raw_ipo)
    assert len(ifaces) == 3
    assert ifaces[1]["ipv4_address"] == "192.168.1.10/24"
    assert ifaces[2]["ipv4_address"] is None

    dev = DeviceIntent.model_validate({"hostname": "srv-1.croc.lab", "interfaces": ifaces})
    assert len(dev.interfaces) == 3


# ==============================================================================
# Suite 2: Boundary MTUs and Schema Conformance
# ==============================================================================


@pytest.mark.parametrize(
    ("raw_mtu", "expected_mtu"),
    [
        ("mtu 0", 68),
        ("mtu 1", 68),
        ("mtu 50", 68),
        ("mtu 67", 68),
        ("mtu 68", 68),
        ("mtu 1500", 1500),
        ("mtu 9000", 9000),
        ("mtu 9216", 9216),
        ("mtu 9217", 9216),
        ("mtu 65536", 9216),
        ("mtu -100", 1500),  # Non-digit regex failure -> defaults to 1500
        ("mtu invalid", 1500),  # Non-numeric -> defaults to 1500
    ],
)
def test_mtu_boundaries_clamping(raw_mtu: str, expected_mtu: int) -> None:
    """Verify that all boundary and malformed MTUs are clamped strictly within [68, 9216]."""
    raw = f"2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> {raw_mtu} qdisc fq_codel\n inet 10.0.0.1/24"
    ifaces = _parse_linux_interfaces(raw)
    assert len(ifaces) == 1
    assert ifaces[0]["mtu"] == expected_mtu
    # Strictly valid according to InterfaceIntent schema (ge=68, le=9216)
    dev = DeviceIntent.model_validate({"hostname": "srv-1.croc.lab", "interfaces": ifaces})
    assert dev.interfaces[0].mtu == expected_mtu


# ==============================================================================
# Suite 3: Degraded POSIX Hardware and Metric Parsing
# ==============================================================================


def test_proc_meminfo_degraded_memavailable_greater_than_total() -> None:
    """Verify that when /proc/meminfo reports MemAvailable > MemTotal,

    parse_linux_memory clamps the utilization to 0.0.
    """
    raw = "MemTotal:       1000 kB\nMemAvailable:   1500 kB\nMemFree:         200 kB\n"
    res = parse_linux_memory(raw)
    assert res is not None
    assert res == 0.0


def test_proc_meminfo_degraded_zero_total() -> None:
    """When /proc/meminfo reports 0 total memory, parse_linux_memory falls back gracefully."""
    raw = "MemTotal: 0 kB\nMemFree: 0 kB\n"
    assert parse_linux_memory(raw) is None


def test_df_multiline_wrapping_column_mismatch() -> None:
    """Verify that when a long logical volume name wraps in df -m /,

    _parse_ssh_hardware_specs accurately parses the 'Total' column (parts[0]).
    """
    wrapped_df_out = (
        "Filesystem           1M-blocks      Used Available Use% Mounted on\n"
        "/dev/mapper/ubuntu--vg-ubuntu--lv\n"
        "                        100000     20000     75000  21% /\n"
    )
    conn = MockSSHAdapter(
        {
            "uname -a": "Linux srv-1 5.15.0 x86_64",
            "nproc": "4",
            "grep MemTotal /proc/meminfo": "MemTotal: 8388608 kB",
            "df -m /": wrapped_df_out,
        }
    )
    _plat, _ver, _cpu, _ram, disk_gb = _parse_ssh_hardware_specs(
        conn, Platform.LINUX_SERVER, "fallback"
    )
    # The total disk is 100000 MB (97.66 GB), accurately extracted from parts[0]
    assert disk_gb == 97.66


def test_df_h_kibibytes_unhandled() -> None:
    """When df -h / reports in KiB ('K'), _parse_ssh_hardware_specs returns None."""
    df_h_out = (
        "Filesystem      Size  Used Avail Use% Mounted on\n"
        "/dev/root       500K  100K  380K  21% /\n"
    )
    conn = MockSSHAdapter(
        {
            "uname -a": "Linux srv-1 5.15.0 x86_64",
            "nproc": "2",
            "grep MemTotal /proc/meminfo": "MemTotal: 4194304 kB",
            "df -m /": "df: unrecognized option: -m",
            "df -h /": df_h_out,
        }
    )
    _plat, _ver, _cpu, _ram, disk_gb = _parse_ssh_hardware_specs(
        conn, Platform.LINUX_SERVER, "fallback"
    )
    # Unit 'K' is omitted from if/elif in discovery.py, so disk_gb remains None
    assert disk_gb is None


def test_parse_linux_cpu_idle_percentage() -> None:
    """parse_linux_cpu calculates 100 - idle across typical top outputs."""
    assert parse_linux_cpu("%Cpu(s): 10.0 us, 5.0 sy, 0.0 ni, 85.0 id") == 15.0
    assert parse_linux_cpu("%Cpu(s): 0.0 us, 0.0 sy, 0.0 ni, 100.0 id") == 0.0
    assert parse_linux_cpu("%Cpu(s): 99.0 us, 1.0 sy, 0.0 ni, 0.0 id") == 100.0
    assert parse_linux_cpu("corrupted text without idle token") is None


# ==============================================================================
# Suite 4: Protocol Cascade Adversarial Probing & False Positives
# ==============================================================================


def test_cli_syntax_error_grpc_false_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that when a switch returns an invalid CLI syntax error containing

    the token 'grpc', detect_optimal_telemetry_protocol rejects gNMI and falls back to SSH_CLI.
    """
    # Block sockets to isolate CLI detection
    monkeypatch.setattr(socket, "create_connection", MagicMock(side_effect=OSError("Blocked")))

    res = detect_optimal_telemetry_protocol(
        "192.0.2.1",
        "cisco_iosxe",
        ssh_send=lambda cmd: "% Invalid input detected at marker: show grpc status",
        timeout=0.05,
    )
    assert res["protocol"] == TelemetryProtocol.SSH_CLI
    assert res["cli_supported"] is True


def test_node_exporter_stopped_cli_false_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that when ssh command returns 'node_exporter: no process found',

    detect_optimal_telemetry_protocol rejects node_exporter and falls back to SSH_CLI.
    """
    monkeypatch.setattr(socket, "create_connection", MagicMock(side_effect=OSError("Blocked")))

    res = detect_optimal_telemetry_protocol(
        "192.0.2.1",
        "linux_server",
        ssh_send=lambda cmd: "node_exporter: no process found in process table",
        timeout=0.05,
    )
    assert res["protocol"] == TelemetryProtocol.SSH_CLI
    assert res["cli_supported"] is True


# ==============================================================================
# Suite 5: Concurrency and Multi-Server Onboarding Stress Harness
# ==============================================================================


def test_concurrent_server_onboarding_inventory_race_condition() -> None:
    """Stress harness: 15 Linux servers onboarded simultaneously via bind_device_to_sot.

    Demonstrates that lack of file locking on inventory.yaml causes concurrent write loss.
    """
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        inv_path = tmp_dir / "inventory.yaml"
        inv_path.write_text("devices:\n", encoding="utf-8")

        class FakeSettings:
            def __init__(self, p: Path) -> None:
                self.intent_repo_path = p
                self.offline_lab_path = p / "offline"
                self.auth_profiles: dict[str, Any] = {}

        class FakeContainer:
            def __init__(self, s: FakeSettings) -> None:
                self.settings = s

        container = FakeContainer(FakeSettings(tmp_dir))
        num_servers = 15
        work_items: list[tuple[DeviceCreate, Device]] = []
        for i in range(num_servers):
            dev = Device(
                id=100 + i,
                hostname=f"srv-{i}.croc.lab",
                management_ip=f"10.0.0.{i+10}",
                management_port=22,
                platform=Platform.LINUX_SERVER,
                role=DeviceRole.SERVER,
                auth_profile=f"srv_auth_{i}",
            )
            data = DeviceCreate(
                hostname=f"srv-{i}.croc.lab",
                management_ip=f"10.0.0.{i+10}",
                management_port=22,
                platform=Platform.LINUX_SERVER,
                role=DeviceRole.SERVER,
                auth_profile=f"srv_auth_{i}",
                username=f"user_{i}",
                password="secret_password",
            )
            work_items.append((data, dev))

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(bind_device_to_sot, d, dev, container) for d, dev in work_items]
            concurrent.futures.wait(futures)

        final_inv = yaml.safe_load(inv_path.read_text(encoding="utf-8")) or {}
        saved_devices = final_inv.get("devices") or []
        saved_hostnames = {d["hostname"] for d in saved_devices}

        # Assert all device intent YAML files were generated in devices/
        devices_dir = tmp_dir / "devices"
        intent_files = list(devices_dir.glob("*.yaml"))
        assert len(intent_files) == num_servers

        # Assert all 15 servers were persisted to inventory.yaml without write loss
        missing = {f"srv-{i}.croc.lab" for i in range(num_servers)} - saved_hostnames
        assert len(intent_files) == num_servers
        assert len(saved_hostnames) == num_servers
        assert not missing
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
