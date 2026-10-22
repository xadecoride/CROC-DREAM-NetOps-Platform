"""Requirement-driven integration and E2E test suite for CROC DREAM NetOps Platform.

Covers all 4 Tiers from ORIGINAL_REQUEST.md and PROJECT.md:
Tier 1: Feature Coverage (Static QR multi-device, instant O(1) revocation, 403 RBAC, secret redaction, multi-tenant isolation).
Tier 2: Boundary & Corner Cases (invalid/expired/malformed tokens, 10 concurrent logins, malformed payloads).
Tier 3: Cross-Feature Combinations (navigation during token rotation, observer during active operator deploy).
Tier 4: Real-World Scenarios (complete viewer session lifecycle, honest offline stand metrics).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from netops.enums import DeviceStatus, UserRole
from netops.models import Device, User
from netops.security import hash_token
from tests.conftest import RunJob, auth

ADMIN_HEADERS = auth(UserRole.ADMIN)
OPERATOR_HEADERS = auth(UserRole.OPERATOR)
VIEWER_HEADERS = auth(UserRole.VIEWER)


# ---------------------------------------------------------------------------
# Tier 1 — Feature Coverage
# ---------------------------------------------------------------------------

class TestTier1FeatureCoverage:
    def test_static_qr_viewer_multi_device_login(self, client: TestClient) -> None:
        """R1: Multiple distinct devices and IPs authenticate concurrently using one static QR token."""
        res = client.get("/api/v1/auth/static-viewer", headers=ADMIN_HEADERS)
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["username"] == "static_viewer"
        assert data["role"] == "viewer"
        assert data["enabled"] is True
        static_token = data["token"]
        assert static_token.startswith("crk_viewer_")

        # Simulate 3 independent devices with distinct client IDs, user agents, and IP headers
        devices_meta = [
            ("dev-phone-ios", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)", "198.51.100.11"),
            ("dev-laptop-chrome", "Mozilla/5.0 (X11; Linux x86_64) Chrome/130.0", "203.0.113.22"),
            ("dev-tablet-ipad", "Mozilla/5.0 (iPad; CPU OS 17_5)", "192.0.2.33"),
        ]
        active_clients: list[TestClient] = []

        for cid, ua, ip in devices_meta:
            device_client = TestClient(client.app)
            login_res = device_client.post(
                "/api/v1/auth/login",
                json={"token": static_token},
                headers={"X-Client-ID": cid, "User-Agent": ua, "X-Forwarded-For": ip},
            )
            assert login_res.status_code == 200, (cid, login_res.text)
            assert login_res.json()["role"] == "viewer"
            assert login_res.json()["username"] == "static_viewer"
            assert "netops_sid" in device_client.cookies
            active_clients.append(device_client)

        # All 3 devices concurrently access read-only routes without conflict
        for device_client in active_clients:
            me_res = device_client.get("/api/v1/auth/me")
            assert me_res.status_code == 200
            assert me_res.json()["username"] == "static_viewer"
            assert me_res.json()["role"] == "viewer"

            dev_res = device_client.get("/api/v1/devices")
            assert dev_res.status_code == 200
            assert isinstance(dev_res.json(), list)

    def test_static_qr_token_rotate_instant_401_revocation(self, client: TestClient) -> None:
        """R1: Rotating the static QR token immediately revokes all existing sessions (401)."""
        res = client.get("/api/v1/auth/static-viewer", headers=ADMIN_HEADERS)
        old_token = res.json()["token"]

        viewer_client = TestClient(client.app)
        assert viewer_client.post("/api/v1/auth/login", json={"token": old_token}).status_code == 200
        assert viewer_client.get("/api/v1/auth/me").status_code == 200

        # Admin rotates the static viewer token
        rotate_res = client.post("/api/v1/auth/static-viewer/rotate", headers=ADMIN_HEADERS)
        assert rotate_res.status_code == 200
        new_token = rotate_res.json()["token"]
        assert new_token != old_token

        # Existing session immediately returns 401 on next request
        assert viewer_client.get("/api/v1/auth/me").status_code == 401
        assert viewer_client.get("/api/v1/devices").status_code == 401

        # Re-login with old token is rejected; new token succeeds
        assert viewer_client.post("/api/v1/auth/login", json={"token": old_token}).status_code == 401
        new_client = TestClient(client.app)
        assert new_client.post("/api/v1/auth/login", json={"token": new_token}).status_code == 200
        assert new_client.get("/api/v1/auth/me").status_code == 200

    def test_static_qr_token_toggle_disable_instant_401_revocation(self, client: TestClient) -> None:
        """R1: Disabling static QR access immediately invalidates active sessions (401)."""
        res = client.get("/api/v1/auth/static-viewer", headers=ADMIN_HEADERS)
        static_token = res.json()["token"]

        viewer_client = TestClient(client.app)
        assert viewer_client.post("/api/v1/auth/login", json={"token": static_token}).status_code == 200
        assert viewer_client.get("/api/v1/auth/me").status_code == 200

        # Admin disables static viewer
        toggle_off = client.post(
            "/api/v1/auth/static-viewer/toggle",
            json={"enabled": False},
            headers=ADMIN_HEADERS,
        )
        assert toggle_off.status_code == 200
        assert toggle_off.json()["enabled"] is False

        # Active session immediately rejected with 401
        assert viewer_client.get("/api/v1/auth/me").status_code == 401
        assert viewer_client.get("/api/v1/devices").status_code == 401

        # Login attempts while disabled are rejected
        assert viewer_client.post("/api/v1/auth/login", json={"token": static_token}).status_code == 401

        # Re-enabling allows login again
        toggle_on = client.post(
            "/api/v1/auth/static-viewer/toggle",
            json={"enabled": True},
            headers=ADMIN_HEADERS,
        )
        assert toggle_on.status_code == 200
        assert viewer_client.post("/api/v1/auth/login", json={"token": static_token}).status_code == 200

    def test_strict_403_forbidden_for_viewer_on_mutating_endpoints(self, client: TestClient) -> None:
        """R2: Viewer cannot execute state mutations; returns strict 403 Forbidden."""
        res = client.get("/api/v1/auth/static-viewer", headers=ADMIN_HEADERS)
        viewer_client = TestClient(client.app)
        login_res = viewer_client.post("/api/v1/auth/login", json={"token": res.json()["token"]})
        assert login_res.status_code == 200
        csrf = login_res.json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf}

        mutations = [
            ("POST", "/api/v1/jobs/deploy", {"device_ids": [1], "intent_source": "git_main"}),
            ("POST", "/api/v1/jobs/dry-run", {"device_ids": [1], "intent_source": "git_main"}),
            ("POST", "/api/v1/drift/scan", {"device_ids": [1]}),
            ("POST", "/api/v1/drift/remediate", {"device_ids": [1]}),
            ("POST", "/api/v1/system/chaos", {"scenario": "reset_lab"}),
            ("POST", "/api/v1/emergency/command", {"device": "leaf-1.croc.lab", "command": "show version"}),
            ("POST", "/api/v1/emergency/patch", {"device": "leaf-1.croc.lab", "commands": ["no shut"]}),
            ("POST", "/api/v1/emergency/soft-reset", {}),
            ("POST", "/api/v1/emergency/hard-reset", {}),
            ("POST", "/api/v1/topology/links", {"source_id": 1, "target_id": 2}),
            ("PATCH", "/api/v1/topology/links/link-1/toggle", {"admin_state": "SHUTDOWN"}),
            ("DELETE", "/api/v1/topology/links/link-1", {}),
            ("POST", "/api/v1/topology/nodes/1/drain", {"drained": True}),
            ("POST", "/api/v1/topology/checkpoints", {"name": "chk-e2e"}),
            ("POST", "/api/v1/stands", {"slug": "stand-mut", "name": "Mut Stand"}),
            ("DELETE", "/api/v1/stands/stand-mut", {}),
            ("POST", "/api/v1/users", {"username": "attacker", "role": "admin"}),
            ("PATCH", "/api/v1/users/1", {"role": "admin"}),
            ("DELETE", "/api/v1/users/1", {}),
            ("POST", "/api/v1/users/1/rotate", {}),
            ("POST", "/api/v1/auth/static-viewer/rotate", {}),
            ("POST", "/api/v1/auth/static-viewer/toggle", {"enabled": False}),
        ]

        for method, url, payload in mutations:
            resp = viewer_client.request(method, url, json=payload, headers=headers)
            assert resp.status_code == 403, f"{method} {url} returned {resp.status_code}, expected 403"

    def test_stands_sync_all_requires_admin(self, client: TestClient) -> None:
        """R2 / F2.1: Hot inventory synchronization must be restricted to Admin."""
        res = client.post("/api/v1/stands/sync-all", json={}, headers=VIEWER_HEADERS)
        assert res.status_code == 403, f"Expected 403 Forbidden for Viewer on /stands/sync-all, got {res.status_code}"

    def test_sensitive_secret_redaction_for_viewer(self, client: TestClient) -> None:
        """R2: Secret fields (user tokens, recovery keys, jump passwords) are redacted for Viewer."""
        # 1. /system/integrations redacts user_tokens and sessions for non-admin
        integ = client.get("/api/v1/system/integrations", headers=VIEWER_HEADERS).json()
        assert integ["user_tokens"] == {}
        assert integ["sessions"] == []

        # 2. /system/bootstrap/status redacts recovery_key for non-owner/viewer
        boot = client.get("/api/v1/system/bootstrap/status", headers=VIEWER_HEADERS).json()
        assert boot["owner"]["recovery_key"] is None
        if "telegram" in boot and boot["telegram"].get("deep_link_claim"):
            assert "crk_owner_" not in boot["telegram"]["deep_link_claim"]

        # 3. /stands never leaks raw passwords in responses
        client.post(
            "/api/v1/stands",
            json={
                "slug": "stand-secret-chk",
                "name": "Secret Check Stand",
                "jump_password": "super-secret-jump-password-xyz",
            },
            headers=ADMIN_HEADERS,
        )
        stands_res = client.get("/api/v1/stands", headers=VIEWER_HEADERS)
        assert stands_res.status_code == 200
        assert "super-secret-jump-password-xyz" not in stands_res.text
        target = next(s for s in stands_res.json() if s["slug"] == "stand-secret-chk")
        assert target["has_password"] is True
        assert "jump_password" not in target

    def test_multitenant_job_isolation_for_scoped_viewer(
        self, client: TestClient, session: Session, devices: dict, run_job: RunJob
    ) -> None:
        """R2 / F2.2: Scoped user cannot access config diffs or explanations for out-of-scope stands."""
        # Create user scoped strictly to stand-alpha
        alpha_viewer_token = "crk_user_alpha_isolated"
        session.add(
            User(
                username="viewer-alpha-only",
                role=UserRole.VIEWER,
                allowed_system="fabric:stand-alpha",
                token_sha256=hash_token(alpha_viewer_token),
                is_active=True,
            )
        )
        # Create device belonging to stand-beta
        dev_beta = Device(
            hostname="beta-leaf-99.croc.lab",
            management_ip="172.20.99.1",
            platform="arista_eos",
            role="leaf",
            auth_profile="lab",
            fabric_id="stand-beta",
            site="dc-beta",
            status=DeviceStatus.IN_SYNC,
        )
        session.add(dev_beta)
        session.commit()

        # Operator triggers dry-run on beta device
        job_res = client.post(
            "/api/v1/jobs/dry-run",
            json={"device_ids": [dev_beta.id], "intent_source": "git_main"},
            headers=OPERATOR_HEADERS,
        )
        assert job_res.status_code == 202
        job_id = uuid.UUID(job_res.json()["job_id"])

        alpha_headers = {"Authorization": f"Bearer {alpha_viewer_token}"}

        # Alpha-scoped viewer must receive 403 on Beta job routes
        assert client.get(f"/api/v1/jobs/{job_id}", headers=alpha_headers).status_code == 403
        assert client.get(f"/api/v1/jobs/{job_id}/diff", headers=alpha_headers).status_code == 403
        assert client.post(f"/api/v1/jobs/{job_id}/explain", headers=alpha_headers).status_code == 403


# ---------------------------------------------------------------------------
# Tier 2 — Boundary & Corner Cases
# ---------------------------------------------------------------------------

class TestTier2BoundaryCases:
    def test_invalid_malformed_and_expired_tokens(self, client: TestClient, session: Session) -> None:
        """Boundary: Reject empty, malformed, non-existent, and expired tokens."""
        # 1. Empty and too short tokens
        assert client.post("/api/v1/auth/login", json={"token": ""}).status_code == 422
        assert client.post("/api/v1/auth/login", json={"token": "12345"}).status_code == 422
        # 2. Whitespace token
        assert client.post("/api/v1/auth/login", json={"token": "      "}).status_code == 401
        # 3. Nonexistent random token
        assert client.post("/api/v1/auth/login", json={"token": "crk_nonexistent_token_9999"}).status_code == 401

        # 4. Expired token
        past = datetime.now(UTC) - timedelta(hours=2)
        exp_user = User(
            username="expired-viewer",
            role=UserRole.VIEWER,
            token_sha256=hash_token("crk_expired_viewer_tok"),
            is_active=True,
            expires_at=past,
        )
        session.add(exp_user)
        session.commit()
        exp_login = client.post("/api/v1/auth/login", json={"token": "crk_expired_viewer_tok"})
        assert exp_login.status_code in (401, 403)

    def test_rapid_concurrent_logins_10_devices(self, client: TestClient) -> None:
        """Stress/Concurrency: 10 distinct devices authenticate with the same static QR token."""
        res = client.get("/api/v1/auth/static-viewer", headers=ADMIN_HEADERS)
        static_token = res.json()["token"]

        clients: list[TestClient] = []
        for i in range(10):
            sim_client = TestClient(client.app)
            login_res = sim_client.post(
                "/api/v1/auth/login",
                json={"token": static_token},
                headers={
                    "X-Client-ID": f"sim-device-{i:02d}",
                    "User-Agent": f"SimDeviceAgent/{i}.0",
                    "X-Forwarded-For": f"10.200.0.{10 + i}",
                },
            )
            assert login_res.status_code == 200, (i, login_res.text)
            assert login_res.json()["role"] == "viewer"
            clients.append(sim_client)

        assert len(clients) == 10
        for c in clients:
            me = c.get("/api/v1/auth/me")
            assert me.status_code == 200
            assert me.json()["username"] == "static_viewer"

    def test_empty_blank_and_malformed_payloads(self, client: TestClient) -> None:
        """Corner: Malformed JSON, oversized payloads, and missing fields are rejected with 422."""
        assert client.post("/api/v1/auth/login", json={}).status_code == 422
        assert client.post("/api/v1/auth/login", json={"token": 99999}).status_code == 422
        assert client.post("/api/v1/auth/login", json={"token": "a" * 1000}).status_code == 422
        assert client.post("/api/v1/auth/static-viewer/toggle", json={}, headers=ADMIN_HEADERS).status_code == 422
        assert (
            client.post(
                "/api/v1/auth/static-viewer/toggle",
                json={"enabled": "invalid-bool"},
                headers=ADMIN_HEADERS,
            ).status_code
            == 422
        )


# ---------------------------------------------------------------------------
# Tier 3 — Cross-Feature Combinations
# ---------------------------------------------------------------------------

class TestTier3CrossFeatureCombinations:
    def test_viewer_navigates_dashboard_while_admin_rotates_token(self, client: TestClient) -> None:
        """Cross-Feature: Active viewer browsing endpoints is revoked mid-flight on token rotation."""
        res = client.get("/api/v1/auth/static-viewer", headers=ADMIN_HEADERS)
        static_token = res.json()["token"]

        viewer = TestClient(client.app)
        assert viewer.post("/api/v1/auth/login", json={"token": static_token}).status_code == 200

        # Step 1: Viewer successfully reads devices and topology
        assert viewer.get("/api/v1/devices").status_code == 200
        assert viewer.get("/api/v1/topology/graph").status_code == 200

        # Step 2: Admin rotates the static QR token
        assert client.post("/api/v1/auth/static-viewer/rotate", headers=ADMIN_HEADERS).status_code == 200

        # Step 3: Viewer's very next request is rejected with 401
        assert viewer.get("/api/v1/jobs").status_code == 401
        assert viewer.get("/api/v1/drift/report").status_code == 401

    def test_operator_deploy_viewer_observes_cannot_cancel_or_mutate(
        self, client: TestClient, devices: dict
    ) -> None:
        """Cross-Feature: Operator initiates dry-run; Viewer observes stream but cannot alter state."""
        res = client.get("/api/v1/auth/static-viewer", headers=ADMIN_HEADERS)
        viewer = TestClient(client.app)
        login_res = viewer.post("/api/v1/auth/login", json={"token": res.json()["token"]})
        assert login_res.status_code == 200
        csrf = login_res.json()["csrf_token"]

        # Operator triggers dry-run
        op_res = client.post(
            "/api/v1/jobs/dry-run",
            json={"device_ids": [devices["spine-1.croc.lab"].id], "intent_source": "git_main"},
            headers=OPERATOR_HEADERS,
        )
        assert op_res.status_code == 202
        job_id = op_res.json()["job_id"]

        # Viewer can read job status and SSE stream
        assert viewer.get(f"/api/v1/jobs/{job_id}").status_code == 200
        stream = viewer.get(f"/api/v1/jobs/{job_id}/stream", params={"once": "true"})
        assert stream.status_code == 200
        assert "text/event-stream" in stream.headers.get("content-type", "")

        # Viewer cannot trigger deploy or inject chaos
        v_headers = {"X-CSRF-Token": csrf}
        assert (
            viewer.post(
                "/api/v1/jobs/deploy",
                json={"device_ids": [devices["spine-1.croc.lab"].id]},
                headers=v_headers,
            ).status_code
            == 403
        )
        assert (
            viewer.post(
                "/api/v1/system/chaos",
                json={"scenario": "reset_lab"},
                headers=v_headers,
            ).status_code
            == 403
        )


# ---------------------------------------------------------------------------
# Tier 4 — Real-World Scenarios
# ---------------------------------------------------------------------------

class TestTier4RealWorldScenarios:
    def test_full_viewer_session_lifecycle(self, client: TestClient) -> None:
        """Scenario: Complete viewer journey from QR link auto-login to exploration, mutation block, and logout."""
        # 1. Admin generates or inspects static viewer link
        info = client.get("/api/v1/auth/static-viewer", headers=ADMIN_HEADERS).json()
        raw_url = info["url"]
        parsed_url = urlparse(raw_url)
        extracted_token = parse_qs(parsed_url.query)["token"][0]

        # 2. Browser loads link, auto-logs in via token
        viewer = TestClient(client.app)
        login = viewer.post(
            "/api/v1/auth/login",
            json={"token": extracted_token},
            headers={"X-Client-ID": "browser-tab-jury-01"},
        )
        assert login.status_code == 200
        csrf = login.json()["csrf_token"]
        assert "netops_sid" in viewer.cookies

        # 3. Viewer explores dashboards, topology, and reports
        assert viewer.get("/api/v1/auth/me").status_code == 200
        assert viewer.get("/api/v1/devices").status_code == 200
        assert viewer.get("/api/v1/topology/graph").status_code == 200
        assert viewer.get("/api/v1/drift/report").status_code == 200
        assert viewer.get("/api/v1/jobs").status_code == 200

        # 4. Viewer attempts unauthorized mutation -> blocked with 403
        bad_post = viewer.post(
            "/api/v1/jobs/deploy",
            json={"device_ids": [1], "intent_source": "git_main"},
            headers={"X-CSRF-Token": csrf},
        )
        assert bad_post.status_code == 403

        # 5. Viewer logs out
        logout = viewer.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        assert logout.status_code == 204

        # 6. Session cookie is permanently invalidated
        assert viewer.get("/api/v1/auth/me").status_code == 401

    def test_stand_offline_zero_fake_metrics(self, client: TestClient, session: Session) -> None:
        """Scenario: Unreachable stand reports honest zero in-sync devices and non-zero unreachable/unknown."""
        stand_slug = "stand-offline-honesty"
        client.post(
            "/api/v1/stands",
            json={
                "slug": stand_slug,
                "name": "Offline Unreachable Stand",
                "jump_host": "198.51.100.254",
                "jump_port": 2222,
                "jump_password": "fake-offline-pw",
            },
            headers=ADMIN_HEADERS,
        )

        # Insert unverified offline devices for this stand
        session.add_all([
            Device(
                hostname="offline-sw1.croc.lab",
                management_ip="172.20.254.1",
                platform="arista_eos",
                role="leaf",
                auth_profile="lab",
                fabric_id=stand_slug,
                site=stand_slug,
                status=DeviceStatus.UNREACHABLE,
            ),
            Device(
                hostname="offline-sw2.croc.lab",
                management_ip="172.20.254.2",
                platform="arista_eos",
                role="spine",
                auth_profile="lab",
                fabric_id=stand_slug,
                site=stand_slug,
                status=DeviceStatus.UNKNOWN,
            ),
        ])
        session.commit()

        # Query stand details
        stand_res = client.get(f"/api/v1/stands/{stand_slug}", headers=VIEWER_HEADERS)
        assert stand_res.status_code == 200
        stand_data = stand_res.json()
        assert stand_data["slug"] == stand_slug

        # Honest metrics check: in_sync_devices MUST be 0
        assert stand_data["in_sync_devices"] == 0, f"Fabricated in_sync count: {stand_data['in_sync_devices']}"
        assert stand_data["unreachable_devices"] >= 1

        # Query devices list
        devs_res = client.get("/api/v1/devices", headers=VIEWER_HEADERS)
        assert devs_res.status_code == 200
        stand_devices = [d for d in devs_res.json() if d.get("fabric_id") == stand_slug]
        assert len(stand_devices) == 2
        for dev in stand_devices:
            assert dev["status"] != "IN_SYNC", f"Device {dev['hostname']} falsely reported IN_SYNC while offline"
