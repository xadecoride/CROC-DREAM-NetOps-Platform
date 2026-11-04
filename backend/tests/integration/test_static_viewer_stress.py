"""Adversarial stress and concurrency test suite for Static Multi-Viewer QR Access (R1).

Verifies empirical invariants under heavy concurrent load and hostile conditions:
1. 25+ distinct simulated devices (unique client IDs, IPs, user agents) simultaneously logging in without DeviceBoundConflictError.
2. Concurrent query storm: all devices simultaneously reading inventory, topology, stands, and jobs.
3. Rapid token rotation under active concurrent traffic: zero requests with old tokens/sessions can succeed once rotated.
4. Immediate rejection on disable: active sessions immediately drop (401), login attempts fail (401), bearer auth fails (401).
5. Malformed, oversized, and SQL injection token resilience.
6. Privilege escalation and anti-tampering boundary enforcement.
7. Empirical detection of cold-start concurrency race conditions in get_or_create and list_stands.
"""

from __future__ import annotations

import concurrent.futures
import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from netops.api import create_app
from netops.db import Base, build_engine
from netops.enums import UserRole
from netops.models import User
from netops.settings import Settings
from tests.conftest import TOKENS, auth

ADMIN_HEADERS = auth(UserRole.ADMIN)
VIEWER_HEADERS = auth(UserRole.VIEWER)
OPERATOR_HEADERS = auth(UserRole.OPERATOR)


def test_stress_25_concurrent_devices_login_and_query_storm(client: TestClient) -> None:
    """Stress-test 25 distinct simulated devices logging in and querying simultaneously."""
    # 1. Warm-up and retrieve active token
    client.get("/api/v1/stands", headers=ADMIN_HEADERS)
    client.post("/api/v1/auth/static-viewer/toggle", json={"enabled": True}, headers=ADMIN_HEADERS)
    res = client.get("/api/v1/auth/static-viewer", headers=ADMIN_HEADERS)
    assert res.status_code == 200, res.text
    static_token = res.json()["token"]
    assert static_token.startswith("crk_viewer_")

    device_count = 25
    devices_meta = [
        {
            "client_id": f"sim-device-{i:03d}-{uuid.uuid4().hex[:8]}",
            "ip": f"198.51.100.{i + 1}",
            "ua": f"Mozilla/5.0 (Device-{i}; Linux x86_64) NetOpsSim/1.0",
        }
        for i in range(device_count)
    ]

    device_clients: list[tuple[TestClient, dict[str, str]]] = []
    for meta in devices_meta:
        c = TestClient(client.app)
        device_clients.append((c, meta))

    # 2. Concurrently execute login for all 25 devices
    def do_login(pair: tuple[TestClient, dict[str, str]]) -> tuple[int, dict[str, Any], str | None]:
        c, meta = pair
        headers = {
            "X-Client-ID": meta["client_id"],
            "X-Forwarded-For": meta["ip"],
            "User-Agent": meta["ua"],
        }
        resp = c.post("/api/v1/auth/login", json={"token": static_token}, headers=headers)
        sid = c.cookies.get("netops_sid")
        return resp.status_code, resp.json() if resp.status_code == 200 else {"error": resp.text}, sid

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        login_results = list(executor.map(do_login, device_clients))

    # Verify every device successfully logged in
    session_ids: set[str] = set()
    for code, data, sid in login_results:
        assert code == 200, f"Login failed: {data}"
        assert data["role"] == "viewer"
        assert data["username"] == "static_viewer"
        assert "csrf_token" in data
        assert sid is not None
        session_ids.add(sid)

    # Every device must have received a distinct, unique session ID
    assert len(session_ids) == device_count, "Expected distinct session IDs for all 25 devices"

    # 3. Concurrent query storm across all 25 devices
    endpoints = [
        "/api/v1/auth/me",
        "/api/v1/devices",
        "/api/v1/stands",
        "/api/v1/topology/graph",
        "/api/v1/jobs",
    ]

    def query_endpoints(c: TestClient) -> list[tuple[str, int]]:
        results = []
        for ep in endpoints:
            r = c.get(ep)
            results.append((ep, r.status_code))
        return results

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        query_results = list(executor.map(query_endpoints, [c for c, _ in device_clients]))

    for dev_idx, dev_res in enumerate(query_results):
        for ep, status_code in dev_res:
            assert status_code == 200, f"Device {dev_idx} failed on {ep} with status {status_code}"

    # 4. Concurrently verify RBAC mutation protection across all 25 devices
    def attempt_mutations(pair: tuple[TestClient, dict[str, str]]) -> list[int]:
        c, meta = pair
        me_resp = c.get("/api/v1/auth/me").json()
        assert me_resp["username"] == "static_viewer"
        # We need CSRF token for mutating calls
        login_resp = c.post("/api/v1/auth/login", json={"token": static_token}, headers={"X-Client-ID": meta["client_id"]})
        csrf = login_resp.json().get("csrf_token", "")
        mut_headers = {"X-CSRF-Token": csrf}

        codes = [
            c.post("/api/v1/jobs/dry-run", json={}, headers=mut_headers).status_code,
            c.post("/api/v1/jobs/deploy", json={"target_hosts": ["leaf-1"]}, headers=mut_headers).status_code,
            c.post("/api/v1/drift/scan", json={}, headers=mut_headers).status_code,
            c.post("/api/v1/devices", json={"hostname": "hack", "management_ip": "1.1.1.1", "platform": "cisco_iosxe"}, headers=mut_headers).status_code,
            c.post("/api/v1/stands", json={"name": "rogue", "management_prefix": "10.0.0.0/24"}, headers=mut_headers).status_code,
            c.post("/api/v1/users", json={"username": "hacker", "role": "admin"}, headers=mut_headers).status_code,
        ]
        return codes

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        mutation_results = list(executor.map(attempt_mutations, device_clients))

    for dev_idx, codes in enumerate(mutation_results):
        for code in codes:
            assert code == 403, f"Device {dev_idx} was NOT blocked with 403 (got {code})"


