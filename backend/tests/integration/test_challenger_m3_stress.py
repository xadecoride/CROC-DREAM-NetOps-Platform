"""Adversarial Security Oracle & Stress Test Suite (Challenger M3).

This suite defines the normative security oracles for Milestone 3:
1. Cross-stand RBAC isolation (telemetry, topology, integrations, sync).
2. Strict proxy IP spoofing prevention (including 198.51.100.55 and multi-IP XFF chains).
3. Protection of global topology checkpoints against scoped operator deletion.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from netops.api.routes import devices as devices_route
from netops.api.routes import stands as stands_route
from netops.enums import UserRole
from netops.services.topology_resilience import create_topology_checkpoint
from netops.settings import get_settings
from netops.telemetry import targets as targets_mod
from tests.conftest import auth

OWNER_AUTH = auth(UserRole.OWNER)
ADMIN_AUTH = auth(UserRole.ADMIN)
OPERATOR_AUTH = auth(UserRole.OPERATOR)


@pytest.fixture(autouse=True)
def _fast_telemetry_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    noop = lambda *args, **kwargs: None  # noqa: E731
    monkeypatch.setattr(stands_route, "refresh_telemetry_targets", noop)
    monkeypatch.setattr(devices_route, "refresh_telemetry_targets", noop)
    monkeypatch.setattr(targets_mod, "refresh_telemetry_targets", noop)


def _setup_stands_and_scoped_users(client: TestClient) -> dict[str, str]:
    client.post(
        "/api/v1/stands",
        json={"slug": "stand-alpha", "name": "Alpha Stand", "site": "stand-alpha"},
        headers=ADMIN_AUTH,
    )
    client.post(
        "/api/v1/stands",
        json={"slug": "stand-beta", "name": "Beta Stand", "site": "stand-beta"},
        headers=ADMIN_AUTH,
    )
    client.post(
        "/api/v1/devices",
        json={
            "hostname": "alpha-sw1.croc.lab",
            "management_ip": "172.20.10.1",
            "platform": "arista_eos",
            "role": "leaf",
            "auth_profile": "lab",
            "fabric_id": "stand-alpha",
            "site": "stand-alpha",
        },
        headers=ADMIN_AUTH,
    )
    client.post(
        "/api/v1/devices",
        json={
            "hostname": "beta-sw1.croc.lab",
            "management_ip": "172.20.20.1",
            "platform": "cisco_iosxe",
            "role": "leaf",
            "auth_profile": "lab",
            "fabric_id": "stand-beta",
            "site": "stand-beta",
        },
        headers=ADMIN_AUTH,
    )
    r_op_a = client.post(
        "/api/v1/users",
        json={"username": "op-a-oracle", "role": "operator", "allowed_system": "stand:stand-alpha"},
        headers=OWNER_AUTH,
    )
    r_adm_a = client.post(
        "/api/v1/users",
        json={"username": "adm-a-oracle", "role": "admin", "allowed_system": "stand:stand-alpha"},
        headers=OWNER_AUTH,
    )
    r_op_b = client.post(
        "/api/v1/users",
        json={"username": "op-b-oracle", "role": "operator", "allowed_system": "stand:stand-beta"},
        headers=OWNER_AUTH,
    )
    return {
        "op_a": f"Bearer {r_op_a.json()['token']}",
        "adm_a": f"Bearer {r_adm_a.json()['token']}",
        "op_b": f"Bearer {r_op_b.json()['token']}",
    }


def test_cross_stand_protections_enforced(client: TestClient) -> None:
    """Verify that Stand A operator/admin CANNOT execute foreign stand operations."""
    tokens = _setup_stands_and_scoped_users(client)
    op_a_headers = {"Authorization": tokens["op_a"]}
    adm_a_headers = {"Authorization": tokens["adm_a"]}

    # 1. Telemetry collector mutation on foreign device -> 403
    r_tel = client.put(
        "/api/v1/telemetry/collectors/beta-sw1.croc.lab",
        json={"protocol": "snmp"},
        headers=op_a_headers,
    )
    assert r_tel.status_code == 403

    # 2. Checkpoint restore by scoped operator -> 403
    settings = get_settings()
    cp = create_topology_checkpoint("chk-restore-oracle", settings)
    r_restore = client.post(
        f"/api/v1/topology/checkpoints/{cp['id']}/restore",
        headers=op_a_headers,
    )
    assert r_restore.status_code == 403

    # 3. Foreign stand user tokens in system integrations -> excluded
    r_integ = client.get("/api/v1/system/integrations", headers=adm_a_headers)
    assert r_integ.status_code == 200
    user_tokens = r_integ.json().get("user_tokens", {})
    assert "op-a-oracle" in user_tokens
    assert "op-b-oracle" not in user_tokens
    assert "owner" not in user_tokens

    # 4. Global inventory sync by scoped admin -> 403
    r_inv = client.post("/api/v1/inventory/sync", headers=adm_a_headers)
    assert r_inv.status_code == 403

    # 5. Global git sync by scoped operator -> 403
    r_git = client.post("/api/v1/git/sync", json={"trigger_dry_run": False}, headers=op_a_headers)
    assert r_git.status_code == 403


def test_oracle_rejects_spoofed_198_51_100_55_header(client: TestClient) -> None:
    """Security Oracle: X-Forwarded-For: 198.51.100.55 must be rejected (401)."""
    resp = client.post(
        "/api/v1/auth/login",
        json={"token": "local-owner-console"},
        headers={"Host": "localhost:5173", "X-Forwarded-For": "198.51.100.55"},
    )
    assert resp.status_code == 401, f"Security violation: 198.51.100.55 bypassed owner console! Status: {resp.status_code}"


def test_oracle_rejects_multi_ip_forwarded_for_spoofing(client: TestClient) -> None:
    """Security Oracle: Multi-IP chain with remote client must be rejected (401)."""
    resp = client.post(
        "/api/v1/auth/login",
        json={"token": "local-owner-console"},
        headers={"Host": "localhost:5173", "X-Forwarded-For": "127.0.0.1, 93.184.216.34"},
    )
    assert resp.status_code == 401, f"Security violation: Multi-IP XFF chain bypassed owner console! Status: {resp.status_code}"


def test_oracle_rejects_scoped_operator_checkpoint_deletion(client: TestClient) -> None:
    """Security Oracle: Scoped operator must NOT be allowed to delete global checkpoints (403)."""
    tokens = _setup_stands_and_scoped_users(client)
    op_a_headers = {"Authorization": tokens["op_a"]}

    settings = get_settings()
    cp = create_topology_checkpoint("chk-del-oracle", settings)
    cp_id = cp["id"]

    r_del = client.delete(f"/api/v1/topology/checkpoints/{cp_id}", headers=op_a_headers)
    assert r_del.status_code == 403, f"Security violation: Scoped operator deleted global checkpoint! Status: {r_del.status_code}"


def test_oracle_rejects_scoped_admin_stands_sync_all(client: TestClient) -> None:
    """Security Oracle: Scoped admin must NOT trigger platform-wide /stands/sync-all (403)."""
    tokens = _setup_stands_and_scoped_users(client)
    adm_a_headers = {"Authorization": tokens["adm_a"]}

    r_sync_all = client.post("/api/v1/stands/sync-all", headers=adm_a_headers)
    assert r_sync_all.status_code == 403, f"Security violation: Scoped admin triggered global /stands/sync-all! Status: {r_sync_all.status_code}"


def test_oracle_rejects_scoped_operator_collectors_refresh(client: TestClient) -> None:
    """Security Oracle: Scoped operator must NOT trigger platform-wide /telemetry/collectors/refresh (403)."""
    tokens = _setup_stands_and_scoped_users(client)
    op_a_headers = {"Authorization": tokens["op_a"]}

    r_ref = client.post("/api/v1/telemetry/collectors/refresh", headers=op_a_headers)
    assert r_ref.status_code == 403, f"Security violation: Scoped operator triggered global /telemetry/collectors/refresh! Status: {r_ref.status_code}"


def test_oracle_rejects_hostname_spoofing_starting_with_127(client: TestClient) -> None:
    """Security Oracle: Hostname spoofing starting with 127 must be rejected (401)."""
    for spoofed in ("127.evil.com", "127.0.0.1.attacker.com", "127.0.0.1.nip.io", "::ffff:127.evil.com"):
        resp = client.post(
            "/api/v1/auth/login",
            json={"token": "local-owner-console"},
            headers={"Host": "localhost:5173", "X-Forwarded-For": spoofed},
        )
        assert resp.status_code == 401, f"Security violation: {spoofed} bypassed owner console! Status: {resp.status_code}"



