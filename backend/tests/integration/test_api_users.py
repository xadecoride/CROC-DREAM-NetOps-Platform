from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from netops.enums import UserRole
from netops.models import User
from netops.security import hash_token
from tests.conftest import auth

ADMIN = auth(UserRole.ADMIN)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create(client: TestClient, username: str = "duty", role: str = "operator") -> dict[str, Any]:
    response = client.post(
        "/api/v1/users", json={"username": username, "role": role}, headers=ADMIN
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_created_user_can_authenticate(client: TestClient) -> None:
    user = _create(client)
    assert user["role"] == "operator"
    assert user["is_active"] is True
    assert len(user["token"]) >= 40

    me = client.get("/api/v1/auth/me", headers=_bearer(user["token"]))
    assert me.json() == {"username": "duty", "role": "operator"}
    assert client.get("/api/v1/users", headers=_bearer(user["token"])).status_code == 403


def test_only_a_hash_of_the_token_is_stored(client: TestClient, session: Session) -> None:
    user = _create(client)
    stored = session.get_one(User, user["id"])
    assert stored.token_sha256 == hash_token(user["token"])
    assert user["token"] not in stored.token_sha256
    listed = client.get("/api/v1/users", headers=ADMIN).json()
    assert "token" not in listed[0]


def test_list_and_get(client: TestClient) -> None:
    _create(client, "zoe", "viewer")
    _create(client, "amir", "admin")
    assert [u["username"] for u in client.get("/api/v1/users", headers=ADMIN).json()] == [
        "amir",
        "zoe",
    ]
    user_id = _create(client, "kim")["id"]
    assert client.get(f"/api/v1/users/{user_id}", headers=ADMIN).json()["username"] == "kim"
    assert client.get("/api/v1/users/999", headers=ADMIN).status_code == 404


def test_role_change_takes_effect_immediately(client: TestClient) -> None:
    user = _create(client, role="viewer")
    response = client.patch(f"/api/v1/users/{user['id']}", json={"role": "admin"}, headers=ADMIN)
    assert response.json()["role"] == "admin"
    assert client.get("/api/v1/users", headers=_bearer(user["token"])).status_code == 200


def test_disabled_user_is_rejected(client: TestClient) -> None:
    user = _create(client)
    client.patch(f"/api/v1/users/{user['id']}", json={"is_active": False}, headers=ADMIN)
    assert client.get("/api/v1/auth/me", headers=_bearer(user["token"])).status_code == 401

    client.patch(f"/api/v1/users/{user['id']}", json={"is_active": True}, headers=ADMIN)
    assert client.get("/api/v1/auth/me", headers=_bearer(user["token"])).status_code == 200


def test_token_rotation(client: TestClient) -> None:
    user = _create(client)
    rotated = client.post(f"/api/v1/users/{user['id']}/token", headers=ADMIN).json()
    assert rotated["token"] != user["token"]
    assert client.get("/api/v1/auth/me", headers=_bearer(user["token"])).status_code == 401
    assert client.get("/api/v1/auth/me", headers=_bearer(rotated["token"])).status_code == 200


def test_delete(client: TestClient) -> None:
    user = _create(client)
    assert client.delete(f"/api/v1/users/{user['id']}", headers=ADMIN).status_code == 204
    assert client.get("/api/v1/auth/me", headers=_bearer(user["token"])).status_code == 401
    assert client.get(f"/api/v1/users/{user['id']}", headers=ADMIN).status_code == 404


def test_admin_cannot_lock_themselves_out(client: TestClient) -> None:
    admin = _create(client, "boss", "admin")
    own = _bearer(admin["token"])
    url = f"/api/v1/users/{admin['id']}"

    assert client.patch(url, json={"role": "viewer"}, headers=own).status_code == 409
    assert client.patch(url, json={"is_active": False}, headers=own).status_code == 409
    assert client.delete(url, headers=own).status_code == 409
    assert client.post(f"{url}/token", headers=own).status_code == 200


def test_duplicate_and_reserved_usernames(client: TestClient) -> None:
    _create(client, "duty")
    duplicate = client.post("/api/v1/users", json={"username": "duty"}, headers=ADMIN)
    assert duplicate.status_code == 409
    reserved = client.post("/api/v1/users", json={"username": "admin-user"}, headers=ADMIN)
    assert reserved.status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"username": "has space"},
        {"username": ""},
        {"username": "x", "role": "root"},
        {"username": "x", "password": "nope"},
    ],
)
def test_create_validation(client: TestClient, body: dict[str, object]) -> None:
    assert client.post("/api/v1/users", json=body, headers=ADMIN).status_code == 422


def test_update_validation(client: TestClient) -> None:
    user = _create(client)
    url = f"/api/v1/users/{user['id']}"
    assert client.patch(url, json={"role": None}, headers=ADMIN).status_code == 422
    assert client.patch(url, json={"username": "renamed"}, headers=ADMIN).status_code == 422


