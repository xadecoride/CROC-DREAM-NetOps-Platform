"""Unit tests for device status initialization and honest data guarantees."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from netops.api.routes.devices import create_device
from netops.enums import DeviceRole, DeviceStatus, Platform, UserRole
from netops.models import Device, User
from netops.schemas.devices import DeviceCreate
from netops.services.devices import DeviceService
from netops.settings import Settings
from tests.conftest import auth

ADMIN_AUTH = auth(UserRole.ADMIN)


def test_device_model_db_defaults_to_unknown_status(session: Session) -> None:
    """Device model persisted without explicit status must default to UNKNOWN in DB."""
    dev = Device(
        hostname="leaf-test-init.croc.lab",
        management_ip="172.20.10.99",
        platform=Platform.ARISTA_EOS,
        role=DeviceRole.LEAF,
        auth_profile="lab",
    )
    session.add(dev)
    session.commit()
    session.refresh(dev)
    assert dev.status == DeviceStatus.UNKNOWN


def test_newly_created_device_api_has_unknown_status(client: TestClient, session: Session) -> None:
    """API endpoint POST /api/v1/devices must return status UNKNOWN for newly created devices."""
    payload = {
        "hostname": "border-unknown-check.croc.lab",
        "management_ip": "172.20.10.88",
        "platform": "cisco_iosxe",
        "role": "border",
        "auth_profile": "lab",
        "username": "netadmin",
        "password": "secret-password",
    }
    resp = client.post("/api/v1/devices", json=payload, headers=ADMIN_AUTH)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "UNKNOWN", f"Expected UNKNOWN, got {body['status']}"

    # Also verify direct DB state
    session.expire_all()
    dev = session.query(Device).filter(Device.hostname == "border-unknown-check.croc.lab").first()
    assert dev is not None
    assert dev.status == DeviceStatus.UNKNOWN


def test_create_device_route_keeps_status_unknown_when_sot_bound(
    session: Session, settings: Settings
) -> None:
    """Route create_device must never force DeviceStatus.IN_SYNC even when bind_device_to_sot runs."""
    service = DeviceService(session)
    container = SimpleNamespace(settings=settings)
    admin_user = User(username="admin", role=UserRole.ADMIN, allowed_system=None)

    data = DeviceCreate(
        hostname="spine-init-honest.croc.lab",
        management_ip="172.20.10.77",
        management_port=22,
        platform=Platform.ARISTA_EOS,
        role=DeviceRole.SPINE,
        auth_profile="lab",
        username="admin",
    )

    created = create_device(data, service, container, admin_user)  # type: ignore[arg-type]
    assert created.status == DeviceStatus.UNKNOWN
