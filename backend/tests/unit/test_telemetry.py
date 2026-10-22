from __future__ import annotations

from netops.services.state import get_state_manager, reset_state_manager
from netops.telemetry import collector, metrics
from netops.telemetry.metrics import Sample


def test_sharded_telemetry_publish_and_lookup() -> None:
    reset_state_manager()
    sm = get_state_manager()
    samples = [
        Sample(
            metrics.DEVICE_CPU_UTIL,
            19.5,
            {"hostname": "spine-1.croc.lab", "fabric_id": "pod-1"},
        ),
        Sample(
            metrics.DEVICE_MEMORY_UTIL,
            44.0,
            {"hostname": "spine-1.croc.lab", "fabric_id": "pod-1"},
        ),
        Sample(
            metrics.SERVER_CPU_UTIL,
            31.0,
            {"hostname": "srv-1.croc.lab", "fabric_id": "pod-2"},
        ),
    ]
    ts = collector.publish(samples, ttl_seconds=120)

    all_samples, all_ts = collector.latest()
    assert all_ts == ts
    assert len(all_samples) == 3

    host_samples, host_ts = collector.latest_for_host("spine-1.croc.lab")
    assert host_ts == ts
    assert len(host_samples) == 2
    assert {s.name for s in host_samples} == {metrics.DEVICE_CPU_UTIL, metrics.DEVICE_MEMORY_UTIL}

    raw_shard = sm.get_json("netops:telemetry:host:spine-1.croc.lab")
    assert isinstance(raw_shard, dict)
    assert len(raw_shard["samples"]) == 2

    fab_samples, fab_ts = collector.latest_for_fabric("pod-2")
    assert fab_ts == ts
    assert len(fab_samples) == 1
    assert fab_samples[0].labels["hostname"] == "srv-1.croc.lab"

    sm.clear_telemetry("spine-1.croc.lab")
    assert collector.latest_for_host("spine-1.croc.lab") == ([], None)


def test_extended_l2_and_server_telemetry_parsers() -> None:
    from netops.services.telemetry_parsers import (
        max_cisco_link_utilization,
        max_percent_column,
        parse_cisco_cpu,
        parse_fdb_mac_count,
        parse_interface_errors,
        parse_linux_conntrack,
        parse_linux_tcp_retransmits,
    )

    assert parse_interface_errors("5 input errors, 2 CRC, 1 output errors") == 8.0
    assert parse_fdb_mac_count("Total Mac Addresses for this criterion: 128") == 128.0
    assert parse_linux_tcp_retransmits("TCP: inuse 12 orphan 0 tw 4 alloc 15 mem 2 retrans: 0/19") == 19.0
    assert parse_linux_conntrack("13107\n65536\n") == 20.0
    assert parse_cisco_cpu("CPU utilization for five seconds: 0%/0%; one minute: 1%; five minutes: 2%") == 1.0
    assert parse_cisco_cpu("CPU utilization for five seconds: 0%/0%; one minute: 0%; five minutes: 0%") == 1.0
    assert max_cisco_link_utilization("5 minute input rate 6400 bits/sec, 5 packets/sec") == 0.64
    assert max_percent_column("Et1  connected  6.5 kbps  0.0%  6.5 kbps  0.0%") == 0.65
    assert max_percent_column("GE1/0/1  up  up  0%  0%  0  0\nGE1/0/2  up  up  0%  0%  0  0") == 0.3




def test_stand_inventory_overlay_preserves_live_endpoints(tmp_path) -> None:
    from netops.intent.repository import IntentRepository

    (tmp_path / "inventory.yaml").write_text(
        "devices:\n"
        "  - hostname: spine-1.croc.lab\n"
        "    management_ip: 172.20.20.2\n"
        "    management_port: 22\n"
        "    platform: cisco_iosxe\n"
        "    role: spine\n"
        "    auth_profile: default\n"
        "    rack: R1\n"
        "    unit: 42\n",
        encoding="utf-8",
    )
    stand_dir = tmp_path / "stands" / "croc-dc1-pod1"
    (stand_dir / "devices").mkdir(parents=True)
    (stand_dir / "inventory.yaml").write_text(
        "devices:\n"
        "  - hostname: spine-1.croc.lab\n"
        "    management_ip: 5.228.243.54\n"
        "    management_port: 2211\n"
        "    platform: cisco_iosxe\n"
        "    role: spine\n"
        "    auth_profile: spine1_auth\n"
        "    fabric_id: croc-dc1-pod1\n"
        "    site: croc-dc1-pod1\n"
        "  - hostname: srv-rocky-1.croc.lab\n"
        "    management_ip: 5.228.243.54\n"
        "    management_port: 221\n"
        "    platform: linux_server\n"
        "    role: server\n"
        "    auth_profile: srvrocky1_auth\n"
        "    fabric_id: croc-dc1-pod1\n"
        "    site: croc-dc1-pod1\n",
        encoding="utf-8",
    )

    repo = IntentRepository(tmp_path)
    inv = repo.load_inventory()
    by_host = {d.hostname: d for d in inv.devices}
    assert set(by_host) == {"spine-1.croc.lab", "srv-rocky-1.croc.lab"}
    spine1 = by_host["spine-1.croc.lab"]
    assert str(spine1.management_ip) == "5.228.243.54"
    assert spine1.management_port == 2211
    assert spine1.site == "croc-dc1-pod1"
    assert spine1.rack == "R1"
    assert spine1.unit == 42
    srv = by_host["srv-rocky-1.croc.lab"]
    assert str(srv.management_ip) == "5.228.243.54"
    assert srv.management_port == 221


