"""Empirical adversarial stress tests for Milestone 1 (Honest Data & Device Lifecycle).

Verifies that:
1. Malicious or deceptive payloads cannot trick the API/service into setting status to IN_SYNC or UP
   on newly created devices under any permutations (payload fields, username, driver mode, platform, role).
2. Stand discovery on empty stands does not seed phantom switches (172.20.30.*) when network_driver
   is scrapli or live, or when the stand itself is configured with a live driver.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from netops.api.routes.devices import create_device
from netops.enums import DeviceRole, DeviceStatus, Platform, UserRole
from netops.models import Device, Stand, User
from netops.schemas.devices import DeviceCreate
from netops.services.devices import DeviceService
from netops.services.stand_sync import sync_stand_nodes_from_sot_or_portmap
from tests.conftest import auth

ADMIN_AUTH = auth(UserRole.ADMIN)


def test_adversarial_payload_cannot_force_in_sync_or_up(client: TestClient, session: Session) -> None:
    """Attempt to inject forged status, oper_status, vitality_state, sparkline in POST /devices."""
    deceptive_payload = {
        "hostname": "adversarial-probe-trick.croc.lab",
        "management_ip": "172.20.99.1",
        "management_port": 22,
        "platform": "cisco_iosxe",
        "role": "spine",
        "auth_profile": "lab",
        "username": "superadmin",
        "password": "compromised-password",
        "status": "IN_SYNC",
        "oper_status": "UP",
        "vitality_state": "UP",
        "sparkline": [100.0] * 24,
    }
    resp = client.post("/api/v1/devices", json=deceptive_payload, headers=ADMIN_AUTH)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "UNKNOWN", f"Forged status leaked through API response: {body['status']}"
    assert body["oper_status"] == "UNKNOWN", f"Forged oper_status leaked through: {body['oper_status']}"

    session.expire_all()
    dev = session.query(Device).filter(Device.hostname == deceptive_payload["hostname"]).first()
    assert dev is not None
    assert dev.status == DeviceStatus.UNKNOWN, f"DB persisted forged status: {dev.status}"


def test_adversarial_device_creation_permutations(session: Session, tmp_path: Path) -> None:
    """Stress-test create_device under full cartesian product of usernames, platforms, roles, and drivers."""
    usernames = [None, "admin", "operator"]
    passwords = [None, "s3cr3t"]
    drivers = ["offline", "scrapli"]
    platforms = [
        Platform.ARISTA_EOS,
        Platform.CISCO_IOSXE,
        Platform.HUAWEI_VRP,
        Platform.LINUX_SERVER,
    ]
    roles = [
        DeviceRole.SPINE,
        DeviceRole.LEAF,
        DeviceRole.BORDER,
        DeviceRole.SERVER,
    ]
    admin_user = User(username="admin", role=UserRole.ADMIN, allowed_system=None)
    idx = 0
    with patch("netops.services.discovery.extract_brownfield_intent", return_value=None):
        for u, p, d, plat, role in itertools.product(usernames, passwords, drivers, platforms, roles):
            idx += 1
            hname = f"adv-perm-{idx}.croc.lab"
            mip = f"10.99.{idx // 250}.{idx % 250 + 1}"
            service = DeviceService(session)
            settings = SimpleNamespace(
                network_driver=d,
                intent_repo_path=tmp_path,
                auth_profiles={},
            )
            container = SimpleNamespace(settings=settings)
            data = DeviceCreate(
                hostname=hname,
                management_ip=mip,
                management_port=22,
                platform=plat,
                role=role,
                auth_profile="lab",
                username=u,
                password=p,
                hardware_specs='{"chassis": "test"}',
            )
            created = create_device(data, service, container, admin_user)
            assert created.status == DeviceStatus.UNKNOWN, (
                f"Device {hname} had status {created.status} under (user={u}, pass={p}, driver={d}, platform={plat}, role={role})"
            )


def test_adversarial_empty_stand_discovery_with_scrapli_driver(session: Session, tmp_path: Path) -> None:
    """Empty stand discovery with network_driver='scrapli' must NEVER seed dummy 172.20.30.* nodes."""
    stand = Stand(
        slug="scrapli-empty-stand",
        name="Scrapli Empty Stand",
        driver="scrapli",
        jump_host="192.168.100.1",
        jump_port=22,
    )
    session.add(stand)
    session.commit()

    container = MagicMock()
    container.settings.network_driver = "scrapli"
    container.settings.intent_repo_path = tmp_path

    nodes = sync_stand_nodes_from_sot_or_portmap(
        session, container, stand, {}, seed_default_pod=True
    )
    assert nodes == [], f"Expected 0 nodes on empty scrapli stand, got {nodes}"
    devs = session.query(Device).filter(Device.fabric_id == "scrapli-empty-stand").all()
    assert devs == [], f"Expected 0 devices in DB for empty scrapli stand, found {devs}"


def test_adversarial_empty_stand_discovery_with_live_driver(session: Session, tmp_path: Path) -> None:
    """Empty stand discovery with network_driver='live' must NEVER seed dummy 172.20.30.* nodes."""
    stand = Stand(
        slug="live-empty-stand",
        name="Live Empty Stand",
        driver="scrapli",
        jump_host="192.168.100.2",
        jump_port=22,
    )
    session.add(stand)
    session.commit()

    container = MagicMock()
    container.settings.network_driver = "live"
    container.settings.intent_repo_path = tmp_path

    nodes = sync_stand_nodes_from_sot_or_portmap(
        session, container, stand, {}, seed_default_pod=True
    )
    dummy_nodes = [n for n in nodes if "172.20.30." in str(n.get("endpoint", ""))]
    assert dummy_nodes == [], f"Vulnerability detected: dummy static IPs seeded under live driver: {dummy_nodes}"
    assert nodes == [], f"Expected 0 nodes on empty live stand, got {nodes}"


def test_adversarial_empty_stand_discovery_when_stand_driver_is_scrapli(session: Session, tmp_path: Path) -> None:
    """When Stand.driver is 'scrapli', discovery must not seed phantom switches even if container driver is offline."""
    stand = Stand(
        slug="mixed-driver-stand",
        name="Mixed Driver Stand",
        driver="scrapli",
        jump_host="192.168.100.3",
        jump_port=22,
    )
    session.add(stand)
    session.commit()

    container = MagicMock()
    container.settings.network_driver = "offline"
    container.settings.intent_repo_path = tmp_path

    nodes = sync_stand_nodes_from_sot_or_portmap(
        session, container, stand, {}, seed_default_pod=True
    )
    dummy_nodes = [n for n in nodes if "172.20.30." in str(n.get("endpoint", ""))]
    assert dummy_nodes == [], f"Vulnerability detected: stand with driver=scrapli was seeded with phantom nodes: {dummy_nodes}"