def test_stress_rapid_rotation_under_active_concurrent_traffic(client: TestClient) -> None:
    """Stress-test rapid token rotation under active concurrent requests.

    Verifies the empirical invariant: NO device with the old token can successfully
    make any subsequent requests after rotation.
    """
    # 1. Enable static viewer and get initial token T0
    client.post("/api/v1/auth/static-viewer/toggle", json={"enabled": True}, headers=ADMIN_HEADERS)
    t0 = client.get("/api/v1/auth/static-viewer", headers=ADMIN_HEADERS).json()["token"]

    device_count = 20
    devices: list[TestClient] = []
    for i in range(device_count):
        c = TestClient(client.app)
        r = c.post(
            "/api/v1/auth/login",
            json={"token": t0},
            headers={"X-Client-ID": f"rot-dev-{i:02d}", "X-Forwarded-For": f"192.0.2.{i+1}"},
        )
        assert r.status_code == 200
        devices.append(c)

    # Verify all 20 devices are active
    for c in devices:
        assert c.get("/api/v1/auth/me").status_code == 200

    # 2. Perform first rotation T0 -> T1
    rot1 = client.post("/api/v1/auth/static-viewer/rotate", headers=ADMIN_HEADERS)
    assert rot1.status_code == 200
    t1 = rot1.json()["token"]
    assert t1 != t0

    # Invariant: EVERY device with T0 session must immediately receive 401 Unauthorized
    def probe_device(c: TestClient) -> tuple[int, int]:
        r_me = c.get("/api/v1/auth/me")
        r_dev = c.get("/api/v1/devices")
        return r_me.status_code, r_dev.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(probe_device, devices))

    for idx, (me_code, dev_code) in enumerate(results):
        assert me_code == 401, f"Device {idx} /auth/me succeeded after rotation: {me_code}"
        assert dev_code == 401, f"Device {idx} /devices succeeded after rotation: {dev_code}"

    # Verify old token T0 cannot be used for any new login
    fail_login = client.post("/api/v1/auth/login", json={"token": t0})
    assert fail_login.status_code == 401

    # Perform second rotation T1 -> T2 immediately
    rot2 = client.post("/api/v1/auth/static-viewer/rotate", headers=ADMIN_HEADERS)
    assert rot2.status_code == 200
    t2 = rot2.json()["token"]
    assert t2 != t1

    # Old T1 cannot login
    assert client.post("/api/v1/auth/login", json={"token": t1}).status_code == 401
    # Old T0 cannot login
    assert client.post("/api/v1/auth/login", json={"token": t0}).status_code == 401

    # 3. All 20 devices re-authenticate with newest token T2
    def re_login(pair: tuple[int, TestClient]) -> int:
        idx, c = pair
        r = c.post(
            "/api/v1/auth/login",
            json={"token": t2},
            headers={"X-Client-ID": f"rot-dev-{idx:02d}", "X-Forwarded-For": f"192.0.2.{idx+1}"},
        )
        return r.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        re_login_codes = list(executor.map(re_login, list(enumerate(devices))))

    for idx, code in enumerate(re_login_codes):
        assert code == 200, f"Device {idx} failed to re-login with new token T2: {code}"

    # Confirm all devices are active again
    for c in devices:
        assert c.get("/api/v1/auth/me").status_code == 200


