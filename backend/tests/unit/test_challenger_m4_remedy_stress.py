"""Adversarial stress testing suite for Milestone 4 Iteration 2 (R4 Telemetry Remediation).

Authored by Challenger 1 (Milestone 4 Iteration 2).
Empirically tests:
1. HTTP API Routing & Port Bounds:
   - /api/v1/telemetry/ports via TestClient with valid/invalid platforms,
     port boundaries (1, 65535, -1, 0, 65536, 70000), Containerlab NAT forwarded ports,
     and port override bounds.
   - /api/v1/telemetry/series and /api/v1/telemetry/summary authentication & retrieval.
2. Value Preservation Stress:
   - Extreme rates (0.0, 0.001, 1000.0, 10000.0, 40000.0, 100000.0 Mbps) into
     record_live_metric, _LIVE_METRIC_CACHE, and StateManager.
   - Strict clamping of percentage metrics in [0.0, 100.0].
3. Server Idle 0.0 Boundary:
   - HealthSnapshot with 0.0 readings for disk, network rate, load, cpu, memory
     emits exact 0.0 without falsified non-zero defaults.
4. Scale & Concurrency:
   - 1,000-device concurrent fleet collection and multi-threaded cache/StateManager hammering.
"""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from netops.enums import UserRole
from netops.network.base import HealthSnapshot
from netops.services.state import get_state_manager
from netops.services.telemetry import (
    _LIVE_METRIC_CACHE,
    clear_live_metrics,
    collect_fleet_metrics_parallel,
    record_live_metric,
)
from netops.telemetry import metrics
from netops.telemetry.collector import _emit_server_samples
from tests.conftest import auth


def test_http_telemetry_ports_valid_platforms_and_clab(client: TestClient) -> None:
    """Stress-test /api/v1/telemetry/ports across all 7 platforms and Containerlab NAT."""
    test_cases = [
        # (platform, port, exp_clab, exp_ssh, exp_snmp, exp_gnmi, exp_exp)
        ("cisco_iosxe", 22, False, 22, 161, 9339, 9100),
        ("cisco_iosxe", 2211, True, 2211, 1611, 9339, 9100),
        ("arista_eos", 22, False, 22, 161, 6030, 9100),
        ("arista_eos", 2212, True, 2212, 1612, 6032, 9100),
        ("huawei_vrp", 22, False, 22, 161, 57400, 9100),
        ("huawei_vrp", 2221, True, 2221, 1621, 57400, 9100),
        ("eltex_mes", 22, False, 22, 161, 6030, 9100),
        ("eltex_mes", 2204, True, 2204, 1604, 6034, 9100),
        ("juniper_junos", 22, False, 22, 161, 32767, 9100),
        ("juniper_junos", 2205, True, 2205, 1605, 32767, 9100),
        ("yadro_kornfe", 22, False, 22, 161, 6030, 9100),
        ("yadro_kornfe", 2206, True, 2206, 1606, 6036, 9100),
        ("linux_server", 22, False, 22, 161, 6030, 9100),
        ("linux_server", 2231, True, 2231, 161, 6031, 9100),
    ]

    for plat, port, is_clab, exp_ssh, exp_snmp, exp_gnmi, exp_exp in test_cases:
        url = (
            f"/api/v1/telemetry/ports?platform={plat}&management_port={port}&hostname=node-{plat}.lab"
        )
        resp = client.get(url)
        assert resp.status_code == 200, f"Failed for {plat} port {port}: {resp.text}"
        data = resp.json()
        assert data["platform"] == plat
        assert data["management_port"] == port
        assert data["is_clab_forwarded"] is is_clab
        ports = data["resolved_ports"]
        assert ports["ssh"] == exp_ssh
        assert ports["snmp"] == exp_snmp
        assert ports["exporter"] == exp_exp
        if exp_gnmi is not None:
            assert ports.get("gnmi") == exp_gnmi


