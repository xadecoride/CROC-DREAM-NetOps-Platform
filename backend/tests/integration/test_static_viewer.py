"""Comprehensive integration tests for Static Multi-Viewer QR Access (R1).

Verifies:
1. Multi-device concurrent login with different client IDs and IPs without DeviceBoundConflictError.
2. Immediate O(1) session invalidation on token rotation (401 Unauthorized on next request).
3. Immediate O(1) session invalidation on toggle disable (401 Unauthorized on next request).
4. Full read-only RBAC matrix (viewer can read inventory/topology/jobs/drift, cannot mutate state).
5. Static viewer management endpoints strictly require Admin or Owner.
6. Static viewer account protected against modification/deletion via standard user management APIs.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from netops.enums import UserRole
from netops.models import User
from tests.conftest import auth

ADMIN_HEADERS = auth(UserRole.ADMIN)
VIEWER_HEADERS = auth(UserRole.VIEWER)
OPERATOR_HEADERS = auth(UserRole.OPERATOR)


def test_static_viewer_lifecycle_and_multi_device_login(client: TestClient) -> None:
    # 1. Admin fetches or initializes static viewer token
    res = client.get("/api/v1/auth/static-viewer", headers=ADMIN_HEADERS)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["username"] == "static_viewer"
    assert data["role"] == "viewer"
    assert data["enabled"] is True
    assert data["token"].startswith("crk_viewer_")
    assert f"?token={data['token']}" in data["url"]

    static_token = data["token"]

    # 2. Device 1 (Mobile phone) logs in
    c1 = TestClient(client.app)
    r1 = c1.post(
        "/api/v1/auth/login",
        json={"token": static_token},
        headers={"X-Client-ID": "mobile-client-01", "User-Agent": "MobileSafari/18.0"},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["role"] == "viewer"
    assert "netops_sid" in c1.cookies

    # 3. Device 2 (Laptop) logs in with the SAME token
    c2 = TestClient(client.app)
    r2 = c2.post(
        "/api/v1/auth/login",
        json={"token": static_token},
        headers={"X-Client-ID": "laptop-client-02", "User-Agent": "Chrome/130.0"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["role"] == "viewer"
    assert "netops_sid" in c2.cookies

    # 4. Device 3 (Tablet) logs in with the SAME token
    c3 = TestClient(client.app)
    r3 = c3.post(
        "/api/v1/auth/login",
        json={"token": static_token},
        headers={"X-Client-ID": "tablet-client-03", "User-Agent": "TabletBrowser/1.0"},
    )
    assert r3.status_code == 200, r3.text
    assert r3.json()["role"] == "viewer"
    assert "netops_sid" in c3.cookies

    # 5. Verify all three devices can concurrently query read-only endpoints
    for c in (c1, c2, c3):
        me_res = c.get("/api/v1/auth/me")
        assert me_res.status_code == 200
        assert me_res.json()["username"] == "static_viewer"
        assert me_res.json()["role"] == "viewer"

        devs_res = c.get("/api/v1/devices")
        assert devs_res.status_code == 200
        assert isinstance(devs_res.json(), list)


def test_static_viewer_instant_revocation_on_rotation(client: TestClient) -> None:
    # Get current static viewer token
    res = client.get("/api/v1/auth/static-viewer", headers=ADMIN_HEADERS)
    assert res.status_code == 200
    old_token = res.json()["token"]

    # Active viewer device logs in
    viewer_client = TestClient(client.app)
    login_res = viewer_client.post(
        "/api/v1/auth/login",
        json={"token": old_token},
        headers={"X-Client-ID": "viewer-device-a"},
    )
    assert login_res.status_code == 200
    assert viewer_client.get("/api/v1/auth/me").status_code == 200

    # Admin rotates token
    rotate_res = client.post("/api/v1/auth/static-viewer/rotate", headers=ADMIN_HEADERS)
    assert rotate_res.status_code == 200
    new_token = rotate_res.json()["token"]
    assert new_token != old_token
    assert new_token.startswith("crk_viewer_")

    # The existing session must be revoked immediately on its very next request (401)
    revoked_res = viewer_client.get("/api/v1/auth/me")
    assert revoked_res.status_code == 401

    devs_res = viewer_client.get("/api/v1/devices")
    assert devs_res.status_code == 401

    # Attempting to log in again with the old token must fail
    failed_login = client.post("/api/v1/auth/login", json={"token": old_token})
    assert failed_login.status_code == 401

    # Logging in with the new token must succeed
    new_login = viewer_client.post(
        "/api/v1/auth/login",
        json={"token": new_token},
        headers={"X-Client-ID": "viewer-device-a"},
    )
    assert new_login.status_code == 200
    assert viewer_client.get("/api/v1/auth/me").status_code == 200


def test_static_viewer_instant_revocation_on_toggle(client: TestClient) -> None:
    # Ensure static viewer is enabled
    client.post("/api/v1/auth/static-viewer/toggle", json={"enabled": True}, headers=ADMIN_HEADERS)
    status_data = client.get("/api/v1/auth/static-viewer", headers=ADMIN_HEADERS).json()
    token = status_data["token"]

    # Active viewer device logs in
    viewer_client = TestClient(client.app)
    login_res = viewer_client.post(
        "/api/v1/auth/login",
        json={"token": token},
        headers={"X-Client-ID": "viewer-device-b"},
    )
    assert login_res.status_code == 200
    assert viewer_client.get("/api/v1/auth/me").status_code == 200

    # Admin toggles static viewer to disabled
    toggle_off = client.post(
        "/api/v1/auth/static-viewer/toggle",
        json={"enabled": False},
        headers=ADMIN_HEADERS,
    )
    assert toggle_off.status_code == 200
    assert toggle_off.json()["enabled"] is False

    # Active session must fail immediately with 401
    assert viewer_client.get("/api/v1/auth/me").status_code == 401
    assert viewer_client.get("/api/v1/devices").status_code == 401

    # New login attempts with the token must fail with 401
    new_attempt = client.post("/api/v1/auth/login", json={"token": token})
    assert new_attempt.status_code == 401

    # Admin re-enables static viewer
    toggle_on = client.post(
        "/api/v1/auth/static-viewer/toggle",
        json={"enabled": True},
        headers=ADMIN_HEADERS,
    )
    assert toggle_on.status_code == 200
    assert toggle_on.json()["enabled"] is True

    # Login now succeeds again
    re_login = viewer_client.post("/api/v1/auth/login", json={"token": token})
    assert re_login.status_code == 200
    assert viewer_client.get("/api/v1/auth/me").status_code == 200


def test_static_viewer_read_only_matrix(client: TestClient) -> None:
    # Enable and get static viewer token
    client.post("/api/v1/auth/static-viewer/toggle", json={"enabled": True}, headers=ADMIN_HEADERS)
    token = client.get("/api/v1/auth/static-viewer", headers=ADMIN_HEADERS).json()["token"]

    viewer = TestClient(client.app)
    login_res = viewer.post("/api/v1/auth/login", json={"token": token})
    assert login_res.status_code == 200
    csrf_token = login_res.json()["csrf_token"]
    mut_headers = {"X-CSRF-Token": csrf_token}

    # Allowed reads (200 OK)
    assert viewer.get("/api/v1/devices").status_code == 200
    assert viewer.get("/api/v1/stands").status_code == 200
    assert viewer.get("/api/v1/topology/graph").status_code == 200
    assert viewer.get("/api/v1/topology/checkpoints").status_code == 200
    assert viewer.get("/api/v1/jobs").status_code == 200
    assert viewer.get("/api/v1/drift/report").status_code == 200

    # Mutating actions MUST be blocked with 403 Forbidden
    assert viewer.post("/api/v1/jobs/dry-run", json={}, headers=mut_headers).status_code == 403
    assert (
        viewer.post(
            "/api/v1/jobs/deploy",
            json={"target_hosts": ["leaf-01"]},
            headers=mut_headers,
        ).status_code
        == 403
    )
    assert viewer.post("/api/v1/drift/scan", json={}, headers=mut_headers).status_code == 403
    assert (
        viewer.post(
            "/api/v1/devices",
            json={"hostname": "rogue", "management_ip": "1.1.1.1", "platform": "cisco_iosxe"},
            headers=mut_headers,
        ).status_code
        == 403
    )
    assert (
        viewer.post(
            "/api/v1/users",
            json={"username": "hacker", "role": "admin"},
            headers=mut_headers,
        ).status_code
        == 403
    )


def test_static_viewer_endpoints_access_control(client: TestClient) -> None:
    # 1. Unauthenticated requests are rejected
    anon = TestClient(client.app)
    assert anon.get("/api/v1/auth/static-viewer").status_code == 401
    assert anon.post("/api/v1/auth/static-viewer/toggle", json={"enabled": True}).status_code == 401
    assert anon.post("/api/v1/auth/static-viewer/rotate").status_code == 401

    # 2. Viewer role is rejected with 403 Forbidden
    assert client.get("/api/v1/auth/static-viewer", headers=VIEWER_HEADERS).status_code == 403
    assert (
        client.post(
            "/api/v1/auth/static-viewer/toggle",
            json={"enabled": True},
            headers=VIEWER_HEADERS,
        ).status_code
        == 403
    )
    assert (
        client.post("/api/v1/auth/static-viewer/rotate", headers=VIEWER_HEADERS).status_code == 403
    )

    # 3. Operator role is rejected with 403 Forbidden
    assert client.get("/api/v1/auth/static-viewer", headers=OPERATOR_HEADERS).status_code == 403
    assert (
        client.post(
            "/api/v1/auth/static-viewer/toggle",
            json={"enabled": True},
            headers=OPERATOR_HEADERS,
        ).status_code
        == 403
    )
    assert (
        client.post("/api/v1/auth/static-viewer/rotate", headers=OPERATOR_HEADERS).status_code
        == 403
    )

    # 4. Admin and Owner succeed with 200 OK
    assert client.get("/api/v1/auth/static-viewer", headers=ADMIN_HEADERS).status_code == 200
    assert (
        client.post(
            "/api/v1/auth/static-viewer/toggle",
            json={"enabled": True},
            headers=ADMIN_HEADERS,
        ).status_code
        == 200
    )


def test_static_viewer_account_protected_from_crud(
    client: TestClient, session_factory: Session
) -> None:
    # Initialize static viewer
    client.get("/api/v1/auth/static-viewer", headers=ADMIN_HEADERS)

    # Find static_viewer id in DB
    with session_factory() as sess:
        user = sess.scalar(select(User).where(User.username == "static_viewer"))
        assert user is not None
        sv_id = user.id

    # Admin attempts DELETE /users/{id}
    del_res = client.delete(f"/api/v1/users/{sv_id}", headers=ADMIN_HEADERS)
    assert del_res.status_code == 409, del_res.text
    assert "System account 'static_viewer' cannot be modified or deleted" in del_res.text

    # Admin attempts PATCH /users/{id}
    patch_res = client.patch(
        f"/api/v1/users/{sv_id}",
        json={"role": "admin"},
        headers=ADMIN_HEADERS,
    )
    assert patch_res.status_code == 409, patch_res.text

    # Admin attempts POST /users/{id}/token
    tok_res = client.post(f"/api/v1/users/{sv_id}/token", headers=ADMIN_HEADERS)
    assert tok_res.status_code == 409, tok_res.text

    # Admin attempts POST /users/{id}/unbind
    unbind_res = client.post(f"/api/v1/users/{sv_id}/unbind", headers=ADMIN_HEADERS)
    assert unbind_res.status_code == 409, unbind_res.text


def test_static_viewer_strictly_eternal_and_cold_start(
    client: TestClient, session_factory: Session
) -> None:
    from datetime import timedelta

    from netops.constants import const
    from netops.db import utcnow
    from netops.models import SystemState

    eternal_token = str(const("console.static_viewer_token"))
    eternal_ttl = int(const("session.eternal_ttl_seconds"))

    # Simulate cold start / DB reset: remove static_viewer from User and SystemState
    with session_factory() as sess:
        existing_u = sess.scalar(select(User).where(User.username == "static_viewer"))
        if existing_u is not None:
            sess.delete(existing_u)
        existing_st = sess.get(SystemState, "static_viewer")
        if existing_st is not None:
            sess.delete(existing_st)
        sess.commit()

    # Scanning QR code on cold start BEFORE any admin opens ShareModal must auto-initialize and succeed
    viewer = TestClient(client.app)
    cold_login = viewer.post("/api/v1/auth/login", json={"token": eternal_token})
    assert cold_login.status_code == 200, cold_login.text
    assert cold_login.json()["username"] == "static_viewer"
    set_cookie_hdr = cold_login.headers.get("set-cookie", "")
    assert f"Max-Age={eternal_ttl}" in set_cookie_hdr

    # Even if expires_at is corrupted in DB to the past, session and login never expire
    with session_factory() as sess:
        u = sess.scalar(select(User).where(User.username == "static_viewer"))
        assert u is not None
        u.expires_at = utcnow() - timedelta(days=365)
        sess.commit()

    assert viewer.get("/api/v1/auth/me").status_code == 200
    relogin = viewer.post("/api/v1/auth/login", json={"token": eternal_token})
    assert relogin.status_code == 200
    with session_factory() as sess:
        u = sess.scalar(select(User).where(User.username == "static_viewer"))
        assert u is not None and u.expires_at is None

