"""Adversarial stress-testing suite for Milestone 4 (R4) Telemetry & Port Resolution.

Authored by Challenger 1 (Milestone 4 / R4).
Empirically stress-tests port resolution across 7 platforms and Containerlab NAT,
evaluates 1,000-device concurrent telemetry polling under chaotic failures,
verifies thread-safety of profile cache and StateManager ring-buffers,
and documents empirical boundary behaviors and edge cases.
"""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from fastapi.testclient import TestClient

from netops.api.app import create_app
from netops.enums import Platform
from netops.services.state import get_state_manager
from netops.services.telemetry import (
    PARSER_REGISTRY,
    clear_live_metrics,
    clear_platform_profile_cache,
    collect_fleet_metrics_parallel,
    load_profile_for_platform,
    record_live_metric,
    register_parser,
    resolve_device_ports,
)
from netops.settings import Settings
from netops.telemetry.collector import _emit_server_samples


def test_unmounted_telemetry_router_in_app() -> None:
    """Verify telemetry router is mounted in api_v1 in routes/__init__.py.

    HTTP requests to /api/v1/telemetry/ports return 200 OK.
    """
    app = create_app(Settings(database_url="sqlite:///:memory:", network_driver="offline"))
    client = TestClient(app)

    resp = client.get("/api/v1/telemetry/ports?platform=arista_eos&management_port=22")
    assert resp.status_code == 200, f"Expected 200 for mounted route, got {resp.status_code}"


def test_network_rate_mbps_clamping_behavior() -> None:
    """Verify live telemetry cache and StateManager do not clamp network rate to 100.0.

    When recording network_rate_mbps (unit Mbps, threshold 1000.0), rates > 100 Mbps
    are preserved accurately.
    """
    hostname = "srv-test-rate-adversarial.croc.lab"
    clear_live_metrics(hostname)

    record_live_metric(hostname, "network_rate_mbps", 500.0)
    sm = get_state_manager()
    series = sm.get_telemetry_series(hostname, "network_rate_mbps", points=5)

    assert series == [500.0], f"Expected preserved [500.0], got {series}"
    clear_live_metrics(hostname)


def test_zero_metric_falsification_in_server_samples() -> None:
    """Verify _emit_server_samples preserves real 0.0 values without falsification."""
    health_zero = SimpleNamespace(
        cpu_pct=0.0,
        memory_pct=0.0,
        disk_util_pct=0.0,
        network_rate_mbps=0.0,
        load_1m=0.0,
        uplink_util_pct=0.0,
    )

    samples = _emit_server_samples(health_zero, {"hostname": "srv-zero"}, "srv-zero")
    sample_dict = {s.name: s.value for s in samples}

    # cpu and mem correctly preserve 0.0
    assert sample_dict["netops_server_cpu_utilization_percent"] == 0.0
    assert sample_dict["netops_server_memory_utilization_percent"] == 0.0

    # disk, net_rate, and load_1m preserve real 0.0 readings
    assert sample_dict["netops_server_disk_utilization_percent"] == 0.0
    assert sample_dict["netops_server_network_rate_mbps"] == 0.0
    assert sample_dict["netops_server_load_1m"] == 0.0


def test_port_resolution_across_all_7_platforms_direct_and_clab() -> None:
    """Empirically verify port resolution across all 7 platforms for direct IP and Clab NAT."""
    test_matrix = [
        # (platform, direct_snmp, direct_gnmi, clab_port, exp_clab_snmp, exp_clab_gnmi)
        (Platform.CISCO_IOSXE, 161, 9339, 2201, 1601, 9339),
        (Platform.ARISTA_EOS, 161, 6030, 2202, 1602, 6032),
        (Platform.HUAWEI_VRP, 161, 57400, 2203, 1603, 57400),
        (Platform.ELTEX_MES, 161, 6030, 2204, 1604, 6034),
        (Platform.JUNIPER_JUNOS, 161, 32767, 2205, 1605, 32767),
        (Platform.YADRO_KORNFE, 161, 6030, 2206, 1606, 6036),
        (Platform.LINUX_SERVER, 161, 6030, 2207, 161, 6037),
    ]

    for plat, d_snmp, d_gnmi, c_port, exp_c_snmp, exp_c_gnmi in test_matrix:
        # Direct IP
        direct = resolve_device_ports("10.0.0.1", 22, plat)
        assert direct["ssh"] == 22
        assert direct["snmp"] == d_snmp
        assert direct["gnmi"] == d_gnmi
        assert direct["exporter"] == 9100

        # Containerlab NAT
        clab = resolve_device_ports("127.0.0.1", c_port, plat)
        assert clab["ssh"] == c_port
        assert clab["snmp"] == exp_c_snmp
        assert clab["gnmi"] == exp_c_gnmi
        assert clab["exporter"] == 9100


