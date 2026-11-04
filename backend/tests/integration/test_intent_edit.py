from __future__ import annotations

from fastapi.testclient import TestClient

from netops.enums import UserRole
from tests.conftest import auth

ADMIN = auth(UserRole.ADMIN)
VIEWER = auth(UserRole.VIEWER)


def _first_device_id(client: TestClient) -> tuple[int, str]:
    client.post("/api/v1/inventory/sync", headers=ADMIN)
    dev = client.get("/api/v1/devices", headers=VIEWER).json()[0]
    return dev["id"], dev["hostname"]


def test_get_and_save_roundtrip(client: TestClient) -> None:
    dev_id, host = _first_device_id(client)
    src = client.get(f"/api/v1/devices/{dev_id}/intent-source", headers=VIEWER).json()
    assert src["hostname"] == host and "hostname:" in src["yaml"]

    res = client.put(
        f"/api/v1/devices/{dev_id}/intent-source", json={"yaml": src["yaml"]}, headers=ADMIN
    )
    assert res.status_code == 200 and res.json()["saved"] is True


def test_viewer_cannot_save(client: TestClient) -> None:
    dev_id, _ = _first_device_id(client)
    res = client.put(
        f"/api/v1/devices/{dev_id}/intent-source", json={"yaml": "hostname: x"}, headers=VIEWER
    )
    assert res.status_code == 403


def test_wrong_hostname_and_invalid_model_rejected(client: TestClient) -> None:
    dev_id, host = _first_device_id(client)
    bad_host = client.put(
        f"/api/v1/devices/{dev_id}/intent-source", json={"yaml": "hostname: other"}, headers=ADMIN
    )
    assert bad_host.status_code == 422

    bad_model = client.put(
        f"/api/v1/devices/{dev_id}/intent-source",
        json={"yaml": f"hostname: {host}\ninterfaces: [{{name: GE1, ipv4_address: 10.0.0.1}}]\n"},
        headers=ADMIN,
    )
    assert bad_model.status_code == 200
    assert bad_model.json()["saved"] is False and bad_model.json()["issues"]
