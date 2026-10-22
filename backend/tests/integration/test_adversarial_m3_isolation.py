"""Comprehensive Adversarial Multi-Stand Isolation & Security Invariant Test Suite.

Verifies:
1. Owner console loopback verification and rejection of remote sockets & spoofed proxy headers.
2. Scoped operator cannot mutate telemetry collector protocol on foreign stand devices.
3. Scoped operator cannot toggle or delete custom links on foreign stands.
4. Scoped operator cannot restore global checkpoints.
5. Scoped admin cannot view foreign stand user tokens in system integrations.
6. Scoped admin/operator cannot trigger global inventory or Git SoT sync.
7. Scoped operator cannot access foreign stand jobs or terminal streams.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from netops.api.routes import devices as devices_route
from netops.api.routes import stands as stands_route
from netops.enums import JobType, UserRole
from netops.models import Job
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
        headers=OWNER_AUTH,
    )
    assert resp.status_code == 201, resp.text
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _setup_two_stands_and_devices(client: TestClient) -> dict[str, Any]:
    # 1. Create Stand Alpha
    client.post(
        "/api/v1/stands",
        json={
            "slug": "stand-alpha",
            "name": "Alpha Stand",
            "site": "stand-alpha",
            "jump_host": "198.51.100.11",
            "jump_port": 2221,
            "jump_password": "alpha-secret",
            "port_map": {
                "alpha-leaf1.croc.lab": "127.0.0.1:2101",
                "alpha-leaf2.croc.lab": "127.0.0.1:2102",
            },
        },
        headers=ADMIN_AUTH,
    )

    # 2. Create Stand Beta
    client.post(
        "/api/v1/stands",
        json={
            "slug": "stand-beta",
            "name": "Beta Stand",
            "site": "stand-beta",
            "jump_host": "198.51.100.22",
            "jump_port": 2222,
            "jump_password": "beta-secret",
            "port_map": {
                "beta-leaf1.croc.lab": "127.0.0.1:2201",
                "beta-leaf2.croc.lab": "127.0.0.1:2202",
            },
        },
        headers=ADMIN_AUTH,
    )

    # 3. Add devices for Stand Alpha
    r_a1 = client.post(
        "/api/v1/devices",
        json={
            "hostname": "alpha-leaf1.croc.lab",
            "management_ip": "172.20.101.11",
            "platform": "arista_eos",
            "role": "leaf",
            "auth_profile": "lab",
            "fabric_id": "stand-alpha",
            "site": "stand-alpha",
        },
        headers=ADMIN_AUTH,
    )
    assert r_a1.status_code == 201, r_a1.text

    r_a2 = client.post(
        "/api/v1/devices",
        json={
            "hostname": "alpha-leaf2.croc.lab",
            "management_ip": "172.20.101.12",
            "platform": "arista_eos",
            "role": "spine",
            "auth_profile": "lab",
            "fabric_id": "stand-alpha",
            "site": "stand-alpha",
        },
        headers=ADMIN_AUTH,
    )
    assert r_a2.status_code == 201, r_a2.text

    # 4. Add devices for Stand Beta
    r_b1 = client.post(
        "/api/v1/devices",
        json={
            "hostname": "beta-leaf1.croc.lab",
            "management_ip": "172.20.102.11",
            "platform": "cisco_iosxe",
            "role": "leaf",
            "auth_profile": "lab",
            "fabric_id": "stand-beta",
            "site": "stand-beta",
        },
        headers=ADMIN_AUTH,
    )
    assert r_b1.status_code == 201, r_b1.text

    r_b2 = client.post(
        "/api/v1/devices",
        json={
            "hostname": "beta-leaf2.croc.lab",
            "management_ip": "172.20.102.12",
            "platform": "cisco_iosxe",
            "role": "spine",
            "auth_profile": "lab",
            "fabric_id": "stand-beta",
            "site": "stand-beta",
        },
        headers=ADMIN_AUTH,
    )
    assert r_b2.status_code == 201, r_b2.text

    return {
        "alpha_leaf1_id": r_a1.json()["id"],
        "alpha_leaf2_id": r_a2.json()["id"],
        "beta_leaf1_id": r_b1.json()["id"],
        "beta_leaf2_id": r_b2.json()["id"],
    }


def test_owner_console_spoofed_proxy_headers_and_remote_ip_rejection(client: TestClient) -> None:
    """Owner loopback console rejects non-loopback IPs and spoofed proxy headers."""
    # 1. Valid local request succeeds
    valid_resp = client.post(
        "/api/v1/auth/login",
        json={"token": "local-owner-console"},
        headers={"Host": "localhost:5173"},
    )
    assert valid_resp.status_code == 200
    assert valid_resp.json()["role"] == "owner"

    # 2. Remote peer socket IP is rejected
    app = client.app
    with TestClient(app, client=("93.184.216.34", 45678)) as remote_client:
        r_remote = remote_client.post(
            "/api/v1/auth/login",
            json={"token": "local-owner-console"},
            headers={"Host": "localhost:5173"},
        )
        assert r_remote.status_code == 401

    # 3. Adversarial spoofed proxy headers are rejected
    adversarial_headers_list = [
        {"Host": "localhost:5173", "X-Forwarded-For": "93.184.216.34"},
        {"Host": "localhost:5173", "CF-Connecting-IP": "8.8.8.8"},
        {"Host": "localhost:5173", "X-Real-IP": "93.184.216.34"},
        {"Host": "localhost:5173", "X-Console-Port": "5174"},
        {"Host": "localhost:5173", "Origin": "https://malicious-attacker.com"},
        {"Host": "localhost:5173", "X-Forwarded-Host": "malicious-attacker.com"},
    ]
    for headers in adversarial_headers_list:
        bad_resp = client.post(
            "/api/v1/auth/login",
            json={"token": "local-owner-console"},
            headers=headers,
        )
        assert bad_resp.status_code == 401, f"Failed for headers: {headers}"


def test_cross_stand_telemetry_collector_mutation_isolation(client: TestClient) -> None:
    """Stand A operator cannot mutate telemetry collector protocol on Stand B device."""
    _setup_two_stands_and_devices(client)
    op_alpha = _create_scoped_user(client, "op-telemetry-a", UserRole.OPERATOR, "stand:stand-alpha")

    # 1. Stand A operator mutates Stand B device -> 403 Forbidden
    r_foreign = client.put(
        "/api/v1/telemetry/collectors/beta-leaf1.croc.lab",
        json={"protocol": "snmp"},
        headers=op_alpha,
    )
    assert r_foreign.status_code == 403, r_foreign.text

    # 2. Mutating non-existent device -> 404 Not Found
    r_nonexistent = client.put(
        "/api/v1/telemetry/collectors/ghost-leaf.croc.lab",
        json={"protocol": "snmp"},
        headers=op_alpha,
    )
    assert r_nonexistent.status_code == 404, r_nonexistent.text

    # 3. Stand A operator mutates Stand A device -> 200 OK
    r_own = client.put(
        "/api/v1/telemetry/collectors/alpha-leaf1.croc.lab",
        json={"protocol": "snmp"},
        headers=op_alpha,
    )
    assert r_own.status_code == 200, r_own.text
    assert r_own.json().get("overrides", {}).get("alpha-leaf1.croc.lab") == "snmp"


def test_cross_stand_topology_mutations_isolation(client: TestClient) -> None:
    """Stand A operator cannot toggle or delete custom links on Stand B."""
    ids = _setup_two_stands_and_devices(client)
    op_alpha = _create_scoped_user(client, "op-topo-a", UserRole.OPERATOR, "stand:stand-alpha")

    # 1. Create custom links: link-alpha-1 in Stand A, link-beta-1 in Stand B
    r_link_a = client.post(
        "/api/v1/topology/links",
        json={
            "id": "link-alpha-1",
            "source_id": ids["alpha_leaf1_id"],
            "target_id": ids["alpha_leaf2_id"],
            "layer": "L3_SUBNET",
            "subnet": "10.10.1.0/31",
            "admin_state": "UP",
        },
        headers=ADMIN_AUTH,
    )
    assert r_link_a.status_code == 201, r_link_a.text

    r_link_b = client.post(
        "/api/v1/topology/links",
        json={
            "id": "link-beta-1",
            "source_id": ids["beta_leaf1_id"],
            "target_id": ids["beta_leaf2_id"],
            "layer": "L3_SUBNET",
            "subnet": "10.10.2.0/31",
            "admin_state": "UP",
        },
        headers=ADMIN_AUTH,
    )
    assert r_link_b.status_code == 201, r_link_b.text

    # 2. Stand A operator tries to toggle Stand B link -> 404 Not Found
    r_toggle_b = client.patch(
        "/api/v1/topology/links/link-beta-1/toggle",
        json={"admin_state": "SHUTDOWN", "reason": "Adversarial cross-stand toggle"},
        headers=op_alpha,
    )
    assert r_toggle_b.status_code == 404, r_toggle_b.text
    assert "not found in allowed stand topology" in r_toggle_b.text

    # 3. Stand A operator tries to delete Stand B link -> 404 Not Found
    r_delete_b = client.delete("/api/v1/topology/links/link-beta-1", headers=op_alpha)
    assert r_delete_b.status_code == 404, r_delete_b.text

    # 4. Stand A operator toggles and deletes Stand A link -> 200 OK
    r_toggle_a = client.patch(
        "/api/v1/topology/links/link-alpha-1/toggle",
        json={"admin_state": "SHUTDOWN", "reason": "Authorized toggle"},
        headers=op_alpha,
    )
    assert r_toggle_a.status_code == 200, r_toggle_a.text
    assert r_toggle_a.json()["admin_state"] == "SHUTDOWN"

    r_delete_a = client.delete("/api/v1/topology/links/link-alpha-1", headers=op_alpha)
    assert r_delete_a.status_code == 200, r_delete_a.text
    assert r_delete_a.json()["deleted"] is True


def test_scoped_operator_cannot_restore_global_checkpoints(client: TestClient) -> None:
    """Scoped operator cannot restore global topology checkpoints."""
    op_alpha = _create_scoped_user(client, "op-ckpt-a", UserRole.OPERATOR, "stand:stand-alpha")

    # 1. Create a checkpoint as admin
    r_cp = client.post(
        "/api/v1/topology/checkpoints",
        json={"name": "m3-adversarial-cp"},
        headers=ADMIN_AUTH,
    )
    assert r_cp.status_code == 201, r_cp.text
    cp_id = r_cp.json()["id"]

    # 2. Scoped operator attempts restore -> 403 Forbidden
    r_restore_scoped = client.post(
        f"/api/v1/topology/checkpoints/{cp_id}/restore",
        headers=op_alpha,
    )
    assert r_restore_scoped.status_code == 403, r_restore_scoped.text
    assert "Restoring global checkpoints requires platform admin" in r_restore_scoped.text

    # 3. Unscoped admin restores successfully -> 200 OK
    r_restore_admin = client.post(
        f"/api/v1/topology/checkpoints/{cp_id}/restore",
        headers=ADMIN_AUTH,
    )
    assert r_restore_admin.status_code == 200, r_restore_admin.text


def test_scoped_admin_cannot_view_foreign_stand_user_tokens(client: TestClient) -> None:
    """Stand A admin cannot view Stand B user tokens in system integrations."""
    adm_alpha = _create_scoped_user(client, "adm-alpha-tok", UserRole.ADMIN, "stand:stand-alpha")
    _create_scoped_user(client, "user-alpha-peer", UserRole.OPERATOR, "stand:stand-alpha")
    _create_scoped_user(client, "user-beta-foreign", UserRole.OPERATOR, "stand:stand-beta")

    # Scoped Stand A admin queries integrations
    resp = client.get("/api/v1/system/integrations", headers=adm_alpha)
    assert resp.status_code == 200, resp.text
    tokens = resp.json().get("user_tokens") or {}

    # Must contain Stand A peer, must NOT contain Stand B user or owner
    assert "user-alpha-peer" in tokens
    assert "user-beta-foreign" not in tokens
    assert "owner" not in tokens


def test_scoped_admin_and_operator_cannot_trigger_global_sync(client: TestClient) -> None:
    """Scoped admin and operator cannot trigger platform-wide inventory or Git SoT sync."""
    adm_alpha = _create_scoped_user(client, "adm-sync-a", UserRole.ADMIN, "stand:stand-alpha")
    op_alpha = _create_scoped_user(client, "op-sync-a", UserRole.OPERATOR, "stand:stand-alpha")

    # 1. Scoped admin calls inventory sync -> 403 Forbidden
    r_inv = client.post("/api/v1/inventory/sync", headers=adm_alpha)
    assert r_inv.status_code == 403, r_inv.text
    assert "Global inventory/Git SoT sync requires platform admin or owner" in r_inv.text

    # 2. Scoped operator calls git sync -> 403 Forbidden
    r_git = client.post("/api/v1/git/sync", json={"trigger_dry_run": False}, headers=op_alpha)
    assert r_git.status_code == 403, r_git.text
    assert "Global inventory/Git SoT sync requires platform admin or owner" in r_git.text

    # 3. Unscoped admin can trigger inventory sync -> 200 OK
    r_inv_ok = client.post("/api/v1/inventory/sync", headers=ADMIN_AUTH)
    assert r_inv_ok.status_code == 200, r_inv_ok.text

    # 4. Unscoped operator can trigger git sync -> 200 OK
    r_git_ok = client.post("/api/v1/git/sync", json={"trigger_dry_run": False}, headers=OPERATOR_AUTH)
    assert r_git_ok.status_code == 200, r_git_ok.text


def test_cross_stand_jobs_and_terminal_stream_isolation(
    client: TestClient, session: Session
) -> None:
    """Stand A operator cannot access Stand B jobs, logs, diffs, or terminal streams."""
    ids = _setup_two_stands_and_devices(client)
    op_alpha = _create_scoped_user(client, "op-jobs-a", UserRole.OPERATOR, "stand:stand-alpha")

    # 1. Create Job A (Stand A device) and Job B (Stand B device)
    r_job_a = client.post(
        "/api/v1/jobs/dry-run",
        json={"device_ids": [ids["alpha_leaf1_id"]]},
        headers=OPERATOR_AUTH,
    )
    assert r_job_a.status_code == 202, r_job_a.text
    job_a_id = r_job_a.json()["job_id"]

    r_job_b = client.post(
        "/api/v1/jobs/dry-run",
        json={"device_ids": [ids["beta_leaf1_id"]]},
        headers=OPERATOR_AUTH,
    )
    assert r_job_b.status_code == 202, r_job_b.text
    job_b_id = r_job_b.json()["job_id"]

    # 2. Create Job Empty (no targets)
    empty_job = Job(type=JobType.DRY_RUN, created_by="test-system")
    session.add(empty_job)
    session.commit()
    empty_job_id = str(empty_job.id)

    # 3. Operator A accessing Job B endpoints -> 403 Forbidden
    for ep in (
        f"/api/v1/jobs/{job_b_id}",
        f"/api/v1/jobs/{job_b_id}/logs",
        f"/api/v1/jobs/{job_b_id}/diff",
        f"/api/v1/jobs/{job_b_id}/stream?once=true",
    ):
        resp = client.get(ep, headers=op_alpha)
        assert resp.status_code == 403, f"Expected 403 for {ep}, got {resp.status_code}: {resp.text}"

    # Explain endpoint for Job B -> 403 Forbidden
    r_explain = client.post(f"/api/v1/jobs/{job_b_id}/explain", headers=op_alpha)
    assert r_explain.status_code == 403, r_explain.text

    # 4. Operator A accessing Empty Job -> 403 Forbidden
    r_empty = client.get(f"/api/v1/jobs/{empty_job_id}", headers=op_alpha)
    assert r_empty.status_code == 403, r_empty.text

    # 5. Operator A accessing Job A -> 200 OK
    r_job_a_get = client.get(f"/api/v1/jobs/{job_a_id}", headers=op_alpha)
    assert r_job_a_get.status_code == 200, r_job_a_get.text
    r_job_a_logs = client.get(f"/api/v1/jobs/{job_a_id}/logs", headers=op_alpha)
    assert r_job_a_logs.status_code == 200, r_job_a_logs.text