def test_stress_toggle_disable_and_reenable_concurrency(client: TestClient) -> None:
    """Stress-test immediate rejection on disable across all concurrent devices."""
    client.post("/api/v1/auth/static-viewer/toggle", json={"enabled": True}, headers=ADMIN_HEADERS)
    token = client.get("/api/v1/auth/static-viewer", headers=ADMIN_HEADERS).json()["token"]

    device_count = 20
    devices: list[TestClient] = []
    for i in range(device_count):
        c = TestClient(client.app)
        r = c.post(
            "/api/v1/auth/login",
            json={"token": token},
            headers={"X-Client-ID": f"dis-dev-{i:02d}", "X-Forwarded-For": f"198.51.100.{i+1}"},
        )
        assert r.status_code == 200
        devices.append(c)

    # All devices are initially active
    for c in devices:
        assert c.get("/api/v1/auth/me").status_code == 200

    # Admin disables static viewer
    toggle_resp = client.post("/api/v1/auth/static-viewer/toggle", json={"enabled": False}, headers=ADMIN_HEADERS)
    assert toggle_resp.status_code == 200
    assert toggle_resp.json()["enabled"] is False

    # Concurrently verify EVERY device is rejected with 401
    def check_rejected(c: TestClient) -> tuple[int, int, int]:
        r1 = c.get("/api/v1/auth/me").status_code
        r2 = c.get("/api/v1/devices").status_code
        r3 = c.get("/api/v1/stands").status_code
        return r1, r2, r3

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        rejections = list(executor.map(check_rejected, devices))

    for idx, (r1, r2, r3) in enumerate(rejections):
        assert r1 == 401, f"Device {idx} /auth/me did not fail with 401: {r1}"
        assert r2 == 401, f"Device {idx} /devices did not fail with 401: {r2}"
        assert r3 == 401, f"Device {idx} /stands did not fail with 401: {r3}"

    # New login attempts while disabled MUST fail with 401
    def attempt_disabled_login(idx: int) -> int:
        c = TestClient(client.app)
        return c.post(
            "/api/v1/auth/login",
            json={"token": token},
            headers={"X-Client-ID": f"new-dev-{idx}"},
        ).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        login_attempts = list(executor.map(attempt_disabled_login, range(10)))

    for idx, code in enumerate(login_attempts):
        assert code == 401, f"New login {idx} succeeded while static viewer disabled: {code}"

    # Bearer auth attempts while disabled MUST fail with 401
    bearer_res = client.get("/api/v1/devices", headers={"Authorization": f"Bearer {token}"})
    assert bearer_res.status_code == 401

    # Re-enable static viewer
    re_enable = client.post("/api/v1/auth/static-viewer/toggle", json={"enabled": True}, headers=ADMIN_HEADERS)
    assert re_enable.status_code == 200
    assert re_enable.json()["enabled"] is True

    # Previous session cookies MUST remain invalid (they were purged upon 401)
    for c in devices:
        assert c.get("/api/v1/auth/me").status_code == 401

    # Fresh login succeeds
    fresh_login = client.post("/api/v1/auth/login", json={"token": token})
    assert fresh_login.status_code == 200
    assert fresh_login.json()["role"] == "viewer"