def test_http_telemetry_ports_boundary_and_validation_errors(client: TestClient) -> None:
    """Stress-test port bounds (1, 65535, 0, -1, 65536, 70000) and query validation."""
    # Min valid port (1)
    r1 = client.get("/api/v1/telemetry/ports?platform=linux_server&management_port=1")
    assert r1.status_code == 200
    assert r1.json()["resolved_ports"]["ssh"] == 1
    assert r1.json()["is_clab_forwarded"] is False

    # Max valid port (65535)
    r65535 = client.get("/api/v1/telemetry/ports?platform=linux_server&management_port=65535")
    assert r65535.status_code == 200
    assert r65535.json()["resolved_ports"]["ssh"] == 65535
    assert r65535.json()["is_clab_forwarded"] is False

    # Out of bounds management_port: 0, -1, 65536, 70000 -> HTTP 422
    for invalid_port in (0, -1, 65536, 70000):
        resp = client.get(
            f"/api/v1/telemetry/ports?platform=linux_server&management_port={invalid_port}"
        )
        assert resp.status_code == 422, f"Expected 422 for management_port={invalid_port}"

    # Valid overrides
    url_over = (
        "/api/v1/telemetry/ports?platform=linux_server&management_port=22"
        "&gnmi_port=16030&snmp_port=1161&exporter_port=9131"
    )
    r_over = client.get(url_over)
    assert r_over.status_code == 200
    data_over = r_over.json()["resolved_ports"]
    assert data_over["gnmi"] == 16030
    assert data_over["snmp"] == 1161
    assert data_over["exporter"] == 9131

    # Invalid overrides bounds -> HTTP 422
    for query_param in ("gnmi_port=70000", "snmp_port=0", "snmp_port=-5", "exporter_port=65536"):
        resp = client.get(f"/api/v1/telemetry/ports?platform=linux_server&{query_param}")
        assert resp.status_code == 422, f"Expected 422 for query param {query_param}"

    # Unknown platform string gracefully handled without 500
    r_unk = client.get(
        "/api/v1/telemetry/ports?platform=unsupported_brand_switch&management_port=22"
    )
    assert r_unk.status_code == 200
    assert r_unk.json()["resolved_ports"]["ssh"] == 22


def test_http_telemetry_series_endpoints(client: TestClient) -> None:
    """Verify /api/v1/telemetry/series and /summary endpoints with auth and error cases."""
    # 1. Unauthenticated requests -> HTTP 401
    assert client.get("/api/v1/telemetry/summary").status_code == 401
    assert (
        client.get("/api/v1/telemetry/series/test-srv.croc.lab?metric=cpu_pct").status_code
        == 401
    )

    # 2. Authenticated Viewer -> HTTP 200
    viewer_headers = auth(UserRole.VIEWER)

    # Summary
    r_sum = client.get("/api/v1/telemetry/summary", headers=viewer_headers)
    assert r_sum.status_code == 200
    data_sum = r_sum.json()
    assert data_sum["active_metrics_count"] >= 5
    assert "cpu_pct" in data_sum["supported_metrics"]
    assert "disk_util_pct" in data_sum["supported_metrics"]
    assert "network_rate_mbps" in data_sum["supported_metrics"]

    # Valid series request with live recorded metrics
    test_host = "test-srv.croc.lab"
    clear_live_metrics(test_host)
    for v in [25.0, 30.0, 35.0, 40.0, 45.0]:
        record_live_metric(test_host, "cpu_pct", v)

    r_series = client.get(
        f"/api/v1/telemetry/series/{test_host}?metric=cpu_pct&points=5",
        headers=viewer_headers,
    )
    assert r_series.status_code == 200
    data_series = r_series.json()
    assert data_series["hostname"] == test_host
    assert data_series["metric"] == "cpu_pct"
    assert data_series["points"] == [25.0, 30.0, 35.0, 40.0, 45.0]
    assert data_series["unit"] == "%"
    assert data_series["threshold"] == 80.0
    clear_live_metrics(test_host)

    # Unknown metric request -> HTTP 400
    r_bad = client.get(
        f"/api/v1/telemetry/series/{test_host}?metric=nonexistent_fantasy_metric",
        headers=viewer_headers,
    )
    assert r_bad.status_code == 400


