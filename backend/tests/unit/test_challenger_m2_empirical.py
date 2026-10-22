"""Empirical Challenge Test Suite: Dynamic Platform Extensibility & Zero-Hardcode Verification.

Authored by Challenger 2 (M2) to empirically stress-test:
1. Dynamic addition of a brand new platform ('custom_future_switch') without any Python code edits.
2. CLI metric collection and protocol detection driven 100% by YAML profile.
3. Concurrent loading and validation of all 7 platform profiles.
4. Concurrent parallel fleet telemetry polling across all 7 platforms.
5. Error handling on malformed YAML, extra keys, and command execution failures.
"""

from __future__ import annotations

import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from netops.intent.models import PlatformProfile
from netops.intent.repository import IntentRepository
from netops.services.telemetry import (
    clear_live_metrics,
    collect_fleet_metrics_parallel,
    collect_metrics_from_cli,
    detect_optimal_telemetry_protocol,
    load_profile_for_platform,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
PLATFORMS_DIR = REPO_ROOT / "intent" / "platforms"

ALL_SEVEN_PLATFORMS = [
    "cisco_iosxe",
    "arista_eos",
    "huawei_vrp",
    "eltex_mes",
    "juniper_junos",
    "yadro_kornfe",
    "linux_server",
]


MOCK_CUSTOM_YAML_CONTENT = """
platform: custom_future_switch
vendor: FutureSwitch Networks Inc
default_password: future_master_key
default_interface: HundredGigE1/0/1
offline_ports: [2299]
offline_interfaces:
  - HundredGigE1/0/1
  - HundredGigE1/0/2
ssh_banners:
  - futureos
  - custom_future_switch
version_signatures:
  - "FutureOS Software Version 10.4"
interface_prefixes:
  - HundredGigE
  - FortyGigE
  - Loopback
lldp_port_prefixes:
  - HundredGigE
  - FortyGigE
commands:
  version: show system version
  memory: show system memory
  cpu: show system cpu
  running_config: show configuration running
  hostname_filter: show configuration | include hostname
  interfaces_brief: show interfaces summary
  lldp_neighbors: show lldp neighbors detail
telemetry:
  cpu_pct:
    command: show system cpu-summary
    parser: parse_arista_cpu
  memory_pct:
    command: show system memory-summary
    parser: parse_cisco_memory
  uplink_util_pct:
    command: show interfaces rate-summary
    parser: max_cisco_link_utilization
gnmi:
  default_port: 6030
  check_command: show gnmi service status
  match_tokens:
    - active
    - running
    - enabled
    - "6030"
snmp:
  base_port: 161
  clab_offset: 0
exporter:
  port: 9100
  path: /metrics
protocol_priority:
  - gnmi
  - node_exporter
  - snmp
  - ssh_cli
emergency_commands:
  reset_bgp_sessions: clear bgp *
  rollback_last_commit: rollback configuration 1
  restart_services: systemctl restart network-routing
  reboot: systemctl reboot -f
  default: show system version
"""


@pytest.fixture
def custom_platform_yaml_file(tmp_path: Path):
    """Create a temporary custom platform YAML and provide path."""
    pdir = tmp_path / "platforms"
    pdir.mkdir(parents=True)
    custom_file = pdir / "custom_future_switch.yaml"
    custom_file.write_text(MOCK_CUSTOM_YAML_CONTENT, encoding="utf-8")
    return tmp_path, custom_file


def test_zero_hardcode_custom_platform_loading(custom_platform_yaml_file):
    """Test 1: Mock platform profile 'custom_future_switch.yaml' is loaded & validated via IntentRepository."""
    tmp_path, custom_file = custom_platform_yaml_file
    repo = IntentRepository(tmp_path)

    profile = repo.get_platform_profile("custom_future_switch")
    assert profile is not None
    assert profile.platform == "custom_future_switch"
    assert profile.vendor == "FutureSwitch Networks Inc"
    assert profile.default_interface == "HundredGigE1/0/1"
    assert "cpu_pct" in profile.telemetry
    assert profile.telemetry["cpu_pct"].parser == "parse_arista_cpu"
    assert profile.gnmi is not None
    assert profile.gnmi.default_port == 6030

    # Also test load_profile_for_platform with repo_path
    resolved_prof = load_profile_for_platform("custom_future_switch", repo_path=tmp_path)
    assert resolved_prof is not None
    assert resolved_prof.vendor == "FutureSwitch Networks Inc"


def test_zero_hardcode_telemetry_collection_without_python_changes(custom_platform_yaml_file):
    """Test 2: collect_metrics_from_cli() parses telemetry from mock CLI output with ZERO Python modifications."""
    tmp_path, custom_file = custom_platform_yaml_file
    repo = IntentRepository(tmp_path)
    profile = repo.get_platform_profile("custom_future_switch")

    mock_device_responses = {
        "show system cpu-summary": "%Cpu(s): 14.5 us,  3.2 sy,  0.0 ni, 82.3 id",
        "show system memory-summary": "Processor Pool Total: 16000000 Used: 4800000",
        "show interfaces rate-summary": "5 minute input rate 120000000 bits/sec, 5 minute output rate 80000000 bits/sec",
    }

    clear_live_metrics("future-switch-01.dc")
    metrics = collect_metrics_from_cli(
        hostname="future-switch-01.dc",
        platform="custom_future_switch",
        send=lambda cmd: mock_device_responses.get(cmd, ""),
        platform_profile=profile,
    )

    assert "cpu_pct" in metrics
    assert "memory_pct" in metrics
    assert "uplink_util_pct" in metrics

    # Verify calculated values:
    # CPU: 100 - 82.3 = 17.7%
    assert metrics["cpu_pct"] == 17.7
    # Memory: 4800000 / 16000000 * 100 = 30.0%
    assert metrics["memory_pct"] == 30.0
    # Uplink: 120 Mbps / 1000 Mbps * 100 = 12.0%
    assert metrics["uplink_util_pct"] == 12.0


def test_zero_hardcode_optimal_protocol_detection(custom_platform_yaml_file):
    """Test 3: detect_optimal_telemetry_protocol() auto-detects protocols for custom platform."""
    tmp_path, custom_file = custom_platform_yaml_file
    repo = IntentRepository(tmp_path)
    profile = repo.get_platform_profile("custom_future_switch")

    # 1. gNMI: CLI говорит «включён», но порт закрыт → не gNMI (открытый порт обязателен)
    closed = detect_optimal_telemetry_protocol(
        "127.0.0.1",
        "custom_future_switch",
        ssh_send=lambda cmd: "gNMI service status: active and listening on port 6030",
        platform_profile=profile,
        gnmi_port=1,
        timeout=0.01,
    )
    assert closed["protocol"] == "ssh_cli"

    # 2. CLI подтверждает + реальный TCP-листенер, gnmic недоступен → gNMI (cli+tcp)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        opt_gnmi = detect_optimal_telemetry_protocol(
            "127.0.0.1",
            "custom_future_switch",
            ssh_send=lambda cmd: "gNMI service status: active and listening on port 6030",
            platform_profile=profile,
            gnmi_port=port,
            timeout=0.2,
        )
    assert opt_gnmi["protocol"] == "gnmi"
    assert opt_gnmi["port"] == port
    assert opt_gnmi["cli_supported"] is True
    assert opt_gnmi["verified_by"] == "cli+tcp"

    # 2. Advanced protocols inactive -> Fallback to SSH CLI
    opt_fallback = detect_optimal_telemetry_protocol(
        "127.0.0.1",
        "custom_future_switch",
        ssh_send=lambda cmd: "Service disabled: gNMI is not configured",
        platform_profile=profile,
        timeout=0.01,
    )
    assert opt_fallback["protocol"] == "ssh_cli"
    assert opt_fallback["port"] == 22


