"""Empirical adversarial stress test suite for Milestone M2:
Manual Override, Protocol Invariants, and Platform Inference.

Authored by Challenger 2 (M2) to empirically verify:
1. PUT /api/v1/telemetry/collectors/{hostname} with manual protocol overrides:
   - Administrator and Operator can override protocol even when probe flagged supported: false.
   - Unsafe protocol combinations (SNMP on Huawei VRP) remain properly blocked without crashes.
   - RBAC enforcement: viewer and unauthenticated requests cannot mutate collector protocol (403/401).
   - Bug reproduction: manual override to prometheus_exporter / node_exporter causes target drop.
2. Stand sync platform inference with diverse and edge-case hostnames:
   - Mixed-case, hyphenated, multi-vendor names.
   - parse_platform_value robustness against malformed/null/numeric inputs.
   - sync_stand_devices_from_port_map handling of unknown platforms and structured port maps.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from netops.enums import Platform, UserRole
from netops.models import Device, Stand
from netops.services.stand_sync import (
    infer_platform_from_name,
    parse_platform_value,
    sync_stand_devices_from_port_map,
)
from netops.services.state import get_state_manager
from tests.conftest import auth

ADMIN = auth(UserRole.ADMIN)
OPERATOR = auth(UserRole.OPERATOR)
VIEWER = auth(UserRole.VIEWER)


# ==============================================================================
# 1. RBAC MUTATION RESTRICTION TESTS
# ==============================================================================

def test_rbac_viewer_cannot_mutate_collector_protocol(
    client: TestClient, session: Session, devices: dict[str, Device]
) -> None:
    """Viewer role must strictly receive 403 Forbidden when attempting PUT /collectors/{hostname}."""
    target_host = sorted(devices.keys())[0]

    # Attempt mutation as viewer
    resp = client.put(
        f"/api/v1/telemetry/collectors/{target_host}",
        json={"protocol": "gnmi", "manual_override": True},
        headers=VIEWER,
    )
    assert resp.status_code == 403, f"Expected 403 for viewer, got {resp.status_code}: {resp.text}"
    assert "The operator role is required" in resp.text or "role is required" in resp.text

    # Verify state was NOT modified
    sm = get_state_manager()
    overrides = sm.get_json("telemetry_protocol_overrides") or {}
    assert overrides.get(target_host) != "gnmi", "Viewer mutation leaked into state manager overrides!"


def test_rbac_viewer_cannot_refresh_collectors(client: TestClient) -> None:
    """Viewer role must strictly receive 403 Forbidden when attempting POST /collectors/refresh."""
    resp = client.post("/api/v1/telemetry/collectors/refresh", headers=VIEWER)
    assert resp.status_code == 403, f"Expected 403 for viewer on refresh, got {resp.status_code}: {resp.text}"


def test_unauthenticated_cannot_mutate_collector_protocol(
    client: TestClient, devices: dict[str, Device]
) -> None:
    """Unauthenticated request must strictly receive 401 Unauthorized."""
    target_host = sorted(devices.keys())[0]
    resp = client.put(
        f"/api/v1/telemetry/collectors/{target_host}",
        json={"protocol": "gnmi", "manual_override": True},
    )
    assert resp.status_code == 401, f"Expected 401 for anonymous, got {resp.status_code}: {resp.text}"


def test_rbac_operator_and_admin_and_owner_can_mutate_collector(
    client: TestClient, devices: dict[str, Device]
) -> None:
    """Operator, Admin, and Owner roles must all be permitted to update collector protocols."""
    target_host = sorted(devices.keys())[0]

    # Operator
    op_resp = client.put(
        f"/api/v1/telemetry/collectors/{target_host}",
        json={"protocol": "gnmi"},
        headers=OPERATOR,
    )
    assert op_resp.status_code == 200, f"Operator failed: {op_resp.text}"

    # Admin
    admin_resp = client.put(
        f"/api/v1/telemetry/collectors/{target_host}",
        json={"protocol": "prometheus_exporter"},
        headers=ADMIN,
    )
    assert admin_resp.status_code == 200, f"Admin failed: {admin_resp.text}"

    # Owner
    owner_resp = client.put(
        f"/api/v1/telemetry/collectors/{target_host}",
        json={"protocol": "auto"},
        headers=auth(UserRole.OWNER),
    )
    assert owner_resp.status_code == 200, f"Owner failed: {owner_resp.text}"


# ==============================================================================
# 2. MANUAL OVERRIDE WHEN PROBE FLAGGED SUPPORTED: FALSE
# ==============================================================================

def test_admin_can_override_protocol_when_probe_flagged_unsupported(
    client: TestClient, session: Session
) -> None:
    """Admin override succeeds even when probe recorded supported: False."""
    # Create an Arista switch
    dev_payload = {
        "hostname": "adv-unsupported-probe-switch.croc.lab",
        "management_ip": "172.29.10.1",
        "management_port": 22,
        "platform": "arista_eos",
        "role": "leaf",
        "auth_profile": "lab",
    }
    create_resp = client.post("/api/v1/devices", json=dev_payload, headers=ADMIN)
    assert create_resp.status_code == 201
    dev_id = create_resp.json()["id"]

    try:
        # Seed an existing plan where gnmi probe explicitly flagged supported: False
        sm = get_state_manager()
        fake_plan = {
            "refreshed_at": "2026-10-22T00:00:00Z",
            "devices": {
                dev_payload["hostname"]: {
                    "protocol": "ssh_cli",
                    "port": 22,
                    "host": "172.29.10.1",
                    "platform": "arista_eos",
                    "fabric_id": "default",
                    "supported_protocols": {
                        "gnmi": {"supported": False, "port": 6030, "verified_by": None},
                        "snmp": {"supported": True, "port": 161, "verified_by": "snmp_profile"},
                    },
                }
            },
        }
        sm.set_json("telemetry_protocol_plan", fake_plan)
        sm.set_db_state("telemetry_protocol_plan", fake_plan)

        # Admin overrides to gnmi despite supported: False
        override_resp = client.put(
            f"/api/v1/telemetry/collectors/{dev_payload['hostname']}",
            json={"protocol": "gnmi", "manual_override": True},
            headers=ADMIN,
        )
        assert override_resp.status_code == 200, override_resp.text
        plan = override_resp.json()
        dev_entry = plan.get("devices", {}).get(dev_payload["hostname"], {})

        assert dev_entry.get("protocol") == "gnmi", "Protocol was not overridden to gnmi!"
        assert dev_entry.get("manual_override") is True
        assert dev_entry.get("selected_mode") == "gnmi"
        assert dev_entry.get("verified_by") == "manual_override:gnmi"

    finally:
        client.delete(f"/api/v1/devices/{dev_id}", headers=ADMIN)


# ==============================================================================
# 3. PROTOCOL SAFETY INVARIANTS: HUAWEI VRP VS SNMP
# ==============================================================================

def test_huawei_vrp_snmp_override_properly_blocked_without_crash(
    client: TestClient, session: Session
) -> None:
    """Overriding Huawei VRP to SNMP is blocked (override_rejected) and never crashes or emits SNMP SD."""
    # Create a Huawei VRP switch
    huawei_payload = {
        "hostname": "adv-huawei-core-01.croc.lab",
        "management_ip": "172.29.20.1",
        "management_port": 22,
        "platform": "huawei_vrp",
        "role": "spine",
        "auth_profile": "lab",
    }
    c_resp = client.post("/api/v1/devices", json=huawei_payload, headers=ADMIN)
    assert c_resp.status_code == 201
    dev_id = c_resp.json()["id"]

    try:
        # 1. Attempt manual override to snmp on Huawei VRP
        override_resp = client.put(
            f"/api/v1/telemetry/collectors/{huawei_payload['hostname']}",
            json={"protocol": "snmp", "manual_override": True},
            headers=ADMIN,
        )
        # Must not crash (500)
        assert override_resp.status_code in (200, 400), f"Unexpected status {override_resp.status_code}: {override_resp.text}"

        body = override_resp.json()
        dev_info = body.get("devices", {}).get(huawei_payload["hostname"], {})
        # If 200, must indicate rejection of override
        if override_resp.status_code == 200:
            assert dev_info.get("override_rejected") == "snmp_not_supported"
            assert dev_info.get("protocol") != "snmp", "Huawei VRP protocol must NOT be set to snmp!"

        # 2. Check SNMP HTTP SD - Huawei VRP must NEVER appear in SNMP discovery
        sd_snmp_resp = client.get("/api/v1/telemetry/sd/snmp")
        assert sd_snmp_resp.status_code == 200
        snmp_targets = sd_snmp_resp.json()
        snmp_hostnames = [t.get("labels", {}).get("hostname") for t in snmp_targets]
        assert huawei_payload["hostname"] not in snmp_hostnames, (
            f"CRITICAL INVARIANT VIOLATION: Huawei VRP switch appeared in Prometheus SNMP targets! {snmp_hostnames}"
        )

        # 3. Test case variations: "SNMP", "  snmp  "
        for variant in ("SNMP", "  snmp  ", "Snmp"):
            var_resp = client.put(
                f"/api/v1/telemetry/collectors/{huawei_payload['hostname']}",
                json={"protocol": variant},
                headers=ADMIN,
            )
            assert var_resp.status_code in (200, 400)
            if var_resp.status_code == 200:
                v_body = var_resp.json()
                v_dev = v_body.get("devices", {}).get(huawei_payload["hostname"], {})
                assert v_dev.get("protocol") != "snmp"

    finally:
        client.delete(f"/api/v1/devices/{dev_id}", headers=ADMIN)


def test_cisco_allowed_protocol_snmp_succeeds(
    client: TestClient, session: Session
) -> None:
    """Cisco IOS-XE allows SNMP override and appears in Prometheus SNMP HTTP SD."""
    cisco_payload = {
        "hostname": "adv-cisco-snmp-01.croc.lab",
        "management_ip": "172.29.30.1",
        "management_port": 22,
        "platform": "cisco_iosxe",
        "role": "leaf",
        "auth_profile": "lab",
    }
    c_resp = client.post("/api/v1/devices", json=cisco_payload, headers=ADMIN)
    assert c_resp.status_code == 201
    cisco_id = c_resp.json()["id"]

    try:
        # Cisco to SNMP
        cisco_snmp = client.put(
            f"/api/v1/telemetry/collectors/{cisco_payload['hostname']}",
            json={"protocol": "snmp"},
            headers=ADMIN,
        )
        assert cisco_snmp.status_code == 200
        assert cisco_snmp.json()["devices"][cisco_payload["hostname"]]["protocol"] == "snmp"

        # Check Cisco is in SNMP SD
        snmp_sd = client.get("/api/v1/telemetry/sd/snmp").json()
        assert any(t.get("labels", {}).get("hostname") == cisco_payload["hostname"] for t in snmp_sd)

    finally:
        client.delete(f"/api/v1/devices/{cisco_id}", headers=ADMIN)


def test_bug_node_exporter_override_drops_device_from_sd(
    client: TestClient, session: Session
) -> None:
    """EMPIRICAL BUG REPRODUCTION:
    When a server device is created, it initially appears in /api/v1/telemetry/sd/exporter.
    However, when an administrator applies a manual override to 'node_exporter' or 'prometheus_exporter',
    the device DISAPPEARS from the Prometheus exporter HTTP SD targets because sd_builder.py line 206
    checks `proto in (TelemetryProtocol.PROMETHEUS_EXPORTER.value, 'node_exporter')`, which evaluates
    to `proto in ('node_exporter', 'node_exporter')`, omitting 'prometheus_exporter'.
    """
    srv_payload = {
        "hostname": "adv-srv-node-bug-01.croc.lab",
        "management_ip": "172.29.30.2",
        "management_port": 22,
        "platform": "linux_server",
        "role": "server",
        "auth_profile": "lab",
    }
    s_resp = client.post("/api/v1/devices", json=srv_payload, headers=ADMIN)
    assert s_resp.status_code == 201
    srv_id = s_resp.json()["id"]

    try:
        # Before manual override: an unproven device stays on ssh_cli and is not an exporter target
        exp_sd_before = client.get("/api/v1/telemetry/sd/exporter").json()
        assert not any(t.get("labels", {}).get("hostname") == srv_payload["hostname"] for t in exp_sd_before)

        # Apply manual override to node_exporter
        srv_node = client.put(
            f"/api/v1/telemetry/collectors/{srv_payload['hostname']}",
            json={"protocol": "node_exporter"},
            headers=ADMIN,
        )
        assert srv_node.status_code == 200
        assert srv_node.json()["devices"][srv_payload["hostname"]]["protocol"] == "prometheus_exporter"

        # Bug demonstration: after manual override, device vanishes from HTTP SD
        exp_sd_after = client.get("/api/v1/telemetry/sd/exporter").json()
        found_in_sd = any(t.get("labels", {}).get("hostname") == srv_payload["hostname"] for t in exp_sd_after)
        assert found_in_sd, (
            "Prometheus exporter target must be present in HTTP SD after manual node_exporter override!"
        )

    finally:
        client.delete(f"/api/v1/devices/{srv_id}", headers=ADMIN)


def test_manual_override_from_snmp_to_gnmi_cache_invalidation(
    client: TestClient, session: Session
) -> None:
    """Overriding a device from SNMP to gNMI must immediately remove it from SNMP HTTP SD."""
    cisco_payload = {
        "hostname": "adv-cisco-switch-override.croc.lab",
        "management_ip": "172.29.35.1",
        "management_port": 22,
        "platform": "cisco_iosxe",
        "role": "leaf",
        "auth_profile": "lab",
    }
    c_resp = client.post("/api/v1/devices", json=cisco_payload, headers=ADMIN)
    assert c_resp.status_code == 201
    dev_id = c_resp.json()["id"]

    try:
        # Unproven devices stay on ssh_cli; the operator picks SNMP explicitly
        client.put(f"/api/v1/telemetry/collectors/{cisco_payload['hostname']}", json={"protocol": "snmp"}, headers=ADMIN)
        # Step 1: Read SNMP SD -> populates cache with Cisco device, sets sd_targets_dirty = False
        sd_snmp_1 = client.get("/api/v1/telemetry/sd/snmp").json()
        hosts_1 = [t.get("labels", {}).get("hostname") for t in sd_snmp_1]
        assert cisco_payload["hostname"] in hosts_1, f"Expected in initial SNMP SD, got: {hosts_1}"

        # Step 2: Override protocol to gNMI
        override_resp = client.put(
            f"/api/v1/telemetry/collectors/{cisco_payload['hostname']}",
            json={"protocol": "gnmi"},
            headers=ADMIN,
        )
        assert override_resp.status_code == 200

        # Step 3: Read SNMP SD again -> must NO LONGER contain this device!
        sd_snmp_2 = client.get("/api/v1/telemetry/sd/snmp").json()
        hosts_2 = [t.get("labels", {}).get("hostname") for t in sd_snmp_2]
        assert cisco_payload["hostname"] not in hosts_2, (
            f"STALE CACHE BUG: Device {cisco_payload['hostname']} still appears in SNMP SD after overriding to gNMI!"
        )
    finally:
        client.delete(f"/api/v1/devices/{dev_id}", headers=ADMIN)


# ==============================================================================
# 4. INPUT VALIDATION & INJECTION RESISTANCE
# ==============================================================================

@pytest.mark.parametrize(
    "bad_proto",
    [
        "ftp",
        "telnet",
        "snmpv3_custom",
        "'; DROP TABLE devices;--",
        "",
        "   ",
        "unknown_proto",
        "gnmi;rm -rf /",
    ],
)
def test_invalid_protocols_rejected_with_400(
    client: TestClient, devices: dict[str, Device], bad_proto: str
) -> None:
    """Invalid protocol strings must be rejected with 400 Bad Request without unhandled exceptions."""
    target_host = sorted(devices.keys())[0]
    resp = client.put(
        f"/api/v1/telemetry/collectors/{target_host}",
        json={"protocol": bad_proto},
        headers=ADMIN,
    )
    assert resp.status_code == 400, f"Expected 400 for '{bad_proto}', got {resp.status_code}: {resp.text}"
    assert "Unsupported protocol" in resp.text


# ==============================================================================
# 5. PLATFORM INFERENCE & PARSER HEURISTICS STRESS TESTING
# ==============================================================================

def test_infer_platform_diverse_and_edge_case_hostnames() -> None:
    """Test infer_platform_from_name against diverse, mixed-case, and edge-case patterns."""
    # 1. Mixed-case and delimiters
    assert infer_platform_from_name("CiScO-SpInE-01") == Platform.CISCO_IOSXE
    assert infer_platform_from_name("HUAWEI_CE6800_ToR") == Platform.HUAWEI_VRP
    assert infer_platform_from_name("ArIsTa-vEoS-02") == Platform.ARISTA_EOS
    assert infer_platform_from_name("RoCkY-SrV-Compute") == Platform.LINUX_SERVER

    # 2. Specific hardware tokens
    assert infer_platform_from_name("csr1000v.core.lab") == Platform.CISCO_IOSXE
    assert infer_platform_from_name("edge-ne40-border") == Platform.HUAWEI_VRP
    assert infer_platform_from_name("ce12800-agg-01") == Platform.HUAWEI_VRP
    assert infer_platform_from_name("host-ubuntu-k8s-node1") == Platform.LINUX_SERVER
    assert infer_platform_from_name("debian-worker-01") == Platform.LINUX_SERVER

    # 3. Generic fallback
    assert infer_platform_from_name("generic-switch-99") == Platform.ARISTA_EOS
    assert infer_platform_from_name("leaf-pod1-01") == Platform.ARISTA_EOS

    # 4. Fallback on empty, whitespace, and special characters
    assert infer_platform_from_name("") == Platform.ARISTA_EOS
    assert infer_platform_from_name("   ") == Platform.ARISTA_EOS
    assert infer_platform_from_name("!@#$%^&*()_+") == Platform.ARISTA_EOS
    assert infer_platform_from_name("192.168.1.1") == Platform.ARISTA_EOS
    assert infer_platform_from_name("x" * 10000) == Platform.ARISTA_EOS

    # 5. Multi-keyword precedence: Cisco checked before Server
    # Note: 'cat' substring matches in words like 'application' or 'cat9k'
    assert infer_platform_from_name("cisco-to-huawei-gateway") == Platform.CISCO_IOSXE


def test_parse_platform_value_robustness() -> None:
    """parse_platform_value must handle enums, formatted strings, None, numbers, and garbage without raising."""
    # Valid enum and string variants
    assert parse_platform_value(Platform.CISCO_IOSXE) == Platform.CISCO_IOSXE
    assert parse_platform_value("cisco_iosxe") == Platform.CISCO_IOSXE
    assert parse_platform_value("CISCO-IOS-XE") == Platform.CISCO_IOSXE
    assert parse_platform_value("huawei_vrp") == Platform.HUAWEI_VRP
    assert parse_platform_value("arista_eos") == Platform.ARISTA_EOS
    assert parse_platform_value("linux_server") == Platform.LINUX_SERVER

    # Resilience against non-string and garbage inputs
    assert parse_platform_value(None) == Platform.ARISTA_EOS
    assert parse_platform_value(12345) == Platform.ARISTA_EOS
    assert parse_platform_value({"unexpected": "dict"}) == Platform.ARISTA_EOS
    assert parse_platform_value("") == Platform.ARISTA_EOS
    assert parse_platform_value("unknown_fantasy_os") == Platform.ARISTA_EOS


def test_sync_stand_devices_from_port_map_edge_cases(session: Session) -> None:
    """sync_stand_devices_from_port_map handles mixed-case, per-device dicts, and invalid platform fallbacks."""
    stand = Stand(
        slug="adv-test-stand-edge",
        name="Adversarial Edge Test Stand",
        site="LAB-1",
        driver="offline",
        jump_host="10.10.0.1",
        jump_port=22,
    )
    session.add(stand)
    session.flush()

    # Port map with diverse structures:
    # - mixed case hostname
    # - per-device dict with explicit platform
    # - per-device dict with invalid platform (must fallback, not crash)
    # - generic hostname
    port_map: dict[str, Any] = {
        "CiScO-LeAf-10.croc.lab": "10.10.1.1:22",
        "custom-node-huawei": {
            "endpoint": "10.10.1.2:22",
            "platform": "HUAWEI-VRP",
        },
        "custom-node-unknown": {
            "endpoint": "10.10.1.3:22",
            "platform": "INVALID_OS_XYZ",
        },
        "srv-compute-node-10": "10.10.1.4:22",
    }

    nodes = sync_stand_devices_from_port_map(session, stand, port_map)
    assert len(nodes) == 4, f"Expected 4 synced nodes, got: {len(nodes)}"

    # Check Cisco mixed case
    cisco_dev = session.query(Device).filter(Device.hostname == "CiScO-LeAf-10.croc.lab").first()
    assert cisco_dev is not None
    assert cisco_dev.platform == Platform.CISCO_IOSXE

    # Check Huawei explicit
    huawei_dev = session.query(Device).filter(Device.hostname == "custom-node-huawei").first()
    assert huawei_dev is not None
    assert huawei_dev.platform == Platform.HUAWEI_VRP

    # Check unknown platform fallback
    unk_dev = session.query(Device).filter(Device.hostname == "custom-node-unknown").first()
    assert unk_dev is not None
    assert isinstance(unk_dev.platform, Platform)

    # Check server
    srv_dev = session.query(Device).filter(Device.hostname == "srv-compute-node-10").first()
    assert srv_dev is not None
    assert srv_dev.platform == Platform.LINUX_SERVER
