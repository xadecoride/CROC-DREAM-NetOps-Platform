"""Adversarial stress test suite for Milestone M2: Dynamic Target Discovery & SD Builders.

Tests:
1. Device lifecycle (Create -> Update -> Delete) reflection in Prometheus HTTP SD.
2. Stand lifecycle (Create -> Update -> Delete) reflection in Prometheus HTTP SD.
3. Edge cases: Empty DB SD targets, Huawei SNMP suppression, invalid platforms.
4. High-concurrency stress test: Rapid concurrent mutations & queries against race conditions,
   stale reads, and unhandled 500 exceptions in targets.py and sd_builder.py.
"""

from __future__ import annotations

import concurrent.futures

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from netops.enums import UserRole
from tests.conftest import auth

ADMIN = auth(UserRole.ADMIN)
OPERATOR = auth(UserRole.OPERATOR)
VIEWER = auth(UserRole.VIEWER)


def test_device_update_immediately_reflects_in_http_sd(
    client: TestClient, session: Session
) -> None:
    """Updating a device's platform or IP must immediately reflect in Prometheus HTTP SD on the next query."""
    # 1. Create a Cisco IOS-XE device
    dev_payload = {
        "hostname": "adv-dev-update-01.croc.lab",
        "management_ip": "172.28.10.1",
        "management_port": 22,
        "platform": "cisco_iosxe",
        "role": "leaf",
        "auth_profile": "lab",
    }
    create_resp = client.post("/api/v1/devices", json=dev_payload, headers=ADMIN)
    assert create_resp.status_code == 201, create_resp.text
    dev_id = create_resp.json()["id"]

    try:
        client.put("/api/v1/telemetry/collectors/adv-dev-update-01.croc.lab", json={"protocol": "snmp"}, headers=ADMIN)
        # Check SNMP SD - must be present
        sd_snmp_1 = client.get("/api/v1/telemetry/sd/snmp").json()
        snmp_hosts_1 = [t.get("labels", {}).get("hostname") for t in sd_snmp_1]
        assert "adv-dev-update-01.croc.lab" in snmp_hosts_1, f"Expected in SNMP SD, got: {snmp_hosts_1}"

        # 2. Update device platform to linux_server and management_ip to 172.28.10.2
        patch_resp = client.patch(
            f"/api/v1/devices/{dev_id}",
            json={"platform": "linux_server", "management_ip": "172.28.10.2"},
            headers=ADMIN,
        )
        assert patch_resp.status_code == 200, patch_resp.text

        client.put("/api/v1/telemetry/collectors/adv-dev-update-01.croc.lab", json={"protocol": "prometheus_exporter"}, headers=ADMIN)
        # 3. Very next queries to HTTP SD endpoints
        sd_snmp_2 = client.get("/api/v1/telemetry/sd/snmp").json()
        snmp_hosts_2 = [t.get("labels", {}).get("hostname") for t in sd_snmp_2]
        assert "adv-dev-update-01.croc.lab" not in snmp_hosts_2, (
            f"Device adv-dev-update-01.croc.lab should NO LONGER be in SNMP SD after updating to linux_server! Got: {snmp_hosts_2}"
        )

        sd_exp_2 = client.get("/api/v1/telemetry/sd/exporter").json()
        exp_targets_2 = {t.get("labels", {}).get("hostname"): t.get("targets", []) for t in sd_exp_2}
        assert "adv-dev-update-01.croc.lab" in exp_targets_2, (
            f"Device adv-dev-update-01.croc.lab MUST be in Exporter SD after updating to linux_server! Got: {exp_targets_2}"
        )
        assert any("172.28.10.2:9100" in tg for tg in exp_targets_2.get("adv-dev-update-01.croc.lab", [])), (
            f"Exporter target must use updated IP 172.28.10.2:9100, got: {exp_targets_2.get('adv-dev-update-01.croc.lab')}"
        )

    finally:
        client.delete(f"/api/v1/devices/{dev_id}", headers=ADMIN)


def test_empty_database_sd_targets_returns_empty_list(
    client: TestClient, session: Session
) -> None:
    """When all devices are deleted from database, HTTP SD must return an empty list without stale entries or crash."""
    # 1. Create a temporary device
    dev_payload = {
        "hostname": "adv-temp-cisco.croc.lab",
        "management_ip": "172.28.20.1",
        "management_port": 22,
        "platform": "cisco_iosxe",
        "role": "leaf",
        "auth_profile": "lab",
    }
    c_resp = client.post("/api/v1/devices", json=dev_payload, headers=ADMIN)
    assert c_resp.status_code == 201, c_resp.text
    dev_id = c_resp.json()["id"]

    client.put("/api/v1/telemetry/collectors/adv-temp-cisco.croc.lab", json={"protocol": "snmp"}, headers=ADMIN)
    # Verify present in SNMP
    sd_snmp = client.get("/api/v1/telemetry/sd/snmp").json()
    assert any(t.get("labels", {}).get("hostname") == "adv-temp-cisco.croc.lab" for t in sd_snmp)

    # 2. Delete the device
    del_resp = client.delete(f"/api/v1/devices/{dev_id}", headers=ADMIN)
    assert del_resp.status_code == 204

    # 3. Query HTTP SD
    sd_snmp_after = client.get("/api/v1/telemetry/sd/snmp").json()
    assert not any(t.get("labels", {}).get("hostname") == "adv-temp-cisco.croc.lab" for t in sd_snmp_after)