def test_extreme_rate_and_load_value_preservation() -> None:
    """Stress-test rate and load metrics across extreme multi-gigabit ranges."""
    hostname = "srv-extreme-rates.croc.lab"
    clear_live_metrics(hostname)
    sm = get_state_manager()

    rates_to_test = [
        0.0,
        0.001,
        0.05,
        1.5,
        100.0,
        500.0,
        1000.0,
        10000.0,  # 10 Gbps
        40000.0,  # 40 Gbps
        100000.0,  # 100 Gbps
    ]

    for rate in rates_to_test:
        clear_live_metrics(hostname)
        record_live_metric(hostname, "network_rate_mbps", rate)
        cached = _LIVE_METRIC_CACHE[hostname]["network_rate_mbps"][-1]
        series = sm.get_telemetry_series(hostname, "network_rate_mbps", points=5)
        expected = round(max(0.0, rate), 2)

        assert cached == expected, f"Rate {rate}: cached={cached} != expected={expected}"
        assert series[-1] == expected, f"Rate {rate}: sm={series[-1]} != expected={expected}"

    # Test load averages up to 128-core system load
    loads_to_test = [0.0, 0.05, 1.0, 8.4, 32.0, 128.5]
    for load in loads_to_test:
        clear_live_metrics(hostname)
        record_live_metric(hostname, "load_1m", load)
        cached = _LIVE_METRIC_CACHE[hostname]["load_1m"][-1]
        series = sm.get_telemetry_series(hostname, "load_1m", points=5)
        expected = round(max(0.0, load), 2)

        assert cached == expected, f"Load {load}: cached={cached} != expected={expected}"
        assert series[-1] == expected, f"Load {load}: sm={series[-1]} != expected={expected}"

    # Test StateManager.set_telemetry_series preservation of rates > 100
    high_rate_series = [150.0, 500.0, 1200.0, 40000.0]
    sm.set_telemetry_series(hostname, "network_rate_mbps", high_rate_series)
    retrieved = sm.get_telemetry_series(hostname, "network_rate_mbps", points=10)
    assert retrieved == high_rate_series, f"Expected {high_rate_series}, got {retrieved}"

    clear_live_metrics(hostname)


def test_percentage_metrics_strict_clamping_boundary() -> None:
    """Verify percentage metrics are strictly clamped to [0.0, 100.0]."""
    hostname = "srv-pct-clamping.croc.lab"
    sm = get_state_manager()

    pct_metrics = [
        "cpu_pct",
        "memory_pct",
        "disk_util_pct",
        "uplink_util_pct",
        "ping_loss_percent",
    ]

    boundary_inputs = [
        (-100.0, 0.0),
        (-0.01, 0.0),
        (0.0, 0.0),
        (25.456, 25.46),
        (99.999, 100.0),
        (100.0, 100.0),
        (100.01, 100.0),
        (150.0, 100.0),
        (10000.0, 100.0),
    ]

    for m in pct_metrics:
        for val, exp in boundary_inputs:
            clear_live_metrics(hostname)
            record_live_metric(hostname, m, val)
            cached = _LIVE_METRIC_CACHE[hostname][m][-1]
            series = sm.get_telemetry_series(hostname, m, points=5)

            assert cached == exp, f"{m} val={val}: cached={cached} != expected={exp}"
            assert series[-1] == exp, f"{m} val={val}: sm={series[-1]} != expected={exp}"

    # Verify StateManager.set_telemetry_series clamps percentages
    sm.set_telemetry_series(hostname, "cpu_pct", [-10.0, 50.0, 150.0])
    res_series = sm.get_telemetry_series(hostname, "cpu_pct", points=5)
    assert res_series == [0.0, 50.0, 100.0]

    clear_live_metrics(hostname)