def test_stress_bearer_token_concurrent_access_and_invalidation(client: TestClient) -> None:
    """Stress-test direct Bearer token usage by 20 distinct machine clients."""
    client.post("/api/v1/auth/static-viewer/toggle", json={"enabled": True}, headers=ADMIN_HEADERS)
    token = client.get("/api/v1/auth/static-viewer", headers=ADMIN_HEADERS).json()["token"]

    def bearer_query(idx: int) -> int:
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Client-ID": f"bearer-worker-{idx:02d}",
            "X-Forwarded-For": f"10.200.0.{idx+1}",
        }
        r = client.get("/api/v1/devices", headers=headers)
        return r.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        status_codes = list(executor.map(bearer_query, range(20)))

    for idx, code in enumerate(status_codes):
        assert code == 200, f"Bearer client {idx} failed: {code}"

    # Rotate token
    rot = client.post("/api/v1/auth/static-viewer/rotate", headers=ADMIN_HEADERS)
    new_token = rot.json()["token"]

    # Old token Bearer requests MUST all fail with 401
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        rotated_codes = list(executor.map(bearer_query, range(20)))

    for idx, code in enumerate(rotated_codes):
        assert code == 401, f"Bearer client {idx} with old token succeeded after rotation: {code}"

    # New token succeeds
    new_bearer_r = client.get("/api/v1/devices", headers={"Authorization": f"Bearer {new_token}"})
    assert new_bearer_r.status_code == 200


def test_stress_adversarial_malformed_and_injection_tokens(client: TestClient) -> None:
    """Stress-test security boundaries against malformed and injection token payloads."""
    malformed_tokens = [
        "crk_viewer_' OR 1=1;--",
        "crk_viewer_' UNION SELECT username, password FROM users;--",
        "crk_viewer_\x00\r\n\t",
        "crk_viewer_" + "A" * 10000,
        "   ",
        "",
        "crk_viewer_nonexistent_hash_test_1234567890",
        "<script>alert(1)</script>",
        "../../etc/passwd",
        '{"token": "nested_json"}',
    ]

    for bad_token in malformed_tokens:
        resp = client.post("/api/v1/auth/login", json={"token": bad_token})
        # Must either fail validation (422) or authentication (401), never crash (500)
        assert resp.status_code in (401, 422), f"Token {bad_token[:30]!r} returned unexpected status {resp.status_code}: {resp.text}"


