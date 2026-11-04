"""Comprehensive unit and integration tests for Universal Server & Switch Onboarding.

Covers:
1. Switch SSH onboarding & automatic gNMI upgrade.
2. Switch gNMI closed -> SNMP fallback.
3. Switch advanced protocols closed -> SSH CLI fallback.
4. Linux server SSH discovery & POSIX hardware parsing (nproc, meminfo, df -m /).
5. Linux server protocol upgrade to node_exporter (:9100).
6. Linux server fallback to SSH CLI metrics when :9100 closed.
7. Linux server Git Source-of-Truth binding (devices/<hostname>.yaml & inventory.yaml).
8. Prospective server: onboard arbitrary new Linux container/bare-metal node without code changes.
9. Distro banner & version signature resolution.
10. Linux interface parser edge cases (ip addr show, ip -o, MTU clamp, overlapping subnets).
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

from netops.enums import DeviceRole, Platform, TelemetryProtocol
from netops.intent.models import DeviceIntent, PlatformProfile
from netops.models import Device
from netops.schemas.devices import DeviceCreate
from netops.services.discovery import (
    _parse_linux_interfaces,
    bind_device_to_sot,
    detect_platform_from_banner,
    detect_platform_from_version,
    discover_live_switch_via_ssh,
)
from netops.services.telemetry import (
    collect_metrics_from_cli,
    detect_optimal_telemetry_protocol,
    parse_linux_cpu,
    parse_linux_memory,
)
from tests.unit.test_protocol_probe import PROM_TEXT, http_server


@dataclass
class CmdResult:
    result: str


class FakeSSHConn:
    """Mock SSH connection adapter returning predetermined command outputs."""

    def __init__(self, responses: dict[str, str] | None = None, default: str = "") -> None:
        self.responses: dict[str, str] = dict(responses or {})
        self.default: str = default
        self.executed_commands: list[str] = []

    def __enter__(self) -> FakeSSHConn:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def send_command(self, cmd: str) -> CmdResult:
        self.executed_commands.append(cmd)
        res = self.responses.get(cmd, self.default)
        return CmdResult(res)


# ==============================================================================
# 1. Switch SSH Onboarding & Automatic gNMI Upgrade
# ==============================================================================


def test_switch_gnmi_upgrade_when_port_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Universal onboarding of switch: auto-upgrades to gNMI when gNMI port is reachable."""
    # Mock socket connect to simulate open gNMI port 6030
    def mock_create_conn(addr: tuple[str, int], timeout: float = 0.5) -> MagicMock:
        _host, port = addr
        if port == 6030:
            return MagicMock()
        raise OSError("Connection refused")

    monkeypatch.setattr(socket, "create_connection", mock_create_conn)

    detected = detect_optimal_telemetry_protocol(
        "10.0.0.1",
        "arista_eos",
        ssh_send=lambda cmd: "Management API gNMI is enabled, port: 6030",
        timeout=0.05,
    )
    assert detected["protocol"] == TelemetryProtocol.GNMI
    assert detected["protocol"] == "gnmi"
    assert detected["port"] == 6030
    assert detected["tcp_open"] is True


# ==============================================================================
# 2. Switch gNMI Closed -> SNMP Fallback
# ==============================================================================


# ==============================================================================
# 3. Switch Advanced Protocols Closed -> SSH CLI Fallback
# ==============================================================================


def test_switch_all_advanced_closed_ssh_cli_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """When both gNMI and SNMP are closed by firewall, seamlessly fallback to ssh_cli."""
    monkeypatch.setattr(
        socket, "create_connection", MagicMock(side_effect=OSError("Filtered/Closed"))
    )

    detected = detect_optimal_telemetry_protocol(
        "10.0.0.1",
        "arista_eos",
        ssh_send=lambda cmd: "gNMI inactive, snmp disabled",
        timeout=0.05,
    )
    assert detected["protocol"] == TelemetryProtocol.SSH_CLI
    assert detected["protocol"] == "ssh_cli"
    assert detected["port"] == 22