def test_batch_invites_and_single_device_binding(client: TestClient) -> None:
    batch_res = client.post(
        "/api/v1/users/batch",
        json={"prefix": "jury", "role": "viewer", "count": 3},
        headers=ADMIN,
    )
    assert batch_res.status_code == 201, batch_res.text
    created = batch_res.json()
    assert len(created) == 3
    assert [u["username"] for u in created] == ["jury-1", "jury-2", "jury-3"]

    first = created[0]
    token = first["token"]

    # First device binds the token
    res_a = client.get(
        "/api/v1/auth/me",
        headers={**_bearer(token), "X-Client-ID": "device-alpha", "User-Agent": "Firefox/Linux"},
    )
    assert res_a.status_code == 200
    assert res_a.json() == {"username": "jury-1", "role": "viewer"}

    # Second device attempting to use the same link is rejected with 403
    res_b = client.get(
        "/api/v1/auth/me",
        headers={**_bearer(token), "X-Client-ID": "device-beta"},
    )
    assert res_b.status_code == 403

    # Admin unbinds the device, allowing device-beta to connect
    unbind_res = client.post(f"/api/v1/users/{first['id']}/unbind", headers=ADMIN)
    assert unbind_res.status_code == 200
    assert unbind_res.json()["bound_client_id"] is None

    res_b_after = client.get(
        "/api/v1/auth/me",
        headers={**_bearer(token), "X-Client-ID": "device-beta"},
    )
    assert res_b_after.status_code == 200


def test_allowed_system_restricts_device_visibility_and_records_session_telemetry(
    client: TestClient,
    devices: dict[str, Any],
) -> None:

    res = client.post(
        "/api/v1/users",
        json={
            "username": "spine1-engineer",
            "role": "operator",
            "allowed_system": "spine-1.croc.lab",
        },
        headers=ADMIN,
    )
    assert res.status_code == 201, res.text
    created = res.json()
    assert created["allowed_system"] == "spine-1.croc.lab"
    token = created["token"]

    headers = {
        **_bearer(token),
        "X-Client-ID": "laptop-spine1",
        "X-Forwarded-For": "10.20.30.40",
        "User-Agent": "Chrome/125.0 Linux",
    }

    # User only sees spine-1.croc.lab in /api/v1/devices
    devs = client.get("/api/v1/devices", headers=headers)
    assert devs.status_code == 200
    dev_list = devs.json()
    assert len(dev_list) == 1
    assert dev_list[0]["hostname"] == "spine-1.croc.lab"

    # Trying to access another device directly returns 403
    all_devs = client.get("/api/v1/devices", headers=ADMIN).json()
    other_dev = next(d for d in all_devs if d["hostname"] != "spine-1.croc.lab")
    forbidden = client.get(f"/api/v1/devices/{other_dev['id']}", headers=headers)
    assert forbidden.status_code == 403

    # Owner inspects /api/v1/users and sees bound_ip, bound_client_id, last_seen_at
    profile = client.get(f"/api/v1/users/{created['id']}", headers=ADMIN).json()
    assert profile["bound_client_id"] == "laptop-spine1"
    # A client-supplied X-Forwarded-For must NOT be trusted (spoofable); only the socket peer
    # (or uvicorn's trusted-proxy resolution) counts.
    assert profile["bound_ip"] == "testclient"
    assert profile["last_seen_at"] is not None

    # /api/v1/users/me returns the full user profile
    me_full = client.get("/api/v1/users/me", headers=headers).json()
    assert me_full["username"] == "spine1-engineer"
    assert me_full["allowed_system"] == "spine-1.croc.lab"

    # Owner changes allowed_system to null (whole fabric)
    patched = client.patch(
        f"/api/v1/users/{created['id']}",
        json={"allowed_system": None},
        headers=ADMIN,
    )
    assert patched.status_code == 200
    assert patched.json()["allowed_system"] is None
    assert len(client.get("/api/v1/devices", headers=headers).json()) == 4


def test_dev_client_id_strict_single_device_lock_blocks_second_person(
    client: TestClient,
) -> None:
    """Verify that browser-generated dev-* client IDs strictly enforce 1-link = 1-device."""
    res = client.post(
        "/api/v1/users",
        json={"username": "timofey-link", "role": "admin", "expires_in_minutes": 60},
        headers=ADMIN,
    )
    assert res.status_code == 201
    created = res.json()
    token = created["token"]
    assert created["expires_at"] is not None
    assert created["verification_code"] is not None
    assert len(created["verification_code"]) == 6

    # First person (Owner) opens the link from browser dev-owner111
    first_login = client.get(
        "/api/v1/auth/me",
        headers={**_bearer(token), "X-Client-ID": "dev-owner111"},
    )
    assert first_login.status_code == 200

    # Second person (Timofey) tries to open the exact same link from browser dev-timofey222
    second_login = client.get(
        "/api/v1/auth/me",
        headers={**_bearer(token), "X-Client-ID": "dev-timofey222"},
    )
    assert second_login.status_code == 403