def test_server_idle_zero_preservation_exhaustive() -> None:
    """Stress-test _emit_server_samples with idle zero readings across all dimensions."""
    hostname = "idle-zero-server.croc.lab"
    clear_live_metrics(hostname)

    # 1. Authentic completely idle server
    idle_snap = HealthSnapshot(
        cpu_pct=0.0,
        memory_pct=0.0,
        disk_util_pct=0.0,
        network_rate_mbps=0.0,
        load_1m=0.0,
        uplink_util_pct=0.0,
    )
    samples = _emit_server_samples(idle_snap, {"hostname": hostname}, hostname)
    sample_map = {s.name: s.value for s in samples}

    assert sample_map[metrics.SERVER_CPU_UTIL] == 0.0
    assert sample_map[metrics.SERVER_MEMORY_UTIL] == 0.0
    assert sample_map[metrics.SERVER_DISK_UTIL] == 0.0
    assert sample_map[metrics.SERVER_NETWORK_RATE] == 0.0
    assert sample_map[metrics.SERVER_LOAD_1M] == 0.0

    # Also verify live metric cache recorded 0.0
    assert _LIVE_METRIC_CACHE[hostname]["cpu_pct"][-1] == 0.0
    assert _LIVE_METRIC_CACHE[hostname]["memory_pct"][-1] == 0.0
    assert _LIVE_METRIC_CACHE[hostname]["disk_util_pct"][-1] == 0.0
    assert _LIVE_METRIC_CACHE[hostname]["network_rate_mbps"][-1] == 0.0
    assert _LIVE_METRIC_CACHE[hostname]["load_1m"][-1] == 0.0

    # 2. Missing network_rate_mbps is NOT derived from uplink_util_pct (no invented values)
    snap_uplink_zero = HealthSnapshot(
        cpu_pct=0.0,
        memory_pct=0.0,
        disk_util_pct=0.0,
        network_rate_mbps=None,
        uplink_util_pct=0.0,
        load_1m=0.0,
    )
    samples2 = _emit_server_samples(snap_uplink_zero, {"hostname": hostname}, hostname)
    map2 = {s.name: s.value for s in samples2}
    assert metrics.SERVER_NETWORK_RATE not in map2
    assert map2[metrics.SERVER_LOAD_1M] == 0.0

    # 3. Completely empty snapshot emits nothing instead of fake defaults
    snap_none = HealthSnapshot(
        cpu_pct=None,
        memory_pct=None,
        disk_util_pct=None,
        network_rate_mbps=None,
        uplink_util_pct=None,
        load_1m=None,
    )
    assert _emit_server_samples(snap_none, {"hostname": hostname}, hostname) == []

    clear_live_metrics(hostname)


def test_fleet_concurrent_scale_1000_targets_and_thread_stress() -> None:
    """Concurrently poll 1,000 targets and hammer StateManager with 64 threads."""
    platforms = [
        "cisco_iosxe",
        "arista_eos",
        "huawei_vrp",
        "eltex_mes",
        "juniper_junos",
        "yadro_kornfe",
        "linux_server",
    ]

    def _simulated_send(_cmd: str) -> str:
        # Simulate CLI output with random realistic variance
        r = random.random()
        if r < 0.05:
            raise ConnectionError("Simulated peer disconnect")
        if r < 0.10:
            return ""
        return (
            "CPU: 18% usr 4% sys 78% idle\n"
            "MemTotal: 16000000 kB\n"
            "MemFree: 8000000 kB\n"
            "Et1 15.0%\n"
            "/dev/sda1 45%\n"
            "load average: 1.15, 0.85, 0.60\n"
        )

    targets = [
        (
            f"scale-target-{i:04d}.fabric-{i%10}.croc.enterprise",
            platforms[i % len(platforms)],
            _simulated_send,
        )
        for i in range(1000)
    ]

    t0 = time.perf_counter()
    results = collect_fleet_metrics_parallel(targets, max_workers=64)
    elapsed = time.perf_counter() - t0

    assert len(results) == 1000
    assert elapsed < 5.0, f"Concurrent polling took {elapsed:.2f}s, expected < 5.0s"

    # Multi-threaded concurrent write and read hammering
    errors: list[str] = []

    def _hammer_worker(worker_id: int) -> None:
        try:
            h = f"hammer-isolated-node-{worker_id}.fabric-1"
            clear_live_metrics(h)
            sm = get_state_manager()
            for step in range(50):
                rate_val = 500.0 + (step * 10)
                record_live_metric(h, "network_rate_mbps", rate_val)
                record_live_metric(h, "cpu_pct", float(step % 100))
                s_rate = sm.get_telemetry_series(h, "network_rate_mbps", points=5)
                if not s_rate or s_rate[-1] < 500.0:
                    errors.append(f"Worker {worker_id} step {step}: rate was truncated {s_rate}")
            clear_live_metrics(h)
        except Exception as exc:
            errors.append(f"Worker {worker_id} crashed: {type(exc).__name__}: {exc}")

    with ThreadPoolExecutor(max_workers=64) as pool:
        list(pool.map(_hammer_worker, range(64)))

    assert len(errors) == 0, f"Concurrency hammering produced errors: {errors[:5]}"

    for h, _, _ in targets[:50]:
        clear_live_metrics(h)