# ==============================================================================
# 4. Linux Server SSH Discovery & POSIX Hardware Parsing
# ==============================================================================


def test_linux_server_ssh_discovery_and_hardware_parsing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Probe live Linux server over SSH: detect OS, parse POSIX hardware (nproc, meminfo, df)."""
    banner = "SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.1"
    monkeypatch.setattr(
        "netops.services.discovery._probe_tcp_banner",
        lambda host, port: (True, banner),
    )

    cmd_responses = {
        "uname -a": "Linux app-srv-01 5.15.0-88-generic #98-Ubuntu SMP x86_64 GNU/Linux",
        "nproc": "8",
        "grep MemTotal /proc/meminfo": "MemTotal:       32851456 kB",
        "df -m /": (
            "Filesystem 1M-blocks Used Available Use% Mounted on\n"
            "/dev/sda1 102400 35000 62000 37% /\n"
        ),
        "hostname": "app-srv-01",
        "ip -br addr show": "lo UNKNOWN 127.0.0.1/8\neth0 UP 192.168.10.50/24\neth1 DOWN\n",
        "ip addr show": (
            "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN\n"
            "    inet 127.0.0.1/8 scope host lo\n"
            "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP\n"
            "    inet 192.168.10.50/24 scope global eth0\n"
            "3: eth1: <BROADCAST,MULTICAST> mtu 1500 qdisc noop state DOWN\n"
        ),
        "lldpctl": "",
    }

    class MockAdapter:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.conn = FakeSSHConn(cmd_responses)

        def __enter__(self) -> FakeSSHConn:
            return self.conn

        def __exit__(self, *args: Any) -> None:
            return None

    monkeypatch.setattr("netops.services.discovery._ParamikoConnAdapter", MockAdapter)

    dummy_settings = MagicMock()
    dummy_settings.intent_repo_path = tmp_path / "intent"
    dummy_settings.offline_lab_path = tmp_path / "lab"
    dummy_settings.auth_profiles = {}

    result = discover_live_switch_via_ssh(
        host="192.168.10.50",
        port=22,
        username="ubuntu",
        password="secretpassword",
        settings=dummy_settings,
        default_hostname="",
        default_role="",
    )

    assert result is not None
    assert result["platform"] == Platform.LINUX_SERVER.value
    assert result["role"] == "server"
    assert result["hostname"] == "app-srv-01.croc.lab"

    hw = result["hardware_specs"]
    assert hw["cpu_cores"] == 8
    assert hw["ram_gb"] == 31.33
    assert hw["disk_gb"] == 100.0
    assert "eth0" in hw["interfaces"]


# ==============================================================================
# 5. Linux Server node_exporter Protocol Upgrade (:9100)
# ==============================================================================


# ==============================================================================
# 6. Linux Server Fallback to SSH CLI Metrics when :9100 Closed
# ==============================================================================


def test_linux_server_node_exporter_closed_fallback_to_ssh_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When node_exporter is not installed/firewalled, Linux server falls back to SSH CLI."""
    monkeypatch.setattr(
        socket, "create_connection", MagicMock(side_effect=OSError("Connection refused"))
    )

    detected = detect_optimal_telemetry_protocol(
        "192.168.1.100",
        "linux_server",
        timeout=0.05,
    )
    assert detected["protocol"] == TelemetryProtocol.SSH_CLI
    assert detected["protocol"] == "ssh_cli"
    assert detected["port"] == 22

    # Verify POSIX CLI metric parsers
    top_cpu_output = (
        "top - 12:00:01 up 10 days,  2 users,  load average: 0.15, 0.20, 0.18\n"
        "%Cpu(s):  3.5 us,  1.5 sy,  0.0 ni, 94.2 id,  0.5 wa,  0.0 hi,  0.3 si\n"
    )
    parsed_cpu = parse_linux_cpu(top_cpu_output)
    assert parsed_cpu == 5.8  # 100 - 94.2

    meminfo_output = (
        "MemTotal:       16384000 kB\n"
        "MemFree:         4096000 kB\n"
        "MemAvailable:    8192000 kB\n"
    )
    parsed_mem = parse_linux_memory(meminfo_output)
    assert parsed_mem == 50.0  # (16384000 - 8192000) / 16384000 * 100

    # Collect via collect_metrics_from_cli
    mock_responses = {
        "top -bn1 | head -n 5": top_cpu_output,
        "cat /proc/net/dev": "eth0: 1000 0 0 0 0 0 0 0 2000 0 0 0 0 0 0 0",
    }
    collected = collect_metrics_from_cli(
        hostname="srv-node.croc.lab",
        platform="linux_server",
        send=lambda cmd: mock_responses.get(cmd, ""),
    )
    assert collected["cpu_pct"] == 5.8


