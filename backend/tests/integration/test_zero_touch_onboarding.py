"""Integration tests for Zero-Touch Onboarding, Instant Target Discovery & Manual Protocol Override.

Covers:
1. Instant Dynamic Target Discovery (HTTP SD & gNMIc targets update on device/stand mutations).
2. Flexible Manual Protocol Override (PUT /api/v1/telemetry/collectors/{hostname} succeeds without probe requirement).
3. Stand Sync platform inference from hostname heuristics & explicit port map override.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from netops.enums import Platform, UserRole
from netops.models import Device, Stand
from netops.services.stand_sync import (
    infer_platform_from_name,
    sync_stand_devices_from_port_map,
)
from tests.conftest import auth

ADMIN = auth(UserRole.ADMIN)
OPERATOR = auth(UserRole.OPERATOR)
VIEWER = auth(UserRole.VIEWER)


def test_device_creation_and_deletion_immediately_reflects_in_http_sd(
    client: TestClient, session: Session
) -> None:
    """Device creation and deletion immediately update Prometheus HTTP SD endpoints."""
    # 1. Create a Cisco switch device
    cisco_payload = {
        "hostname": "test-cisco-leaf-99.croc.lab",
        "management_ip": "172.20.20.99",
        "management_port": 22,
        "platform": "cisco_iosxe",
        "role": "leaf",
        "auth_profile": "lab",
    }
    create_resp = client.post("/api/v1/devices", json=cisco_payload, headers=ADMIN)
    assert create_resp.status_code == 201, create_resp.text
    dev_id = create_resp.json()["id"]

    client.put(f"/api/v1/telemetry/collectors/test-cisco-leaf-99.croc.lab", json={"protocol": "snmp"}, headers=ADMIN)
    # 2. Check Prometheus SNMP HTTP SD - device with explicit SNMP choice must be immediately present
    sd_snmp_resp = client.get("/api/v1/telemetry/sd/snmp")
    assert sd_snmp_resp.status_code == 200
    snmp_targets = sd_snmp_resp.json()
    snmp_hostnames = [t.get("labels", {}).get("hostname") for t in snmp_targets]
    assert "test-cisco-leaf-99.croc.lab" in snmp_hostnames

    # 3. Create a Linux Server device
    server_payload = {
        "hostname": "test-srv-rocky-99.croc.lab",
        "management_ip": "172.20.30.99",
        "management_port": 22,
        "platform": "linux_server",
        "role": "server",
        "auth_profile": "lab",
    }
    create_srv_resp = client.post("/api/v1/devices", json=server_payload, headers=ADMIN)
    assert create_srv_resp.status_code == 201, create_srv_resp.text
    srv_id = create_srv_resp.json()["id"]

    client.put(f"/api/v1/telemetry/collectors/test-srv-rocky-99.croc.lab", json={"protocol": "prometheus_exporter"}, headers=ADMIN)
    # 4. Check Prometheus Node Exporter HTTP SD - server with explicit exporter choice must be immediately present
    sd_exp_resp = client.get("/api/v1/telemetry/sd/exporter")
    assert sd_exp_resp.status_code == 200
    exp_targets = sd_exp_resp.json()
    exp_hostnames = [t.get("labels", {}).get("hostname") for t in exp_targets]
    assert "test-srv-rocky-99.croc.lab" in exp_hostnames

    # 5. Delete Cisco device - must disappear from SNMP HTTP SD
    del_resp = client.delete(f"/api/v1/devices/{dev_id}", headers=ADMIN)
    assert del_resp.status_code == 204

    sd_snmp_after = client.get("/api/v1/telemetry/sd/snmp")
    snmp_after_hostnames = [t.get("labels", {}).get("hostname") for t in sd_snmp_after.json()]
    assert "test-cisco-leaf-99.croc.lab" not in snmp_after_hostnames

    # Clean up server
    client.delete(f"/api/v1/devices/{srv_id}", headers=ADMIN)


def test_manual_protocol_override_succeeds_without_probe_validation_error(
    client: TestClient, session: Session, devices: dict[str, Device]
) -> None:
    """Administrative manual override in PUT /collectors/{hostname} succeeds without probe requirement."""
    target_host = sorted(devices.keys())[0]

    # Set manual override to gNMI even though live capabilities may not have been probed
    override_resp = client.put(
        f"/api/v1/telemetry/collectors/{target_host}",
        json={"protocol": "gnmi", "manual_override": True},
        headers=OPERATOR,
    )
    assert override_resp.status_code == 200, override_resp.text
    body = override_resp.json()

    dev_plan = body.get("devices", {}).get(target_host, {})
    assert dev_plan.get("protocol") == "gnmi"
    assert dev_plan.get("manual_override") is True
    assert dev_plan.get("selected_mode") == "gnmi"

    # Reset back to auto
    reset_resp = client.put(
        f"/api/v1/telemetry/collectors/{target_host}",
        json={"protocol": "auto"},
        headers=OPERATOR,
    )
    assert reset_resp.status_code == 200


def test_stand_sync_platform_inference_and_explicit_override(session: Session) -> None:
    """sync_stand_devices_from_port_map infers platforms via heuristics and supports explicit overrides."""
    stand = Stand(
        slug="test-infer-pod",
        name="Test Inferred Stand",
        site="LAB-1",
        driver="offline",
        jump_host="10.0.0.1",
        jump_port=22,
    )
    session.add(stand)
    session.flush()

    # 1. Test heuristic inference from hostname
    port_map_heuristics: dict[str, Any] = {
        "leaf-cisco-1.croc.lab": "10.0.1.1:22",
        "spine-huawei-1.croc.lab": "10.0.1.2:22",
        "leaf-arista-1.croc.lab": "10.0.1.3:22",
        "srv-app-1.croc.lab": "10.0.1.4:22",
    }
    nodes = sync_stand_devices_from_port_map(session, stand, port_map_heuristics)
    assert len(nodes) == 4

    dev_cisco = session.query(Device).filter(Device.hostname == "leaf-cisco-1.croc.lab").first()
    assert dev_cisco is not None
    assert dev_cisco.platform == Platform.CISCO_IOSXE

    dev_huawei = session.query(Device).filter(Device.hostname == "spine-huawei-1.croc.lab").first()
    assert dev_huawei is not None
    assert dev_huawei.platform == Platform.HUAWEI_VRP

    dev_arista = session.query(Device).filter(Device.hostname == "leaf-arista-1.croc.lab").first()
    assert dev_arista is not None
    assert dev_arista.platform == Platform.ARISTA_EOS

    dev_srv = session.query(Device).filter(Device.hostname == "srv-app-1.croc.lab").first()
    assert dev_srv is not None
    assert dev_srv.platform == Platform.LINUX_SERVER

    # 2. Test explicit platform override in port map dictionary
    port_map_explicit: dict[str, Any] = {
        "platform": "cisco_iosxe",
        "custom-node-a.croc.lab": "10.0.2.1:22",
    }
    nodes_exp = sync_stand_devices_from_port_map(session, stand, port_map_explicit)
    assert len(nodes_exp) == 1

    dev_custom_a = session.query(Device).filter(Device.hostname == "custom-node-a.croc.lab").first()
    assert dev_custom_a is not None
    assert dev_custom_a.platform == Platform.CISCO_IOSXE

    # 3. Test per-device platform dictionary override
    port_map_per_device: dict[str, Any] = {
        "custom-node-b.croc.lab": {
            "endpoint": "10.0.2.2:22",
            "platform": "huawei_vrp",
        }
    }
    nodes_pd = sync_stand_devices_from_port_map(session, stand, port_map_per_device)
    assert len(nodes_pd) == 1

    dev_custom_b = session.query(Device).filter(Device.hostname == "custom-node-b.croc.lab").first()
    assert dev_custom_b is not None
    assert dev_custom_b.platform == Platform.HUAWEI_VRP


def test_infer_platform_from_name_heuristics() -> None:
    """Unit checks for hostname heuristic matching."""
    assert infer_platform_from_name("cisco-spine-1") == Platform.CISCO_IOSXE
    assert infer_platform_from_name("csr1000v-edge") == Platform.CISCO_IOSXE
    assert infer_platform_from_name("huawei-leaf-1") == Platform.HUAWEI_VRP
    assert infer_platform_from_name("ce6800-agg") == Platform.HUAWEI_VRP
    assert infer_platform_from_name("arista-leaf-2") == Platform.ARISTA_EOS
    assert infer_platform_from_name("veos-spine") == Platform.ARISTA_EOS
    assert infer_platform_from_name("srv-ubuntu-k8s") == Platform.LINUX_SERVER
    assert infer_platform_from_name("rocky-host-01") == Platform.LINUX_SERVER
    assert infer_platform_from_name("generic-switch-1") == Platform.ARISTA_EOS


def test_platform_commands_endpoint_serves_yaml_commands(client: TestClient) -> None:
    resp = client.get("/api/v1/telemetry/platform-commands", headers=VIEWER)
    assert resp.status_code == 200
    cmds = resp.json()
    assert cmds["huawei_vrp"]["bgp_summary"] == "display bgp peer"
    assert cmds["cisco_iosxe"]["bgp_summary"] == "show ip bgp summary"
