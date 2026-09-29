from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from netops.enums import DeviceStatus, UserRole
from netops.models import Device
from tests.conftest import auth

ADMIN = auth(UserRole.ADMIN)
VIEWER = auth(UserRole.VIEWER)

NEW_DEVICE = {
    "hostname": "border-1.croc.lab",
    "management_ip": "172.20.20.31",
    "platform": "cisco_iosxe",
    "role": "border",
    "auth_profile": "lab",
}


def _create(client: TestClient, **overrides: Any) -> Any:
    response = client.post("/api/v1/devices", json=NEW_DEVICE | overrides, headers=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


class TestCrud:
    def test_create_and_read(self, client: TestClient) -> None:
        created = _create(client)
        assert created["id"] > 0
        assert created["management_port"] == 22
        assert created["status"] == "UNKNOWN"
        assert created["last_checked_at"] is None

        response = client.get(f"/api/v1/devices/{created['id']}", headers=VIEWER)
        assert response.status_code == 200
        body = response.json()
        assert body["hostname"] == "border-1.croc.lab"
        assert body["intent"] is None  # no file in the intent repository
        assert body["intent_issues"] == []

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("management_ip", "300.1.1.1"),
            ("management_port", 0),
            ("platform", "junos"),
            ("role", "core"),
            ("hostname", "bad host"),
        ],
    )
    def test_create_validation(self, client: TestClient, field: str, value: object) -> None:
        response = client.post("/api/v1/devices", json=NEW_DEVICE | {field: value}, headers=ADMIN)
        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["body", field]

    def test_duplicates_are_conflicts(self, client: TestClient) -> None:
        _create(client)
        duplicate_host = client.post(
            "/api/v1/devices", json=NEW_DEVICE | {"management_ip": "172.20.20.99"}, headers=ADMIN
        )
        assert duplicate_host.status_code == 409
        duplicate_endpoint = client.post(
            "/api/v1/devices", json=NEW_DEVICE | {"hostname": "other.croc.lab"}, headers=ADMIN
        )
        assert duplicate_endpoint.status_code == 409
        # Same IP on another forwarded port is a different endpoint.
        _create(client, hostname="other.croc.lab", management_port=2222)

    def test_update(self, client: TestClient) -> None:
        device = _create(client)
        response = client.patch(
            f"/api/v1/devices/{device['id']}",
            json={"role": "leaf", "management_port": 2201},
            headers=ADMIN,
        )
        assert response.status_code == 200
        assert response.json()["role"] == "leaf"
        assert response.json()["management_port"] == 2201
        assert response.json()["hostname"] == NEW_DEVICE["hostname"]

    def test_update_rejects_nulls(self, client: TestClient) -> None:
        device = _create(client)
        response = client.patch(
            f"/api/v1/devices/{device['id']}", json={"hostname": None}, headers=ADMIN
        )
        assert response.status_code == 422

    def test_delete(self, client: TestClient) -> None:
        device = _create(client)
        assert client.delete(f"/api/v1/devices/{device['id']}", headers=ADMIN).status_code == 204
        assert client.get(f"/api/v1/devices/{device['id']}", headers=VIEWER).status_code == 404
        assert client.delete(f"/api/v1/devices/{device['id']}", headers=ADMIN).status_code == 404

    def test_device_being_deployed_cannot_be_changed(
        self, client: TestClient, session: Session
    ) -> None:
        device = _create(client)
        session.get_one(Device, device["id"]).status = DeviceStatus.IN_PROGRESS
        session.commit()
        url = f"/api/v1/devices/{device['id']}"
        assert client.patch(url, json={"role": "leaf"}, headers=ADMIN).status_code == 409
        assert client.delete(url, headers=ADMIN).status_code == 409