def test_port_resolution_boundary_conditions() -> None:
    """Empirically test TCP port boundaries: 1, 65535, 0, negative, None, clab edges."""
    # Min valid TCP port
    p1 = resolve_device_ports("10.0.0.1", 1, Platform.ARISTA_EOS)
    assert p1["ssh"] == 1
    assert p1["snmp"] == 161

    # Max valid TCP port
    p65535 = resolve_device_ports("10.0.0.1", 65535, Platform.ARISTA_EOS)
    assert p65535["ssh"] == 65535
    assert p65535["snmp"] == 161

    # 0, negative, and None gracefully fallback to default SSH port 22
    for invalid_port in (0, -1, -22, None):
        res = resolve_device_ports("10.0.0.1", invalid_port, Platform.ARISTA_EOS)
        assert res["ssh"] == 22
        assert res["snmp"] == 161

    # Containerlab range boundaries: 2000 is clab, 1999 is direct; 2999 is clab, 3000 is direct
    clab_low = resolve_device_ports("127.0.0.1", 2000, Platform.ARISTA_EOS)
    assert clab_low["snmp"] == 1400  # 2000 - 600

    direct_below = resolve_device_ports("127.0.0.1", 1999, Platform.ARISTA_EOS)
    assert direct_below["snmp"] == 161  # not in clab range

    clab_high = resolve_device_ports("127.0.0.1", 2999, Platform.ARISTA_EOS)
    assert clab_high["snmp"] == 2399  # 2999 - 600

    direct_above = resolve_device_ports("127.0.0.1", 3000, Platform.ARISTA_EOS)
    assert direct_above["snmp"] == 161  # not in clab range


def test_concurrent_scale_1000_targets_chaotic_telemetry() -> None:
    """Stress test: 1,000 targets polled concurrently under simulated failures."""
    platforms = [
        "cisco_iosxe",
        "arista_eos",
        "huawei_vrp",
        "eltex_mes",
        "juniper_junos",
        "yadro_kornfe",
        "linux_server",
    ]

    def _chaotic_send(_cmd: str) -> str:
        r = random.random()
        if r < 0.1:
            raise ConnectionResetError("SSH connection reset by peer")
        if r < 0.2:
            raise TimeoutError("Command execution timed out")
        if r < 0.3:
            return "% Syntax error: unrecognized command"
        if r < 0.4:
            return ""
        if r < 0.5:
            return "\x00\xff\xfe corrupted binary stream"
        return (
            "CPU: 15% usr 5% sys 80% idle\n"
            "MemTotal: 8000000 kB\n"
            "MemFree: 4000000 kB\n"
            "Et1 12.0%"
        )

    targets = [
        (
            f"chaos-node-{i:04d}.fabric-{i%10}.enterprise",
            platforms[i % len(platforms)],
            _chaotic_send,
        )
        for i in range(1000)
    ]

    start_t = time.perf_counter()
    results = collect_fleet_metrics_parallel(targets, max_workers=64)
    duration = time.perf_counter() - start_t

    assert len(results) == 1000
    assert duration < 5.0, f"1000 targets took {duration:.2f}s, expected < 5.0s"

    # Cleanup
    for h, _, _ in targets[:50]:
        clear_live_metrics(h)


def test_thread_safety_cache_and_state_manager() -> None:
    """Stress test: 50 concurrent threads hammering profile cache, registry, and StateManager."""
    platforms = [
        "cisco_iosxe",
        "arista_eos",
        "huawei_vrp",
        "eltex_mes",
        "juniper_junos",
        "yadro_kornfe",
        "linux_server",
    ]
    errors: list[str] = []

    def _worker(thread_id: int) -> None:
        try:
            # 1. Profile cache concurrency & clearing
            for _ in range(30):
                p = platforms[thread_id % len(platforms)]
                prof = load_profile_for_platform(p)
                if prof is None:
                    errors.append(f"Thread {thread_id}: profile for {p} was None")
                if thread_id % 10 == 0:
                    clear_platform_profile_cache()

            # 2. Parser registry concurrency
            for _ in range(30):
                name = f"custom_parser_{thread_id}"
                register_parser(name, lambda x: 42.0)
                fn = PARSER_REGISTRY.get(name)
                if fn is None or fn("test") != 42.0:
                    errors.append(f"Thread {thread_id}: parser registry corruption")

            # 3. Live metric recording and ring buffer capping
            sm = get_state_manager()
            h = f"thread-safety-node-{thread_id}.fabric-1"
            for v in range(40):
                record_live_metric(h, "cpu_pct", float(v % 100))
            series = sm.get_telemetry_series(h, "cpu_pct", points=40)
            if len(series) != 40:
                errors.append(f"Thread {thread_id}: series len {len(series)} != 40")
            clear_live_metrics(h)
        except Exception as exc:
            errors.append(f"Thread {thread_id} crashed: {type(exc).__name__}: {exc}")

    with ThreadPoolExecutor(max_workers=50) as pool:
        list(pool.map(_worker, range(50)))

    assert len(errors) == 0, f"Encountered concurrency errors: {errors[:5]}"