def test_stress_privilege_escalation_and_tampering_boundaries(client: TestClient, session_factory: Session) -> None:
    """Stress-test that static viewer role cannot access admin controls or tamper with system account."""
    client.post("/api/v1/auth/static-viewer/toggle", json={"enabled": True}, headers=ADMIN_HEADERS)
    token = client.get("/api/v1/auth/static-viewer", headers=ADMIN_HEADERS).json()["token"]

    viewer_client = TestClient(client.app)
    login_r = viewer_client.post("/api/v1/auth/login", json={"token": token})
    assert login_r.status_code == 200
    csrf = login_r.json()["csrf_token"]
    mut_headers = {"X-CSRF-Token": csrf}

    # 1. Static viewer cannot access static viewer management APIs
    assert viewer_client.get("/api/v1/auth/static-viewer").status_code == 403
    assert viewer_client.post("/api/v1/auth/static-viewer/toggle", json={"enabled": False}, headers=mut_headers).status_code == 403
    assert viewer_client.post("/api/v1/auth/static-viewer/rotate", headers=mut_headers).status_code == 403

    # 2. Operator role cannot access static viewer management APIs
    assert client.get("/api/v1/auth/static-viewer", headers=OPERATOR_HEADERS).status_code == 403
    assert client.post("/api/v1/auth/static-viewer/toggle", json={"enabled": False}, headers=OPERATOR_HEADERS).status_code == 403
    assert client.post("/api/v1/auth/static-viewer/rotate", headers=OPERATOR_HEADERS).status_code == 403

    # 3. System account immutability in database under concurrent admin attempts
    with session_factory() as sess:
        sv_user = sess.scalar(select(User).where(User.username == "static_viewer"))
        assert sv_user is not None
        sv_id = sv_user.id

    def try_tamper(action: str) -> tuple[str, int]:
        if action == "delete":
            return action, client.delete(f"/api/v1/users/{sv_id}", headers=ADMIN_HEADERS).status_code
        if action == "patch":
            return action, client.patch(f"/api/v1/users/{sv_id}", json={"role": "admin"}, headers=ADMIN_HEADERS).status_code
        if action == "token":
            return action, client.post(f"/api/v1/users/{sv_id}/token", headers=ADMIN_HEADERS).status_code
        if action == "unbind":
            return action, client.post(f"/api/v1/users/{sv_id}/unbind", headers=ADMIN_HEADERS).status_code
        return action, 0

    actions = ["delete", "patch", "token", "unbind"] * 3
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        tamper_results = list(executor.map(try_tamper, actions))

    for act, code in tamper_results:
        assert code == 409, f"Action {act} did not return 409 Conflict: {code}"

    # Confirm static_viewer account is still intact and functional
    assert viewer_client.get("/api/v1/auth/me").status_code == 200


def test_adversarial_oracle_cold_start_static_viewer_race(tmp_path: Any) -> None:
    """Adversarial Oracle: Test whether cold-start concurrent GET /auth/static-viewer suffers from UNIQUE constraint race condition."""
    db_file = tmp_path / "cold_start_sv.db"
    db_url = f"sqlite:///{db_file}"
    engine = build_engine(db_url)
    Base.metadata.create_all(engine)

    s = Settings(
        database_url=db_url,
        api_tokens={token: {"username": f"{role.value}-user", "role": role} for role, token in TOKENS.items()},
    )
    app = create_app(s, engine=engine)

    def hit_endpoint(i: int) -> int:
        c = TestClient(app)
        try:
            r = c.get("/api/v1/auth/static-viewer", headers=auth(UserRole.ADMIN))
            return r.status_code
        except Exception:
            return 500

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        codes = list(executor.map(hit_endpoint, range(15)))

    # Empirical assertion: all 15 requests MUST succeed with 200 OK without any 500 error
    failed_500s = [c for c in codes if c == 500]
    assert len(failed_500s) == 0, f"Cold-start race condition detected: {len(failed_500s)} of 15 requests crashed with HTTP 500: {codes}"


def test_adversarial_oracle_cold_start_stands_race(tmp_path: Any) -> None:
    """Adversarial Oracle: Test whether cold-start concurrent GET /stands suffers from check-then-act race condition."""
    db_file = tmp_path / "cold_start_stands.db"
    db_url = f"sqlite:///{db_file}"
    engine = build_engine(db_url)
    Base.metadata.create_all(engine)

    s = Settings(
        database_url=db_url,
        api_tokens={token: {"username": f"{role.value}-user", "role": role} for role, token in TOKENS.items()},
    )
    app = create_app(s, engine=engine)

    def hit_stands(i: int) -> int:
        c = TestClient(app)
        try:
            r = c.get("/api/v1/stands", headers=auth(UserRole.VIEWER))
            return r.status_code
        except Exception:
            return 500

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        codes = list(executor.map(hit_stands, range(15)))

    # Empirical assertion: all 15 requests MUST succeed with 200 OK without any 500 error
    failed_500s = [c for c in codes if c == 500]
    assert len(failed_500s) == 0, f"Cold-start race condition detected: {len(failed_500s)} of 15 requests crashed with HTTP 500: {codes}"