def test_token_ttl_and_verification_code_generation(
    client: TestClient,
    session: Session,
) -> None:
    from datetime import UTC, datetime, timedelta

    res = client.post(
        "/api/v1/users",
        json={"username": "temp-operator", "role": "operator", "expires_in_minutes": 15},
        headers=ADMIN,
    )
    assert res.status_code == 201
    data = res.json()
    user_id = data["id"]
    token = data["token"]

    # Generate a fresh 6-digit Telegram verification code
    vc_res = client.post(f"/api/v1/users/{user_id}/verify-code", headers=ADMIN)
    assert vc_res.status_code == 200
    new_code = vc_res.json()["verification_code"]
    assert new_code is not None and len(new_code) == 6

    # Simulate token expiration in DB
    db_user = session.get_one(User, user_id)
    db_user.expires_at = datetime.now(UTC) - timedelta(minutes=5)
    session.commit()

    expired_check = client.get("/api/v1/auth/me", headers=_bearer(token))
    assert expired_check.status_code == 403

    # Switch token to forever (expires_in_minutes = 0)
    forever_res = client.patch(
        f"/api/v1/users/{user_id}",
        json={"expires_in_minutes": 0},
        headers=ADMIN,
    )
    assert forever_res.status_code == 200
    assert forever_res.json()["expires_at"] is None
    assert client.get("/api/v1/auth/me", headers=_bearer(token)).status_code == 200


def test_matryoshka_bootstrap_and_owner_pc_recovery(
    client: TestClient,
) -> None:
    # Step 0: Check public bootstrap status before initialization
    st0 = client.get("/api/v1/system/bootstrap/status", headers={"X-Client-Id": "pc-owner-1"})
    assert st0.status_code == 200
    assert st0.json()["initialized"] is False

    # Step 1: Owner claims cluster on PC #1
    claim = client.post(
        "/api/v1/system/bootstrap/claim-owner",
        json={"cluster_name": "CROC-DOCKER-CLUSTER-1"},
        headers={"X-Client-Id": "pc-owner-1"},
    )
    assert claim.status_code == 200
    claim_data = claim.json()
    assert claim_data["status"] == "claimed"
    assert claim_data["bound_client_id"] == "pc-owner-1"
    recovery_key = claim_data["recovery_key"]
    assert recovery_key.startswith("crk_owner_")
    # Claim opens an HttpOnly owner session cookie and returns the CSRF token.
    assert "netops_sid" in claim.cookies
    assert "httponly" in claim.headers["set-cookie"].lower()
    csrf = {"X-CSRF-Token": claim_data["csrf_token"]}

    cfg_payload = {
        "cluster_name": "CROC-DOCKER-CLUSTER-1",
        "github_repo_url": "https://github.com/Vazilll/CROC-DREAM-NetOps-Platform",
        "bootstrap_completed": True,
    }
    # Session cookie without CSRF header is rejected.
    assert client.post("/api/v1/system/bootstrap/configure", json=cfg_payload).status_code == 403

    # Step 2 & 3: Owner configures GitHub, Telegram & marks bootstrap_completed
    cfg = client.post("/api/v1/system/bootstrap/configure", json=cfg_payload, headers=csrf)
    assert cfg.status_code == 200

    # Step 4: Discover / unpack server in offline mode
    disc = client.post(
        "/api/v1/system/bootstrap/discover-server",
        json={"mode": "offline"},
        headers=csrf,
    )
    assert disc.status_code == 200
    assert disc.json()["synced_total"] == 4

    # Owner's own session sees the recovery key; anonymous callers never do.
    assert client.get("/api/v1/system/bootstrap/status").json()["owner"]["recovery_key"]
    client.cookies.clear()
    anon_status = client.get("/api/v1/system/bootstrap/status").json()
    assert anon_status["owner"]["recovery_key"] is None
    assert recovery_key not in anon_status["telegram"]["deep_link_claim"]

    # Anonymous callers cannot reconfigure the cluster.
    assert client.post("/api/v1/system/bootstrap/configure", json=cfg_payload).status_code == 401

    # Stranger on PC #2 (even spoofing the owner's X-Client-Id) cannot claim without the key
    for cid in ("pc-stranger-99", "pc-owner-1"):
        stranger = client.post(
            "/api/v1/system/bootstrap/claim-owner",
            json={"cluster_name": "HACKED"},
            headers={"X-Client-Id": cid},
        )
        assert stranger.status_code == 403

    # Owner moves to PC #2 (old PC lost) and recovers using Master Recovery Key
    recovered = client.post(
        "/api/v1/system/bootstrap/recover-owner",
        json={"action": "verify", "recovery_key": recovery_key},
        headers={"X-Client-Id": "pc-owner-new-2"},
    )
    assert recovered.status_code == 200
    assert recovered.json()["bound_client_id"] == "pc-owner-new-2"
    assert client.get("/api/v1/auth/me").json()["role"] == "owner"