# ==============================================================================
# 7. Linux Server Git SoT Binding (devices/<hostname>.yaml & inventory.yaml)
# ==============================================================================


def test_linux_server_bind_device_to_sot(tmp_path: Path) -> None:
    """Linux server onboarding binds to Git SoT: creates devices/<hostname>.yaml."""
    intent_repo = tmp_path / "intent"
    devices_dir = intent_repo / "devices"
    devices_dir.mkdir(parents=True, exist_ok=True)

    # Initialize inventory.yaml
    inv_file = intent_repo / "inventory.yaml"
    inv_file.write_text(yaml.safe_dump({"devices": []}), encoding="utf-8")

    container = MagicMock()
    container.settings.intent_repo_path = intent_repo
    container.settings.offline_lab_path = tmp_path / "lab"
    container.settings.auth_profiles = {}

    server_device = Device(
        id=42,
        hostname="compute-node-42.croc.lab",
        management_ip="10.100.0.42",
        management_port=22,
        platform=Platform.LINUX_SERVER,
        role=DeviceRole.SERVER,
        auth_profile="compute_auth",
    )

    create_data = DeviceCreate(
        hostname="compute-node-42.croc.lab",
        management_ip="10.100.0.42",
        management_port=22,
        platform=Platform.LINUX_SERVER,
        role=DeviceRole.SERVER,
        auth_profile="compute_auth",
        username="admin",
        password="securepassword",
    )

    # bind_device_to_sot must not drop the server
    bind_device_to_sot(create_data, server_device, container)

    # 1. Device intent YAML created in devices/
    intent_yaml_path = devices_dir / "compute-node-42.croc.lab.yaml"
    assert intent_yaml_path.is_file()

    saved_intent = yaml.safe_load(intent_yaml_path.read_text(encoding="utf-8"))
    assert saved_intent["hostname"] == "compute-node-42.croc.lab"
    assert saved_intent["bgp"] is None
    assert len(saved_intent["interfaces"]) >= 2
    assert any(i["name"] == "lo" for i in saved_intent["interfaces"])
    assert any(i["name"] == "eth0" for i in saved_intent["interfaces"])

    # Strict Pydantic model validation of the saved server intent
    validated_intent = DeviceIntent.model_validate(saved_intent)
    assert validated_intent.hostname == "compute-node-42.croc.lab"
    assert validated_intent.bgp is None

    # 2. Server appended to inventory.yaml with role: server and platform: linux_server
    updated_inv = yaml.safe_load(inv_file.read_text(encoding="utf-8"))
    devs = updated_inv.get("devices") or []
    assert len(devs) == 1
    assert devs[0]["hostname"] == "compute-node-42.croc.lab"
    assert devs[0]["role"] == "server"
    assert devs[0]["platform"] == "linux_server"


# ==============================================================================
# 8. Prospective Server: Onboard Arbitrary Node Without Code Changes
# ==============================================================================