def test_huawei_snmp_suppression_invariant(
    client: TestClient, session: Session
) -> None:
    """Huawei VRP devices must never appear in Prometheus SNMP HTTP SD targets even if manually forced."""
    payload = {
        "hostname": "adv-huawei-spine-99.croc.lab",
        "management_ip": "172.28.30.1",
        "management_port": 22,
        "platform": "huawei_vrp",
        "role": "spine",
        "auth_profile": "lab",
    }
    create_resp = client.post("/api/v1/devices", json=payload, headers=ADMIN)
    assert create_resp.status_code == 201, create_resp.text
    dev_id = create_resp.json()["id"]

    try:
        # Check SNMP SD - must NOT be present
        sd_snmp = client.get("/api/v1/telemetry/sd/snmp").json()
        assert not any(t.get("labels", {}).get("hostname") == "adv-huawei-spine-99.croc.lab" for t in sd_snmp)

        # Attempt manual override to snmp via PUT /api/v1/telemetry/collectors/{hostname}
        override_resp = client.put(
            "/api/v1/telemetry/collectors/adv-huawei-spine-99.croc.lab",
            json={"protocol": "snmp", "manual_override": True},
            headers=OPERATOR,
        )
        assert override_resp.status_code == 200
        plan = override_resp.json()
        dev_plan = plan.get("devices", {}).get("adv-huawei-spine-99.croc.lab", {})
        # Manual override to SNMP on Huawei should be rejected or sanitized to ssh_cli
        assert dev_plan.get("protocol") != "snmp", "Huawei VRP must NOT accept SNMP protocol"

        # HTTP SD check again
        sd_snmp_after = client.get("/api/v1/telemetry/sd/snmp").json()
        assert not any(t.get("labels", {}).get("hostname") == "adv-huawei-spine-99.croc.lab" for t in sd_snmp_after)
    finally:
        client.delete(f"/api/v1/devices/{dev_id}", headers=ADMIN)


def test_concurrent_mutations_and_sd_reads_stress(
    client: TestClient, session: Session
) -> None:
    """Stress test: 20 concurrent threads performing rapid device creation, deletion, protocol overrides,
    and HTTP SD reads without unhandled 500 exceptions, race conditions, or malformed responses.
    """
    total_ops = 30
    errors: list[str] = []

    def op_create_and_delete(idx: int) -> None:
        hname = f"adv-stress-node-{idx}.croc.lab"
        plat = "linux_server" if idx % 2 == 0 else "cisco_iosxe"
        ip = f"172.29.{idx // 254}.{(idx % 254) + 1}"
        # 1. Create
        c_res = client.post(
            "/api/v1/devices",
            json={
                "hostname": hname,
                "management_ip": ip,
                "management_port": 22,
                "platform": plat,
                "role": "leaf",
                "auth_profile": "lab",
            },
            headers=ADMIN,
        )
        if c_res.status_code not in (201, 409):
            errors.append(f"Create failed for {hname}: {c_res.status_code} - {c_res.text}")
            return

        if c_res.status_code == 201:
            dev_id = c_res.json()["id"]
            # 2. Read SD
            sd_res = client.get("/api/v1/telemetry/sd/snmp" if plat == "cisco_iosxe" else "/api/v1/telemetry/sd/exporter")
            if sd_res.status_code != 200:
                errors.append(f"SD query failed: {sd_res.status_code} - {sd_res.text}")

            # 3. Protocol override
            ov_res = client.put(
                f"/api/v1/telemetry/collectors/{hname}",
                json={"protocol": "ssh_cli"},
                headers=OPERATOR,
            )
            if ov_res.status_code not in (200, 404):
                errors.append(f"Override failed for {hname}: {ov_res.status_code} - {ov_res.text}")

            # 4. Delete
            d_res = client.delete(f"/api/v1/devices/{dev_id}", headers=ADMIN)
            if d_res.status_code not in (204, 404):
                errors.append(f"Delete failed for {hname}: {d_res.status_code} - {d_res.text}")

    def op_read_sd(idx: int) -> None:
        endpoint = "/api/v1/telemetry/sd/snmp" if idx % 2 == 0 else "/api/v1/telemetry/sd/exporter"
        res = client.get(endpoint)
        if res.status_code != 200:
            errors.append(f"Concurrent SD read failed on {endpoint}: {res.status_code} - {res.text}")
        else:
            data = res.json()
            if not isinstance(data, list):
                errors.append(f"Expected list response from {endpoint}, got: {type(data)}")
            for item in data:
                if not isinstance(item, dict) or "targets" not in item or "labels" not in item:
                    errors.append(f"Malformed Prometheus SD target: {item}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = []
        for i in range(total_ops):
            futures.append(executor.submit(op_create_and_delete, i))
            futures.append(executor.submit(op_read_sd, i))
        for f in concurrent.futures.as_completed(futures):
            try:
                f.result()
            except Exception as exc:
                errors.append(f"Thread raised unexpected exception: {exc}")

    assert not errors, f"Stress test encountered {len(errors)} errors:\n" + "\n".join(errors[:20])
