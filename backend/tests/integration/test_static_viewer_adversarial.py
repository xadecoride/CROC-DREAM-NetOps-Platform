"""Empirical Adversarial Security & RBAC Verification for Static Multi-Viewer (R1).

Challenger 2 Verification Suite:
1. Mutation blocking on all state-changing endpoints (POST/PUT/PATCH/DELETE).
2. Protection of static-viewer management endpoints (rotate, toggle, status).
3. Secret leakage audit (passwords, tokens, verification codes, Telegram chat IDs).
4. System account immutability (static_viewer cannot be modified or deleted via user APIs).
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from netops.enums import UserRole
from netops.models import Device, User
from tests.conftest import auth

ADMIN_HEADERS = auth(UserRole.ADMIN)


def _get_static_viewer_session(client: TestClient) -> tuple[TestClient, dict[str, str]]:
    """Helper to initialize and authenticate a static viewer session."""
    res = client.get("/api/v1/auth/static-viewer", headers=ADMIN_HEADERS)
    assert res.status_code == 200, res.text
    token = res.json()["token"]

    viewer_client = TestClient(client.app)
    login_res = viewer_client.post(
        "/api/v1/auth/login",
        json={"token": token},
        headers={"X-Client-ID": "adversarial-probe-device"},
    )
    assert login_res.status_code == 200, login_res.text
    csrf = login_res.json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf}
    return viewer_client, headers


def test_adversarial_static_viewer_mutation_endpoints(
    client: TestClient, devices: dict[str, Device], session_factory: Session
) -> None:
    """Stress-test mutating POST/PUT/PATCH/DELETE endpoints with static viewer session."""
    viewer_client, headers = _get_static_viewer_session(client)

    # Fetch a device ID for targeted mutations
    devs_res = viewer_client.get("/api/v1/devices")
    assert devs_res.status_code == 200
    dev_list = devs_res.json()
    assert len(dev_list) > 0, "Expected devices from devices fixture"
    dev_id = dev_list[0]["id"]
    hostname = dev_list[0]["hostname"]

    # 1. Jobs: Deploy & Dry-run
    r_deploy = viewer_client.post(
        "/api/v1/jobs/deploy",
        json={"job_id": "00000000-0000-0000-0000-000000000000"},
        headers=headers,
    )
    assert r_deploy.status_code == 403, f"Deploy returned {r_deploy.status_code}: {r_deploy.text}"

    r_dry_run = viewer_client.post(
        "/api/v1/jobs/dry-run",
        json={"device_ids": [dev_id]},
        headers=headers,
    )
    assert r_dry_run.status_code == 403, f"Dry-run returned {r_dry_run.status_code}: {r_dry_run.text}"

    # 2. Chaos Injection
    r_chaos = viewer_client.post(
        "/api/v1/system/chaos",
        json={"scenario": "latency_spike", "target_hostname": hostname},
        headers=headers,
    )
    assert r_chaos.status_code == 403, f"Chaos returned {r_chaos.status_code}: {r_chaos.text}"

    # 3. Drift: Scan & Remediate
    r_scan = viewer_client.post(
        "/api/v1/drift/scan",
        json={"device_ids": [dev_id]},
        headers=headers,
    )
    assert r_scan.status_code == 403, f"Drift scan returned {r_scan.status_code}: {r_scan.text}"

    r_remediate = viewer_client.post(
        "/api/v1/drift/remediate",
        json={"device_id": dev_id},
        headers=headers,
    )
    assert r_remediate.status_code == 403, f"Drift remediate returned {r_remediate.status_code}: {r_remediate.text}"

    # 4. Devices CRUD
    r_dev_create = viewer_client.post(
        "/api/v1/devices",
        json={
            "hostname": "adversary-leaf.lab",
            "management_ip": "10.255.255.99",
            "platform": "arista_eos",
            "role": "leaf",
        },
        headers=headers,
    )
    assert r_dev_create.status_code == 403, f"Device create returned {r_dev_create.status_code}"

    r_dev_update = viewer_client.patch(
        f"/api/v1/devices/{dev_id}",
        json={"role": "spine"},
        headers=headers,
    )
    assert r_dev_update.status_code == 403, f"Device update returned {r_dev_update.status_code}"

    r_dev_del = viewer_client.delete(f"/api/v1/devices/{dev_id}", headers=headers)
    assert r_dev_del.status_code == 403, f"Device delete returned {r_dev_del.status_code}"

    r_dev_em = viewer_client.post(
        f"/api/v1/devices/{dev_id}/emergency-action",
        json={"action": "reboot"},
        headers=headers,
    )
    assert r_dev_em.status_code == 403, f"Emergency action returned {r_dev_em.status_code}"

    r_dev_promote = viewer_client.post(
        f"/api/v1/devices/{dev_id}/promote",
        headers=headers,
    )
    assert r_dev_promote.status_code == 403, f"Device promote returned {r_dev_promote.status_code}"

    # 5. Topology mutations: Toggle link & Node drain
    r_toggle_patch = viewer_client.patch(
        "/api/v1/topology/links/link-01/toggle",
        json={"admin_state": "SHUTDOWN"},
        headers=headers,
    )
    assert r_toggle_patch.status_code == 403, f"Toggle link patch returned {r_toggle_patch.status_code}"

    r_drain = viewer_client.post(
        f"/api/v1/topology/nodes/{dev_id}/drain",
        json={"drained": True},
        headers=headers,
    )
    assert r_drain.status_code == 403, f"Node drain returned {r_drain.status_code}"

    r_checkpoint = viewer_client.post(
        "/api/v1/topology/checkpoints",
        json={"name": "Attacker Checkpoint"},
        headers=headers,
    )
    assert r_checkpoint.status_code == 403, f"Checkpoint returned {r_checkpoint.status_code}"

    # 6. Stands CRUD & Sync
    r_stand_create = viewer_client.post(
        "/api/v1/stands",
        json={"slug": "rogue_stand", "name": "Rogue Stand"},
        headers=headers,
    )
    assert r_stand_create.status_code == 403, f"Stand create returned {r_stand_create.status_code}"

    r_stand_del = viewer_client.delete("/api/v1/stands/test_slug", headers=headers)
    assert r_stand_del.status_code == 403, f"Stand delete returned {r_stand_del.status_code}"

    r_stand_probe = viewer_client.post(
        "/api/v1/stands/probe",
        json={"jump_host": "1.2.3.4", "jump_port": 22},
        headers=headers,
    )
    assert r_stand_probe.status_code == 403, f"Stand probe returned {r_stand_probe.status_code}"

    # 7. User management mutations
    r_user_create = viewer_client.post(
        "/api/v1/users",
        json={"username": "rogue_user", "role": "admin"},
        headers=headers,
    )
    assert r_user_create.status_code == 403, f"User create returned {r_user_create.status_code}"

    r_user_batch = viewer_client.post(
        "/api/v1/users/batch",
        json={"prefix": "rogue", "count": 2},
        headers=headers,
    )
    assert r_user_batch.status_code == 403, f"User batch returned {r_user_batch.status_code}"

    # 8. Emergency direct interventions
    r_em_cmd = viewer_client.post(
        "/api/v1/emergency/command",
        json={"device": hostname, "command": "show version"},
        headers=headers,
    )
    assert r_em_cmd.status_code == 403, f"Emergency command returned {r_em_cmd.status_code}"

    r_em_patch = viewer_client.post(
        "/api/v1/emergency/patch",
        json={"device": hostname, "patch": "hostname hacked"},
        headers=headers,
    )
    assert r_em_patch.status_code == 403, f"Emergency patch returned {r_em_patch.status_code}"

    r_em_soft = viewer_client.post("/api/v1/emergency/soft-reset", headers=headers)
    assert r_em_soft.status_code == 403, f"Emergency soft-reset returned {r_em_soft.status_code}"

    # 9. Factory freeze
    r_freeze = viewer_client.post(
        "/api/v1/system/freeze",
        json={"frozen": True, "reason": "attack"},
        headers=headers,
    )
    assert r_freeze.status_code == 403, f"Freeze returned {r_freeze.status_code}"

    # 10. Intent edit
    r_intent = viewer_client.put(
        f"/api/v1/devices/{dev_id}/intent-source",
        json={"yaml": "hostname: rogue\n"},
        headers=headers,
    )
    assert r_intent.status_code == 403, f"Intent save returned {r_intent.status_code}"

    # 11. Git sync & commit
    r_git_sync = viewer_client.post("/api/v1/git/sync", headers=headers)
    assert r_git_sync.status_code == 403, f"Git sync returned {r_git_sync.status_code}"


def test_adversarial_stand_sync_all_rbac(client: TestClient) -> None:
    """Audit POST /api/v1/stands/sync-all: Must strictly require Operator or Admin, not Viewer."""
    viewer_client, headers = _get_static_viewer_session(client)
    r_sync_all = viewer_client.post("/api/v1/stands/sync-all", headers=headers)
    # Check if stands/sync-all correctly blocks viewer with 403
    print("SYNC-ALL response status:", r_sync_all.status_code)
    assert r_sync_all.status_code == 403, (
        f"VULNERABILITY: POST /api/v1/stands/sync-all allowed Viewer execution! "
        f"Status: {r_sync_all.status_code}, Response: {r_sync_all.text[:200]}"
    )


def test_adversarial_static_viewer_management_endpoints(client: TestClient) -> None:
    """Verify static viewer cannot call /rotate, /toggle, or status."""
    viewer_client, headers = _get_static_viewer_session(client)

    # 1. GET /static-viewer
    r_get = viewer_client.get("/api/v1/auth/static-viewer")
    assert r_get.status_code == 403, f"GET static-viewer returned {r_get.status_code}"

    # 2. POST /static-viewer/rotate
    r_rot = viewer_client.post("/api/v1/auth/static-viewer/rotate", headers=headers)
    assert r_rot.status_code == 403, f"POST static-viewer/rotate returned {r_rot.status_code}"

    # 3. POST /static-viewer/toggle
    r_tog = viewer_client.post(
        "/api/v1/auth/static-viewer/toggle",
        json={"enabled": False},
        headers=headers,
    )
    assert r_tog.status_code == 403, f"POST static-viewer/toggle returned {r_tog.status_code}"


def test_adversarial_secret_leakage_audit(client: TestClient) -> None:
    """Audit all responses readable by static viewer for secret leakage."""
    viewer_client, _ = _get_static_viewer_session(client)

    # 1. /api/v1/users (Listing users)
    r_users = viewer_client.get("/api/v1/users")
    assert r_users.status_code == 403, f"/users should be forbidden to viewer, got {r_users.status_code}"

    # 2. /api/v1/users/me (Own profile)
    r_me = viewer_client.get("/api/v1/users/me")
    assert r_me.status_code == 200
    me_data = r_me.json()
    assert "token_sha256" not in me_data
    assert "token" not in me_data

    # 3. /api/v1/stands (Stands list and details)
    r_stands = viewer_client.get("/api/v1/stands")
    assert r_stands.status_code == 200
    stands = r_stands.json()
    for s in stands:
        assert "password" not in s
        assert "jump_password" not in s
        assert "has_password" in s

    if stands:
        slug = stands[0]["slug"]
        r_stand_detail = viewer_client.get(f"/api/v1/stands/{slug}")
        assert r_stand_detail.status_code == 200
        detail = r_stand_detail.json()
        assert "password" not in detail
        assert "jump_password" not in detail

    # 4. /api/v1/system/bootstrap/status
    r_boot = viewer_client.get("/api/v1/system/bootstrap/status")
    assert r_boot.status_code == 200
    boot_data = r_boot.json()
    assert boot_data.get("caller_is_bound_owner") is False
    assert boot_data.get("owner", {}).get("recovery_key") is None
    assert boot_data.get("owner", {}).get("telegram_chat_id") is None
    assert boot_data.get("telegram", {}).get("chat_ids") == []

    # Check LLM keys are not exposed
    ai_cascade = boot_data.get("ai", {}).get("cascade", [])
    for item in ai_cascade:
        assert not item.get("api_key") or item.get("api_key") == "", (
            f"API key leaked in AI cascade: {item}"
        )

    # 5. /api/v1/system/integrations (Integrations status)
    r_integ = viewer_client.get("/api/v1/system/integrations")
    assert r_integ.status_code == 200
    integ_data = r_integ.json()
    assert integ_data.get("user_tokens") == {}
    assert integ_data.get("sessions") == []
    assert integ_data.get("access_registry") == {}


def test_adversarial_telegram_chat_ids_leak_in_integrations(client: TestClient) -> None:
    """Audit whether configured Telegram chat IDs leak to viewers in /api/v1/system/integrations."""
    # Configure telegram targets on settings
    container = client.app.state.container
    orig_chat_ids = container.settings.telegram_chat_ids
    orig_chat_id = container.settings.telegram_chat_id
    try:
        container.settings.telegram_chat_ids = ["987654321"]
        container.settings.telegram_chat_id = "123456789"

        viewer_client, _ = _get_static_viewer_session(client)
        r_integ = viewer_client.get("/api/v1/system/integrations")
        assert r_integ.status_code == 200
        tg_info = r_integ.json().get("telegram", {})
        chat_ids = tg_info.get("chat_ids", [])
        print("Integrations telegram chat_ids for viewer:", chat_ids)
        assert chat_ids == [], (
            f"VULNERABILITY: /api/v1/system/integrations leaked Telegram chat IDs {chat_ids} to viewer session!"
        )
    finally:
        container.settings.telegram_chat_ids = orig_chat_ids
        container.settings.telegram_chat_id = orig_chat_id


def test_adversarial_static_viewer_account_immutability(
    client: TestClient, session_factory: Session
) -> None:
    """Verify that static_viewer system account cannot be mutated/deleted via user APIs."""
    # Ensure static_viewer exists in DB
    client.get("/api/v1/auth/static-viewer", headers=ADMIN_HEADERS)

    with session_factory() as sess:
        user = sess.scalar(select(User).where(User.username == "static_viewer"))
        assert user is not None
        sv_id = user.id

    # 1. DELETE /users/{id}
    r_del = client.delete(f"/api/v1/users/{sv_id}", headers=ADMIN_HEADERS)
    assert r_del.status_code == 409, f"DELETE /users/{sv_id} returned {r_del.status_code}"

    # 2. PATCH /users/{id}
    r_patch = client.patch(
        f"/api/v1/users/{sv_id}",
        json={"role": "admin"},
        headers=ADMIN_HEADERS,
    )
    assert r_patch.status_code == 409, f"PATCH /users/{sv_id} returned {r_patch.status_code}"

    # 3. POST /users/{id}/token
    r_tok = client.post(f"/api/v1/users/{sv_id}/token", headers=ADMIN_HEADERS)
    assert r_tok.status_code == 409, f"POST /users/{sv_id}/token returned {r_tok.status_code}"

    # 4. POST /users/{id}/rotate
    r_rot = client.post(f"/api/v1/users/{sv_id}/rotate", headers=ADMIN_HEADERS)
    assert r_rot.status_code == 409, f"POST /users/{sv_id}/rotate returned {r_rot.status_code}"

    # 5. POST /users/{id}/unbind
    r_unbind = client.post(f"/api/v1/users/{sv_id}/unbind", headers=ADMIN_HEADERS)
    assert r_unbind.status_code == 409, f"POST /users/{sv_id}/unbind returned {r_unbind.status_code}"