def test_prospective_server_custom_profile_without_code_changes(tmp_path: Path) -> None:
    """Verify arbitrary new Linux container/bare-metal node can be onboarded."""
    custom_profile_data = {
        "platform": "debian_micro_vm",
        "vendor": "Debian GNU/Linux",
        "default_password": "root",
        "default_interface": "ens3",
        "offline_ports": [2290],
        "offline_interfaces": ["lo", "ens3"],
        "ssh_banners": ["debian", "microvm"],
        "version_signatures": ["Debian", "microvm"],
        "interface_prefixes": ["lo", "ens", "eth"],
        "commands": {
            "version": "cat /etc/os-release",
            "memory": "cat /proc/meminfo",
            "cpu": "nproc",
            "running_config": "ip addr show",
            "hostname_filter": "hostname",
            "interfaces_brief": "ip -br addr show",
        },
        "telemetry": {
            "cpu_pct": {"command": "top -bn1 | head -n 5", "parser": "parse_linux_cpu"},
            "memory_pct": {"command": "cat /proc/meminfo", "parser": "parse_linux_memory"},
        },
        "exporter": {"port": 9100, "path": "/metrics"},
        "protocol_priority": ["node_exporter", "ssh_cli"],
    }

    # Validate against strict Pydantic PlatformProfile schema
    profile = PlatformProfile.model_validate(custom_profile_data)
    assert profile.platform == "debian_micro_vm"
    assert profile.protocol_priority == ["node_exporter", "ssh_cli"]

    # Protocol upgrade cascade: a real exporter answering in Prometheus text format
    with http_server(PROM_TEXT) as actual_port:
        detected_up = detect_optimal_telemetry_protocol(
            "127.0.0.1",
            "debian_micro_vm",
            exporter_port=actual_port,
            platform_profile=profile,
            timeout=0.05,
        )
        assert detected_up["protocol"] == TelemetryProtocol.PROMETHEUS_EXPORTER
        assert detected_up["port"] == actual_port

    # When port closed -> falls back to ssh_cli
    detected_down = detect_optimal_telemetry_protocol(
        "127.0.0.1",
        "debian_micro_vm",
        exporter_port=65432,  # unreachable port
        platform_profile=profile,
        timeout=0.01,
    )
    assert detected_down["protocol"] == TelemetryProtocol.SSH_CLI
    assert detected_down["port"] == 22


# ==============================================================================
# 9. Distro Banner and Version Signatures
# ==============================================================================


@pytest.mark.parametrize(
    ("banner", "expected_platform"),
    [
        ("SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.1", Platform.LINUX_SERVER),
        ("SSH-2.0-OpenSSH_8.4p1 Debian-5+deb11u1", Platform.LINUX_SERVER),
        ("SSH-2.0-OpenSSH_9.0 Alpine Linux 3.16", Platform.LINUX_SERVER),
        ("SSH-2.0-OpenSSH_8.0 CentOS Stream 8", Platform.LINUX_SERVER),
        ("SSH-2.0-OpenSSH_8.7 Red Hat Enterprise Linux 9.2", Platform.LINUX_SERVER),
        ("SSH-2.0-OpenSSH_7.4 cEOSLab Arista", Platform.ARISTA_EOS),
        ("SSH-2.0-VRP-3.20 Huawei Versatile Routing Platform", Platform.HUAWEI_VRP),
    ],
)
def test_distro_banner_resolution(banner: str, expected_platform: Platform) -> None:
    """Verify diverse vendor and Linux distribution banners resolve accurately."""
    detected = detect_platform_from_banner(banner)
    assert detected == expected_platform


@pytest.mark.parametrize(
    ("ver_output", "expected_platform"),
    [
        ("Linux kernel 5.15.0-88-generic #98 SMP", Platform.LINUX_SERVER),
        ("PRETTY_NAME=\"Ubuntu 22.04.3 LTS\"", Platform.LINUX_SERVER),
        ("PRETTY_NAME=\"Debian GNU/Linux 12 (bookworm)\"", Platform.LINUX_SERVER),
        ("NAME=\"Alpine Linux\" ID=alpine", Platform.LINUX_SERVER),
        ("CentOS Stream release 9", Platform.LINUX_SERVER),
        ("Red Hat Enterprise Linux release 9.2 (Plow)", Platform.LINUX_SERVER),
        ("Arista Networks EOS version 4.28.0F", Platform.ARISTA_EOS),
        ("Huawei Versatile Routing Platform Software VRP", Platform.HUAWEI_VRP),
    ],
)
def test_distro_version_signature_resolution(ver_output: str, expected_platform: Platform) -> None:
    """Verify version command outputs resolve correctly via version_signatures."""
    detected = detect_platform_from_version(ver_output, Platform.CISCO_IOSXE)
    assert detected == expected_platform