def test_concurrent_loading_and_validation_all_seven_platforms():
    """Test 4: Requirement 2 — Verify all 7 platform profiles load and validate concurrently."""
    repo = IntentRepository(PLATFORMS_DIR.parent)

    def _load_and_validate(thread_id: int) -> dict[str, str]:
        loaded: dict[str, str] = {}
        for plat_name in ALL_SEVEN_PLATFORMS:
            # 1. Direct YAML model_validate
            yaml_path = PLATFORMS_DIR / f"{plat_name}.yaml"
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            prof_direct = PlatformProfile.model_validate(raw)

            # 2. Repository cache load
            prof_repo = repo.get_platform_profile(plat_name)

            assert prof_direct.vendor == prof_repo.vendor
            loaded[plat_name] = prof_direct.vendor
        return loaded

    # 32 concurrent worker threads
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(_load_and_validate, i) for i in range(32)]
        for f in as_completed(futures):
            res = f.result()
            assert len(res) == 7
            assert set(res.keys()) == set(ALL_SEVEN_PLATFORMS)


def test_concurrent_parallel_fleet_telemetry_all_seven_platforms():
    """Test 5: Concurrent parallel fleet telemetry polling executes reliably across all 7 platform profiles."""
    responses = {
        "cisco_iosxe": {
            "show processes cpu | include CPU utilization": "CPU utilization for five seconds: 15%/2%; one minute: 14%; five minutes: 12%",
            "show processes memory | include Processor Pool": "Processor Pool Total: 1000000 Used: 350000",
            "show interfaces | include rate": "5 minute input rate 25000000 bits/sec, 500 packets/sec",
        },
        "arista_eos": {
            "show processes top once | include Cpu": "%Cpu(s): 10.2 us,  3.1 sy,  0.0 ni, 86.7 id",
            "show processes top once | include Mem": "MiB Mem :  4000.0 total,  1200.0 free,  2800.0 used",
            "show interfaces counters rates": "Et1    1000000000   15.4%   1000000000   12.1%",
        },
        "huawei_vrp": {
            "display cpu": "cpu0 22.5%",
            "display memory": "Memory Using Percentage Is: 45%",
            "display interface brief": "GE1/0/1  up  up  25.0%  18.0%",
        },
        "eltex_mes": {
            "show cpu utilization": "CPU utilization for five seconds: 18%",
            "show memory": "Memory Using Percentage Is: 40%",
            "show interfaces | include rate": "input rate 30000000 bits/sec",
        },
        "juniper_junos": {
            "show chassis routing-engine | match Idle": "Idle 78 percent",
            "show chassis routing-engine | match Memory": "Memory Using Percentage Is: 52%",
            "show interfaces extensive | match bps": "input rate 40000000 bits/sec",
        },
        "yadro_kornfe": {
            "top -bn1 | head -n 5": "%Cpu(s): 8.5 us, 2.1 sy, 0.0 ni, 89.4 id\nMiB Mem :  8000.0 total,  3200.0 free,  4800.0 used",
            "show interfaces counters rates": "Ethernet1/1  up  up  32.0%  28.0%",
        },
        "linux_server": {
            "top -bn1 | head -n 5": "%Cpu(s):  5.0 us,  2.0 sy,  0.0 ni, 93.0 id\nMiB Mem :   7890.0 total,   3100.0 free,   3500.0 used",
            "cat /proc/net/dev": "eth0: 1000 10 0 0 0 0 0 0 2000 20",
        },
    }

    fleet = [
        (f"sw-cisco-{i}", "cisco_iosxe", lambda cmd: responses["cisco_iosxe"].get(cmd, ""))
        for i in range(2)
    ] + [
        (f"sw-arista-{i}", "arista_eos", lambda cmd: responses["arista_eos"].get(cmd, ""))
        for i in range(2)
    ] + [
        (f"sw-huawei-{i}", "huawei_vrp", lambda cmd: responses["huawei_vrp"].get(cmd, ""))
        for i in range(2)
    ] + [
        (f"sw-eltex-{i}", "eltex_mes", lambda cmd: responses["eltex_mes"].get(cmd, ""))
        for i in range(2)
    ] + [
        (f"sw-juniper-{i}", "juniper_junos", lambda cmd: responses["juniper_junos"].get(cmd, ""))
        for i in range(2)
    ] + [
        (f"sw-yadro-{i}", "yadro_kornfe", lambda cmd: responses["yadro_kornfe"].get(cmd, ""))
        for i in range(2)
    ] + [
        (f"srv-linux-{i}", "linux_server", lambda cmd: responses["linux_server"].get(cmd, ""))
        for i in range(2)
    ]

    res = collect_fleet_metrics_parallel(fleet, max_workers=14)
    assert len(res) == 14

    for h, m in res.items():
        assert "cpu_pct" in m, f"Missing cpu_pct in {h}"
        assert "memory_pct" in m, f"Missing memory_pct in {h}"
        assert "uplink_util_pct" in m, f"Missing uplink_util_pct in {h}"
        assert m["cpu_pct"] > 0
        assert m["memory_pct"] > 0
        assert m["uplink_util_pct"] > 0


