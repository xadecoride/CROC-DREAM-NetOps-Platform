from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from netops.enums import DeviceStatus, UserRole
from netops.models import Device, Stand
from netops.services.stands import resolve_stand_connection
from netops.settings import Settings
from tests.conftest import auth

OWNER = auth(UserRole.OWNER)
ADMIN = auth(UserRole.ADMIN)
VIEWER = auth(UserRole.VIEWER)


def _create_scoped_user(
    client: TestClient, username: str, role: UserRole, allowed_system: str
) -> dict[str, str]:
    resp = client.post(
        "/api/v1/users",
        json={
            "username": username,
            "role": role.value,
            "allowed_system": allowed_system,
        },
        headers=OWNER,
    )
    assert resp.status_code == 201, resp.text
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}


class TestMultiStandLifecycleAndRbac:
    def test_create_two_stands_and_verify_encrypted_secrets(
        self, client: TestClient, session: Session, settings: Settings
    ) -> None:
        alpha_payload = {
            "slug": "stand-alpha",
            "name": "Alpha DC Pod",
            "site": "dc-alpha",
            "driver": "scrapli",
            "jump_host": "198.51.100.11",
            "jump_port": 2221,
            "jump_user": "netadmin",
            "jump_password": "alpha-jump-secret-key",
            "port_map": {"alpha-leaf1.croc.lab": "127.0.0.1:2101"},
            "queue_name": "stand:stand-alpha",
            "description": "Primary test stand Alpha",
        }
        beta_payload = {
            "slug": "stand-beta",
            "name": "Beta Branch Pod",
            "site": "dc-beta",
            "driver": "scrapli",
            "jump_host": "198.51.100.22",
            "jump_port": 2222,
            "jump_user": "branchadmin",
            "jump_password": "beta-jump-secret-key",
            "port_map": {"beta-leaf1.croc.lab": "127.0.0.1:2201"},
            "queue_name": "stand:stand-beta",
            "description": "Secondary test stand Beta",
        }

        r_alpha = client.post("/api/v1/stands", json=alpha_payload, headers=ADMIN)
        assert r_alpha.status_code == 201, r_alpha.text
        data_alpha = r_alpha.json()
        assert data_alpha["slug"] == "stand-alpha"
        assert data_alpha["has_password"] is True
        assert "alpha-jump-secret-key" not in r_alpha.text
        assert data_alpha["port_map"] == {"alpha-leaf1.croc.lab": "127.0.0.1:2101"}

        r_beta = client.post("/api/v1/stands", json=beta_payload, headers=ADMIN)
        assert r_beta.status_code == 201, r_beta.text
        data_beta = r_beta.json()
        assert data_beta["slug"] == "stand-beta"
        assert data_beta["has_password"] is True
        assert "beta-jump-secret-key" not in r_beta.text
        assert data_beta["port_map"] == {"beta-leaf1.croc.lab": "127.0.0.1:2201"}

        # Check DB row encryption
        session.expire_all()
        db_alpha = session.scalar(select(Stand).where(Stand.slug == "stand-alpha"))
        db_beta = session.scalar(select(Stand).where(Stand.slug == "stand-beta"))
        assert db_alpha is not None and db_alpha.encrypted_password is not None
        assert db_beta is not None and db_beta.encrypted_password is not None
        assert db_alpha.encrypted_password.startswith("enc:")
        assert db_beta.encrypted_password.startswith("enc:")
        assert "alpha-jump-secret-key" not in db_alpha.encrypted_password
        assert "beta-jump-secret-key" not in db_beta.encrypted_password

        # Verify independent connection resolution
        conn_alpha = resolve_stand_connection("stand-alpha", settings)
        conn_beta = resolve_stand_connection("stand-beta", settings)
        assert conn_alpha["jump_host"] == "198.51.100.11"
        assert conn_alpha["jump_port"] == 2221
        assert conn_alpha["jump_password"] == "alpha-jump-secret-key"
        assert conn_beta["jump_host"] == "198.51.100.22"
        assert conn_beta["jump_port"] == 2222
        assert conn_beta["jump_password"] == "beta-jump-secret-key"

    def test_stand_device_statistics_and_rbacs(
        self, client: TestClient, session: Session
    ) -> None:
        # 1. Create two stands
        client.post(
            "/api/v1/stands",
            json={
                "slug": "stand-alpha",
                "name": "Alpha Stand",
                "site": "stand-alpha",
                "jump_host": "198.51.100.11",
                "jump_port": 2221,
                "jump_password": "alpha-secret-value",
                "port_map": {"alpha-sw1.croc.lab": "127.0.0.1:2101"},
            },
            headers=ADMIN,
        )
        client.post(
            "/api/v1/stands",
            json={
                "slug": "stand-beta",
                "name": "Beta Stand",
                "site": "stand-beta",
                "jump_host": "198.51.100.22",
                "jump_port": 2222,
                "jump_password": "beta-secret-value",
                "port_map": {"beta-sw1.croc.lab": "127.0.0.1:2201"},
            },
            headers=ADMIN,
        )

        # 2. Add devices to stand-alpha and stand-beta
        dev_a1 = client.post(
            "/api/v1/devices",
            json={
                "hostname": "alpha-sw1.croc.lab",
                "management_ip": "172.20.101.11",
                "platform": "arista_eos",
                "role": "leaf",
                "auth_profile": "lab",
                "fabric_id": "stand-alpha",
                "site": "stand-alpha",
            },
            headers=ADMIN,
        )
        assert dev_a1.status_code == 201
        dev_a2 = client.post(
            "/api/v1/devices",
            json={
                "hostname": "alpha-sw2.croc.lab",
                "management_ip": "172.20.101.12",
                "platform": "arista_eos",
                "role": "spine",
                "auth_profile": "lab",
                "fabric_id": "stand-alpha",
                "site": "stand-alpha",
            },
            headers=ADMIN,
        )
        assert dev_a2.status_code == 201

        dev_b1 = client.post(
            "/api/v1/devices",
            json={
                "hostname": "beta-sw1.croc.lab",
                "management_ip": "172.20.102.11",
                "platform": "cisco_iosxe",
                "role": "border",
                "auth_profile": "lab",
                "fabric_id": "stand-beta",
                "site": "stand-beta",
            },
            headers=ADMIN,
        )
        assert dev_b1.status_code == 201
        beta_dev_id = dev_b1.json()["id"]

        # Set statuses for statistics check
        session.expire_all()
        d1 = session.get_one(Device, dev_a1.json()["id"])
        d2 = session.get_one(Device, dev_a2.json()["id"])
        db1 = session.get_one(Device, beta_dev_id)
        d1.status = DeviceStatus.IN_SYNC
        d2.status = DeviceStatus.DRIFT_DETECTED
        db1.status = DeviceStatus.UNREACHABLE
        session.commit()

        # 3. Verify GET /api/v1/stands statistics
        stands_resp = client.get("/api/v1/stands", headers=VIEWER)
        assert stands_resp.status_code == 200
        by_slug = {s["slug"]: s for s in stands_resp.json()}
        assert "stand-alpha" in by_slug
        assert "stand-beta" in by_slug
        assert by_slug["stand-alpha"]["total_devices"] == 2
        assert by_slug["stand-alpha"]["in_sync_devices"] == 1
        assert by_slug["stand-alpha"]["drifted_devices"] == 1
        assert by_slug["stand-alpha"]["unreachable_devices"] == 0
        assert by_slug["stand-beta"]["total_devices"] == 1
        assert by_slug["stand-beta"]["unreachable_devices"] == 1

        # 4. Strict RBAC isolation for single-stand operator (stand:stand-alpha)
        alpha_headers = _create_scoped_user(
            client, "op-alpha", UserRole.OPERATOR, "stand:stand-alpha"
        )

        # Sees only stand-alpha in GET /api/v1/stands
        scoped_stands = client.get("/api/v1/stands", headers=alpha_headers)
        assert scoped_stands.status_code == 200
        assert [s["slug"] for s in scoped_stands.json()] == ["stand-alpha"]

        # Sees only stand-alpha devices in GET /api/v1/devices
        scoped_devs = client.get("/api/v1/devices", headers=alpha_headers)
        assert scoped_devs.status_code == 200
        assert {d["hostname"] for d in scoped_devs.json()} == {
            "alpha-sw1.croc.lab",
            "alpha-sw2.croc.lab",
        }

        # Allowed to view stand-alpha detail card
        detail_alpha = client.get("/api/v1/stands/stand-alpha", headers=alpha_headers)
        assert detail_alpha.status_code == 200
        assert len(detail_alpha.json()["devices"]) == 2

        # Forbidden from viewing stand-beta detail card
        detail_beta = client.get("/api/v1/stands/stand-beta", headers=alpha_headers)
        assert detail_beta.status_code == 403

        # Forbidden from running jobs or scans on stand-beta device
        dry_beta = client.post(
            "/api/v1/jobs/dry-run",
            json={"device_ids": [beta_dev_id]},
            headers=alpha_headers,
        )
        assert dry_beta.status_code == 403

        scan_beta = client.post(
            "/api/v1/drift/scan",
            json={"fabric_id": "stand-beta"},
            headers=alpha_headers,
        )
        assert scan_beta.status_code == 403

        remediate_beta = client.post(
            "/api/v1/drift/remediate",
            json={"device_id": beta_dev_id},
            headers=alpha_headers,
        )
        assert remediate_beta.status_code == 403

        # 5. Multi-scope user (stand:stand-alpha,stand:stand-beta) sees both stands
        multi_headers = _create_scoped_user(
            client,
            "jury-multi",
            UserRole.VIEWER,
            "stand:stand-alpha,stand:stand-beta",
        )
        multi_stands = client.get("/api/v1/stands", headers=multi_headers)
        assert multi_stands.status_code == 200
        assert {s["slug"] for s in multi_stands.json()} == {"stand-alpha", "stand-beta"}

        multi_devs = client.get("/api/v1/devices", headers=multi_headers)
        assert multi_devs.status_code == 200
        assert {d["hostname"] for d in multi_devs.json()} == {
            "alpha-sw1.croc.lab",
            "alpha-sw2.croc.lab",
            "beta-sw1.croc.lab",
        }

        # 6. Update, activate, discover, and delete stand
        patch_resp = client.patch(
            "/api/v1/stands/stand-beta",
            json={"jump_port": 2299, "jump_password": "rotated-beta-secret"},
            headers=ADMIN,
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["jump_port"] == 2299

        act_resp = client.post("/api/v1/stands/stand-beta/activate", headers=ADMIN)
        assert act_resp.status_code == 200
        assert act_resp.json()["is_active"] is True

        disc_resp = client.post("/api/v1/stands/stand-beta/discover", headers=ADMIN)
        assert disc_resp.status_code == 200
        assert disc_resp.json()["reachable"] is True
        assert len(disc_resp.json()["discovered_nodes"]) == 1

        del_resp = client.delete("/api/v1/stands/stand-beta", headers=ADMIN)
        assert del_resp.status_code == 204
