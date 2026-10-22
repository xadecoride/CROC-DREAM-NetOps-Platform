"""Comprehensive unit and scale tests for multi-fabric telemetry, port resolution,

heterogeneous metric collection (switches + servers), and NOC Grafana alignment.
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from netops.api.app import create_app
from netops.db import utcnow
from netops.enums import Platform
from netops.services.state import get_state_manager, reset_state_manager
from netops.services.telemetry import (
    PARSER_REGISTRY,
    clear_live_metrics,
    collect_fleet_metrics_parallel,
    collect_metrics_from_cli,
    parse_linux_disk,
    parse_linux_load,
    parse_linux_network_rate,
    record_live_metric,
    resolve_device_ports,
)
from netops.settings import Settings
from netops.telemetry import collector, metrics
from netops.telemetry.metrics import Sample


def test_port_resolution_matrix() -> None:
    """Test port resolution across direct IP, forwarded Containerlab NAT, and exporter ports."""
    # 1. Direct IP Network Switches
    cisco_direct = resolve_device_ports("172.20.20.5", 22, Platform.CISCO_IOSXE)
    assert cisco_direct["ssh"] == 22
    assert cisco_direct["snmp"] == 161
    assert cisco_direct["gnmi"] == 9339
    assert cisco_direct["exporter"] == 9100

    arista_direct = resolve_device_ports("172.20.20.4", 22, Platform.ARISTA_EOS)
    assert arista_direct["ssh"] == 22
    assert arista_direct["snmp"] == 161
    assert arista_direct["gnmi"] == 6030
    assert arista_direct["exporter"] == 9100

    huawei_direct = resolve_device_ports("172.20.20.2", 22, Platform.HUAWEI_VRP)
    assert huawei_direct["ssh"] == 22
    assert huawei_direct["snmp"] == 161
    assert huawei_direct["gnmi"] == 57400

    juniper_direct = resolve_device_ports("172.20.20.8", 22, Platform.JUNIPER_JUNOS)
    assert juniper_direct["ssh"] == 22
    assert juniper_direct["snmp"] == 161
    assert juniper_direct["gnmi"] == 32767

    # 2. Direct IP Linux / Application Server
    server_direct = resolve_device_ports("172.20.20.10", 22, Platform.LINUX_SERVER)
    assert server_direct["ssh"] == 22
    assert server_direct["snmp"] == 161
    assert server_direct["exporter"] == 9100

    # 3. Containerlab NAT Forwarded Switches (22xx -> 16xx SNMP, 603x gNMI)
    cisco_clab = resolve_device_ports("127.0.0.1", 2211, Platform.CISCO_IOSXE)
    assert cisco_clab["ssh"] == 2211
    assert cisco_clab["snmp"] == 1611  # 2211 - 600

    arista_clab = resolve_device_ports("127.0.0.1", 2212, Platform.ARISTA_EOS)
    assert arista_clab["ssh"] == 2212
    assert arista_clab["snmp"] == 1612  # 2212 - 600
    assert arista_clab["gnmi"] == 6032  # 6030 + (2212 % 10)

    leaf1_clab = resolve_device_ports("127.0.0.1", 2221, Platform.HUAWEI_VRP)
    assert leaf1_clab["ssh"] == 2221
    assert leaf1_clab["snmp"] == 1621  # 2221 - 600

    leaf2_clab = resolve_device_ports("127.0.0.1", 2222, Platform.HUAWEI_VRP)
    assert leaf2_clab["ssh"] == 2222
    assert leaf2_clab["snmp"] == 1622  # 2222 - 600

    # 4. Containerlab NAT Linux Host (2231)
    host_clab = resolve_device_ports("127.0.0.1", 2231, Platform.LINUX_SERVER)
    assert host_clab["ssh"] == 2231
    assert host_clab["snmp"] == 161  # linux_server has clab_offset 0
    assert host_clab["exporter"] == 9100

    # 5. Explicit port overrides
    custom = resolve_device_ports(
        "10.0.0.50",
        22,
        Platform.LINUX_SERVER,
        exporter_port=9131,
        snmp_port=1161,
        gnmi_port=16030,
    )
    assert custom["exporter"] == 9131
    assert custom["snmp"] == 1161
    assert custom["gnmi"] == 16030


def test_server_metric_parsers() -> None:
    """Test Linux server metric parsers for disk, load average, and network rates."""
    df_output = """Filesystem      Size  Used Avail Use% Mounted on