class TestListAndDetail:
    def test_filters(self, client: TestClient, devices: dict[str, Device]) -> None:
        def hostnames(**params: str) -> list[str]:
            response = client.get("/api/v1/devices", params=params, headers=VIEWER)
            assert response.status_code == 200
            return [device["hostname"] for device in response.json()]

        assert hostnames() == sorted(devices)
        assert hostnames(role="spine") == ["spine-1.croc.lab", "spine-2.croc.lab"]
        assert hostnames(platform="cisco_iosxe") == ["leaf-1.croc.lab", "leaf-2.croc.lab"]
        assert hostnames(status="UNKNOWN") == sorted(devices)
        assert hostnames(status="IN_SYNC") == []
        assert hostnames(limit="1", offset="1") == ["leaf-2.croc.lab"]

    def test_invalid_filter(self, client: TestClient) -> None:
        response = client.get("/api/v1/devices", params={"status": "BROKEN"}, headers=VIEWER)
        assert response.status_code == 422

    def test_detail_includes_intent_without_secrets(
        self, client: TestClient, devices: dict[str, Device]
    ) -> None:
        device = devices["leaf-1.croc.lab"]
        response = client.get(f"/api/v1/devices/{device.id}", headers=VIEWER)
        body = response.json()
        assert body["intent"]["bgp"]["asn"] == 65101
        assert body["intent"]["interfaces"][1]["ipv4_address"] == "10.0.1.1/31"
        assert "fabric-secret" not in response.text

    def test_detail_reports_broken_intent(
        self, client: TestClient, devices: dict[str, Device], intent_repo: Path
    ) -> None:
        path = intent_repo / "devices" / "leaf-2.croc.lab.yaml"
        data = yaml.safe_load(path.read_text())
        data["bgp"]["asn"] = 0
        path.write_text(yaml.safe_dump(data))

        body = client.get(f"/api/v1/devices/{devices['leaf-2.croc.lab'].id}", headers=VIEWER).json()
        assert body["intent"] is None
        assert body["intent_issues"][0]["location"] == "bgp.asn"


class TestInventorySync:
    def test_initial_import(self, client: TestClient) -> None:
        response = client.post("/api/v1/inventory/sync", headers=ADMIN)
        assert response.status_code == 200
        assert sorted(response.json()["created"]) == [
            "leaf-1.croc.lab",
            "leaf-2.croc.lab",
            "spine-1.croc.lab",
            "spine-2.croc.lab",
        ]
        assert len(client.get("/api/v1/devices", headers=VIEWER).json()) == 4

    def test_resync_updates_changed_devices_only(
        self, client: TestClient, intent_repo: Path
    ) -> None:
        client.post("/api/v1/inventory/sync", headers=ADMIN)
        inventory_file = intent_repo / "inventory.yaml"
        inventory = yaml.safe_load(inventory_file.read_text())
        inventory["devices"][0]["management_port"] = 2201
        inventory_file.write_text(yaml.safe_dump(inventory))

        result = client.post("/api/v1/inventory/sync", headers=ADMIN).json()
        assert result["created"] == []
        assert result["updated"] == ["spine-1.croc.lab"]
        assert len(result["unchanged"]) == 3

    def test_invalid_inventory(self, client: TestClient, intent_repo: Path) -> None:
        (intent_repo / "inventory.yaml").write_text("devices:\n  - hostname: x\n")
        response = client.post("/api/v1/inventory/sync", headers=ADMIN)
        assert response.status_code == 422
        body = response.json()
        assert body["detail"].startswith("Intent validation failed")
        assert {issue["location"] for issue in body["issues"]} >= {"devices.0.management_ip"}

    def test_conflicting_inventory_is_rejected_atomically(
        self, client: TestClient, intent_repo: Path
    ) -> None:
        _create(client, hostname="rogue.croc.lab", management_ip="172.20.20.11")  # spine-1's IP
        response = client.post("/api/v1/inventory/sync", headers=ADMIN)
        assert response.status_code == 409
        assert len(client.get("/api/v1/devices", headers=VIEWER).json()) == 1


def test_intent_lint_endpoint(client: TestClient, intent_repo: Path) -> None:
    assert client.get("/api/v1/intent/lint", headers=VIEWER).json() == {"valid": True, "issues": []}

    path = intent_repo / "devices" / "spine-2.croc.lab.yaml"
    data = yaml.safe_load(path.read_text())
    data["bgp"]["router_id"] = "10.255.0.1"
    path.write_text(yaml.safe_dump(data))

    report = client.get("/api/v1/intent/lint", headers=VIEWER).json()
    assert report["valid"] is False
    assert {issue["code"] for issue in report["issues"]} == {"duplicate_router_id"}