def test_negative_cases_and_adversarial_inputs():
    """Test 6: Adversarial inputs & schema boundaries."""
    # 1. Extra unknown field rejected by extra='forbid'
    raw_bad_extra = yaml.safe_load(MOCK_CUSTOM_YAML_CONTENT)
    raw_bad_extra["unexpected_injected_root_key"] = "exploit"
    with pytest.raises(ValidationError):
        PlatformProfile.model_validate(raw_bad_extra)

    # 2. Missing default_interface
    raw_missing_iface = yaml.safe_load(MOCK_CUSTOM_YAML_CONTENT)
    del raw_missing_iface["default_interface"]
    with pytest.raises(ValidationError):
        PlatformProfile.model_validate(raw_missing_iface)

    # 3. Non-existent parser specified in telemetry does not crash collection
    raw_custom_unknown_parser = yaml.safe_load(MOCK_CUSTOM_YAML_CONTENT)
    raw_custom_unknown_parser["telemetry"]["cpu_pct"]["parser"] = "non_existent_parser_xyz"
    prof_bad_parser = PlatformProfile.model_validate(raw_custom_unknown_parser)

    # Should gracefully skip unknown parser and collect other valid metrics
    metrics = collect_metrics_from_cli(
        hostname="future-sw.lab",
        platform="custom_future_switch",
        send=lambda cmd: "Processor Pool Total: 1000 Used: 200",
        platform_profile=prof_bad_parser,
    )
    assert "cpu_pct" not in metrics
    assert metrics.get("memory_pct") == 20.0

    # 4. Command execution exception in send() is handled gracefully without crash
    def exploding_send(cmd: str):
        raise ConnectionResetError("SSH socket abruptly terminated")

    metrics_err = collect_metrics_from_cli(
        hostname="future-sw.lab",
        platform="custom_future_switch",
        send=exploding_send,
        platform_profile=prof_bad_parser,
    )
    assert metrics_err == {}