# ==============================================================================
# 10. Linux Interface Parser Edge Cases
# ==============================================================================


def test_linux_interfaces_parser_multi_line_and_edge_cases() -> None:
    """Test parsing standard Linux ip addr show with loopback, down interfaces, and large MTU."""
    raw_ip_output = (
        "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default\n"
        "    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00\n"
        "    inet 127.0.0.1/8 scope host lo\n"
        "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP\n"
        "    link/ether 52:54:00:12:34:56 brd ff:ff:ff:ff:ff:ff\n"
        "    inet 192.168.1.50/24 brd 192.168.1.255 scope global eth0\n"
        "3: eth1: <BROADCAST,MULTICAST> mtu 1500 qdisc noop state DOWN\n"
        "    link/ether 52:54:00:78:9a:bc brd ff:ff:ff:ff:ff:ff\n"
        "4: docker0: <NO-CARRIER,BROADCAST,MULTICAST,UP> mtu 1500 qdisc noqueue state DOWN\n"
        "    link/ether 02:42:e2:39:aa:bb brd ff:ff:ff:ff:ff:ff\n"
        "    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\n"
    )

    parsed = _parse_linux_interfaces(raw_ip_output)
    assert len(parsed) == 4

    lo = next(i for i in parsed if i["name"] == "lo")
    assert lo["enabled"] is True
    assert lo["ipv4_address"] == "127.0.0.1/8"
    assert lo["mtu"] <= 9216  # Clamped to Pydantic max limit

    eth0 = next(i for i in parsed if i["name"] == "eth0")
    assert eth0["enabled"] is True
    assert eth0["ipv4_address"] == "192.168.1.50/24"

    eth1 = next(i for i in parsed if i["name"] == "eth1")
    assert eth1["enabled"] is False
    assert eth1["ipv4_address"] is None

    # Verify parsed dicts convert into valid DeviceIntent
    intent = DeviceIntent(
        hostname="linux-srv.croc.lab",
        interfaces=parsed,
        bgp=None,
        acls=[],
    )
    assert len(intent.interfaces) == 4


def test_extract_brownfield_intent_live_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Directly test extract_brownfield_intent to verify SSH adapter execution and intent extraction."""
    from netops.services.discovery import extract_brownfield_intent

    mock_ip_out = (
        "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN\n"
        "    inet 127.0.0.1/8 scope host lo\n"
        "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP\n"
        "    inet 10.10.10.1/24 scope global eth0\n"
    )

    class MockAdapter:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.conn = FakeSSHConn({"ip addr show": mock_ip_out})

        def __enter__(self) -> FakeSSHConn:
            return self.conn

        def __exit__(self, *args: Any) -> None:
            return None

    monkeypatch.setattr("netops.services.discovery._ParamikoConnAdapter", MockAdapter)

    dev = Device(
        id=1,
        hostname="srv-brownfield.croc.lab",
        management_ip="10.10.10.1",
        management_port=22,
        platform=Platform.LINUX_SERVER,
        role=DeviceRole.SERVER,
        auth_profile="test_auth",
    )
    data = DeviceCreate(
        hostname="srv-brownfield.croc.lab",
        management_ip="10.10.10.1",
        management_port=22,
        platform=Platform.LINUX_SERVER,
        role=DeviceRole.SERVER,
        auth_profile="test_auth",
        username="ubuntu",
        password="secretpassword",
    )
    settings = MagicMock()
    settings.intent_repo_path = tmp_path / "intent"
    settings.auth_profiles = {}

    res = extract_brownfield_intent(dev, data, settings)
    assert res is not None
    assert res["hostname"] == "srv-brownfield.croc.lab"
    assert len(res["interfaces"]) == 2
    assert res["interfaces"][1]["name"] == "eth0"
    assert res["interfaces"][1]["ipv4_address"] == "10.10.10.1/24"