udev            7.8G     0  7.8G   0% /dev
tmpfs           1.6G  2.1M  1.6G   1% /run
/dev/sda1        98G   44G   50G  47% /
tmpfs           7.8G     0  7.8G   0% /dev/shm
"""
    assert parse_linux_disk(df_output) == 47.0
    assert parse_linux_disk("no percentage here") is None

    uptime_output = " 14:23:10 up 18 days,  4:12,  2 users,  load average: 1.45, 0.98, 0.65"
    assert parse_linux_load(uptime_output) == 1.45

    loadavg_output = "0.72 0.55 0.40 1/320 12845"
    assert parse_linux_load(loadavg_output) == 0.72

    net_dev_output = (
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast"
        "|bytes    packets errs drop fifo colls carrier compressed\n"
        "    lo: 12500000    1500    0    0    0     0          0         0 "
        "12500000    1500    0    0    0     0       0          0\n"
        "  eth0: 62500000    8500    0    0    0     0          0         0 "
        "62500000    8500    0    0    0     0       0          0\n"
    )
    # 150,000,000 total bytes * 8 / 1,000,000 = 1200.0 Mbps
    rate = parse_linux_network_rate(net_dev_output)
    assert rate is not None
    assert rate == 1200.0

    assert "parse_linux_disk" in PARSER_REGISTRY
    assert "parse_linux_load" in PARSER_REGISTRY
    assert "parse_linux_network_rate" in PARSER_REGISTRY


def test_heterogeneous_metric_collection_switches_and_servers() -> None:
    """Test metrics collection from switch and server targets into live cache and StateManager."""
    hostname_sw = "spine-test.croc.lab"
    hostname_srv = "app-srv-test.croc.lab"
    clear_live_metrics(hostname_sw)
    clear_live_metrics(hostname_srv)

    # Collect switch metrics via mock CLI send
    def send_switch(cmd: str) -> str:
        if "Mem" in cmd:
            return "Total memory: 16000000 kB\nFree memory: 8000000 kB"
        if "Cpu" in cmd or "top" in cmd:
            return "%Cpu(s): 14.5 us, 3.2 sy, 0.0 ni, 82.3 id"
        if "rates" in cmd:
            return "Et1  10.0%  12.5%"
        return ""

    sw_res = collect_metrics_from_cli(hostname_sw, "arista_eos", send_switch)
    assert sw_res.get("cpu_pct") == 17.7
    assert sw_res.get("memory_pct") == 50.0
    assert sw_res.get("uplink_util_pct") == 12.5

    # Collect server metrics via mock CLI send
    def send_server(cmd: str) -> str:
        return (
            "%Cpu(s): 12.0 us, 4.0 sy, 0.0 ni, 84.0 id\n"
            "MiB Mem : 16000.0 total, 8000.0 free, 8000.0 used\n"
            "MemTotal: 16384000 kB\n"
            "MemAvailable: 8192000 kB"
        )

    srv_res = collect_metrics_from_cli(hostname_srv, "linux_server", send_server)
    assert srv_res.get("cpu_pct") == 16.0
    assert srv_res.get("memory_pct") == 50.0

    # Test record_live_metric persistence
    record_live_metric(hostname_srv, "disk_util_pct", 35.0)
    record_live_metric(hostname_srv, "network_rate_mbps", 150.0)
    record_live_metric(hostname_srv, "load_1m", 1.8)

    sm = get_state_manager()
    assert sm.get_telemetry_series(hostname_srv, "disk_util_pct", points=5) == [35.0]
    assert sm.get_telemetry_series(hostname_srv, "cpu_pct", points=5) == [16.0]

    clear_live_metrics(hostname_sw)
    clear_live_metrics(hostname_srv)


def test_concurrent_telemetry_polling_multi_fabric() -> None:
    """Scale test: Concurrent telemetry collection across heterogeneous targets in multi-fabric."""
    targets: list[tuple[str, str, object]] = []
    platforms = [
        "cisco_iosxe",
        "arista_eos",
        "huawei_vrp",
        "eltex_mes",
        "juniper_junos",
        "yadro_kornfe",
        "linux_server",
    ]

    def _send_linux(_c: str) -> str:
        return "CPU: 10% usr 5% sys 85% idle\nMemTotal: 8000000 kB\nMemFree: 4000000 kB"

    def _send_arista(_c: str) -> str:
        return "%Cpu(s): 15.0 us, 5.0 sy, 0.0 ni, 80.0 id\nEt1 25.0%"

    def _send_huawei(_c: str) -> str:
        return "cpu0 22%\nMemory Using Percentage Is: 40%"

    def _send_default(_c: str) -> str:
        return "one minute: 18%\nProcessor Pool Total: 1000000 Used: 400000"

    for i in range(1000):
        fabric_idx = i % 10
        plat = platforms[i % len(platforms)]
        hostname = f"node-{i:04d}.fabric-{fabric_idx}.croc.enterprise"
        if plat == "linux_server":
            mock_send = _send_linux
        elif plat == "arista_eos":
            mock_send = _send_arista
        elif plat == "huawei_vrp":
            mock_send = _send_huawei
        else:
            mock_send = _send_default
        targets.append((hostname, plat, mock_send))

    start_t = time.perf_counter()
    results = collect_fleet_metrics_parallel(targets, max_workers=32)
    duration = time.perf_counter() - start_t

    assert len(results) == 1000
    # Must complete 1,000 devices concurrently in well under 5 seconds
    assert duration < 5.0, f"1000 devices polled in {duration:.2f}s, expected < 5s"

    # Validate metric readings for heterogeneous samples
    for i in (0, 100, 250, 500, 750, 999):
        h, plat, _ = targets[i]
        dev_res = results[h]
        assert "cpu_pct" in dev_res
        assert dev_res["cpu_pct"] > 0.0
        clear_live_metrics(h)


def test_prometheus_metrics_rendering_switches_and_servers() -> None:
    """Verify /metrics renderer outputs correct Prometheus exposition for switches and servers."""
    now = utcnow()
    samples = [
        # Switch metrics
        Sample(
            metrics.DEVICE_REACHABLE, 1.0, {"hostname": "spine-1.fabric-1", "fabric_id": "fabric-1"}
        ),
        Sample(
            metrics.DEVICE_CPU_UTIL, 18.5, {"hostname": "spine-1.fabric-1", "fabric_id": "fabric-1"}
        ),
        Sample(
            metrics.DEVICE_MEMORY_UTIL,
            42.0,
            {"hostname": "spine-1.fabric-1", "fabric_id": "fabric-1"},
        ),
        Sample(
            metrics.DEVICE_UPLINK_UTIL,
            25.0,
            {"hostname": "spine-1.fabric-1", "fabric_id": "fabric-1"},
        ),
        Sample(
            metrics.BGP_SESSION_UP,
            1.0,
            {"hostname": "spine-1.fabric-1", "fabric_id": "fabric-1", "peer": "10.0.0.2"},
        ),
        # Stand Server metrics
        Sample(
            metrics.DEVICE_REACHABLE,
            1.0,
            {"hostname": "server-1.fabric-1", "fabric_id": "fabric-1"},
        ),
        Sample(
            metrics.SERVER_CPU_UTIL,
            28.4,
            {"hostname": "server-1.fabric-1", "fabric_id": "fabric-1"},
        ),
        Sample(
            metrics.SERVER_MEMORY_UTIL,
            64.2,
            {"hostname": "server-1.fabric-1", "fabric_id": "fabric-1"},
        ),
        Sample(
            metrics.SERVER_DISK_UTIL,
            52.0,
            {"hostname": "server-1.fabric-1", "fabric_id": "fabric-1"},
        ),
        Sample(
            metrics.SERVER_NETWORK_RATE,
            250.0,
            {"hostname": "server-1.fabric-1", "fabric_id": "fabric-1"},
        ),
        Sample(
            metrics.SERVER_LOAD_1M,
            2.15,
            {"hostname": "server-1.fabric-1", "fabric_id": "fabric-1"},
        ),
        # Fleet summary
        Sample(metrics.DEVICES_TOTAL, 2.0, {"status": "IN_SYNC"}),
        Sample(metrics.DRIFT_DEVICES_TOTAL, 0.0, {}),
    ]

    rendered = metrics.render(samples, now)

    # Check Prometheus HELP and TYPE headers
    assert "# HELP netops_device_cpu_utilization_percent" in rendered
    assert "# TYPE netops_device_cpu_utilization_percent gauge" in rendered
    assert "# HELP netops_server_cpu_utilization_percent" in rendered
    assert "# TYPE netops_server_cpu_utilization_percent gauge" in rendered
    assert "# HELP netops_server_disk_utilization_percent" in rendered
    assert "# TYPE netops_server_disk_utilization_percent gauge" in rendered

    # Check line content
    exp_srv_cpu = (
        "netops_server_cpu_utilization_percent{"
        'fabric_id="fabric-1",hostname="server-1.fabric-1"} 28.4'
    )
    assert exp_srv_cpu in rendered

    exp_srv_disk = (
        "netops_server_disk_utilization_percent{"
        'fabric_id="fabric-1",hostname="server-1.fabric-1"} 52'
    )
    assert exp_srv_disk in rendered

    exp_srv_net = (
        'netops_server_network_rate_mbps{fabric_id="fabric-1",hostname="server-1.fabric-1"} 250'
    )
    assert exp_srv_net in rendered

    exp_srv_load = 'netops_server_load_1m{fabric_id="fabric-1",hostname="server-1.fabric-1"} 2.15'
    assert exp_srv_load in rendered

    exp_dev_cpu = (
        "netops_device_cpu_utilization_percent{"
        'fabric_id="fabric-1",hostname="spine-1.fabric-1"} 18.5'
    )
    assert exp_dev_cpu in rendered


def test_redis_ring_buffer_and_latest_snapshot_at_scale() -> None:
    """Verify Redis hot state series ring buffer and the latest-snapshot store at 1,000 samples."""
    reset_state_manager()
    sm = get_state_manager()

    # 1. Ring buffer capping
    hostname = "test-host-scale.lab"
    for v in range(350):
        sm.append_telemetry(hostname, "cpu_pct", float(v % 100), max_points=288)

    series = sm.get_telemetry_series(hostname, "cpu_pct", points=500)
    assert len(series) == 288
    clear_live_metrics(hostname)

    # 2. Latest snapshot across stands (history lives in VictoriaMetrics)
    bulk_samples: list[Sample] = []
    for s_idx in range(500):
        fab = f"stand-{s_idx % 20}"
        h_sw = f"switch-{s_idx}.{fab}"
        h_srv = f"server-{s_idx}.{fab}"
        bulk_samples.append(
            Sample(metrics.DEVICE_REACHABLE, 1.0, {"hostname": h_sw, "fabric_id": fab})
        )
        bulk_samples.append(
            Sample(
                metrics.SERVER_CPU_UTIL, 15.0 + (s_idx % 20), {"hostname": h_srv, "fabric_id": fab}
            )
        )

    saved_time = collector.publish(bulk_samples, ttl_seconds=180)

    latest_samples, latest_at = collector.latest()
    assert latest_at == saved_time
    assert latest_samples == bulk_samples


def test_grafana_noc_dashboard_schema_and_multifabric_panels() -> None:
    """Verify Grafana NOC dashboard YAML structure, fabric_id templating, and server panels."""
    import yaml

    repo_root = Path(__file__).resolve().parents[3]
    dash_path = repo_root / "monitoring/grafana/dashboards/croc-dream-noc.yaml"
    assert dash_path.is_file()

    dash = yaml.safe_load(dash_path.read_text(encoding="utf-8"))
    assert dash["uid"] == "croc-dream-noc"

    # Templating variables
    var_names = [v["name"] for v in dash["templating"]["list"]]
    assert "fabric_id" in var_names
    assert "hostname" in var_names

    # Panels
    panel_titles = [p["title"] for p in dash["panels"]]
    assert any("Server CPU" in t for t in panel_titles)
    assert any("Server Memory" in t or "Disk" in t for t in panel_titles)
    assert any("Switch CPU" in t for t in panel_titles)
    assert len(dash["panels"]) >= 10


def test_telemetry_api_ports_endpoint(session: Session) -> None:
    """Test /api/v1/telemetry/ports endpoint via FastAPI TestClient."""
    app = create_app(Settings(database_url="sqlite:///:memory:", network_driver="offline"))
    client = TestClient(app)

    resp = client.get(
        "/api/v1/telemetry/ports?platform=arista_eos&management_port=2212&hostname=spine-2.croc.lab"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_clab_forwarded"] is True
    assert data["resolved_ports"]["ssh"] == 2212
    assert data["resolved_ports"]["snmp"] == 1612
    assert data["resolved_ports"]["gnmi"] == 6032

    resp_srv = client.get(
        "/api/v1/telemetry/ports?platform=linux_server&management_port=22&hostname=srv.lab"
    )
    assert resp_srv.status_code == 200
    data_srv = resp_srv.json()
    assert data_srv["is_clab_forwarded"] is False
    assert data_srv["resolved_ports"]["ssh"] == 22
    assert data_srv["resolved_ports"]["snmp"] == 161
    assert data_srv["resolved_ports"]["exporter"] == 9100


def test_huawei_bgp_peer_prefrcv_not_remote_as() -> None:
    """Регрессия: PrefRcv берётся из последней колонки, а не из номера AS."""
    from netops.network.table_parsers import parse_bgp_table

    out = (
        "  Peer            V          AS  MsgRcvd  MsgSent  OutQ  Up/Down       State  PrefRcv\n"
        "  10.10.0.0       4       65000       38       43     0 00:28:48 Established        2\n"
        "  10.10.0.4       4       65000       40       42     0 00:28:48 Active\n"
    )
    res = parse_bgp_table(Platform.HUAWEI_VRP, out)
    assert res["10.10.0.0"].prefixes_accepted == 2
    assert res["10.10.0.4"].prefixes_accepted == 0
