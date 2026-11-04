"""Adversarial Stress Test Suite: Jobs, Terminal Stream & Foreign State Isolation.

Milestone 3 Challenger 2 Verification:
1. Cross-stand job & live SSE terminal stream isolation (Stand A vs Stand B).
2. Empty targets, orphaned targets, mixed targets, and hostile payload spoofing.
3. Job list history leak prevention.
4. Concurrent high-throughput cross-stand authorization stress test.
"""

from __future__ import annotations

import concurrent.futures
import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from netops.api.routes import devices as devices_route
from netops.api.routes import stands as stands_route
from netops.api.routes.jobs import _assert_job_allowed
from netops.enums import JobStatus, JobType, LogLevel, UserRole
from netops.models import Job, JobLog, JobTarget
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


def _setup_stands_and_devices(client: TestClient) -> dict[str, Any]:
    # 1. Stand Alpha
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
            },
        },
        headers=ADMIN_AUTH,
    )

    # 2. Stand Beta
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
            },
        },
        headers=ADMIN_AUTH,
    )

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

    r_b1 = client.post(
        "/api/v1/devices",
        json={
            "hostname": "beta-leaf1.croc.lab",
            "management_ip": "172.20.102.11",
            "platform": "arista_eos",
            "role": "leaf",
            "auth_profile": "lab",
            "fabric_id": "stand-beta",
            "site": "stand-beta",
        },
        headers=ADMIN_AUTH,
    )
    assert r_b1.status_code == 201, r_b1.text

    return {
        "alpha_id": r_a1.json()["id"],
        "beta_id": r_b1.json()["id"],
    }


def test_cross_stand_job_and_live_terminal_stream_rejection(
    client: TestClient, session: Session
) -> None:
    """Empirically verify that Stand A operator and viewer cannot access Stand B jobs/streams."""
    ids = _setup_stands_and_devices(client)
    op_alpha = _create_scoped_user(client, "op-challenger-a", UserRole.OPERATOR, "stand:stand-alpha")
    view_alpha = _create_scoped_user(client, "view-challenger-a", UserRole.VIEWER, "stand:stand-alpha")

    # Create Job A (Stand Alpha) and Job B (Stand Beta)
    r_job_a = client.post(
        "/api/v1/jobs/dry-run",
        json={"device_ids": [ids["alpha_id"]]},
        headers=OPERATOR_AUTH,
    )
    assert r_job_a.status_code == 202, r_job_a.text
    job_a_id = r_job_a.json()["job_id"]

    r_job_b = client.post(
        "/api/v1/jobs/dry-run",
        json={"device_ids": [ids["beta_id"]]},
        headers=OPERATOR_AUTH,
    )
    assert r_job_b.status_code == 202, r_job_b.text
    job_b_id = r_job_b.json()["job_id"]

    # Append simulated confidential terminal logs to Job B
    log_secret = JobLog(
        job_id=uuid.UUID(job_b_id),
        step="terminal_exec",
        hostname="beta-leaf1.croc.lab",
        level=LogLevel.INFO,
        message="SECRET_BETA_KEY: Configuring confidential BGP peer on beta-leaf1",
    )
    session.add(log_secret)
    session.commit()

    # 1. Stand A Operator attacking Stand B Job endpoints
    endpoints_to_test = [
        f"/api/v1/jobs/{job_b_id}",
        f"/api/v1/jobs/{job_b_id}/logs",
        f"/api/v1/jobs/{job_b_id}/diff",
        f"/api/v1/jobs/{job_b_id}/stream?once=true",
        f"/api/v1/jobs/{job_b_id}/stream",
    ]
    for ep in endpoints_to_test:
        resp = client.get(ep, headers=op_alpha)
        assert resp.status_code == 403, f"Expected 403 for {ep}, got {resp.status_code}: {resp.text}"
        assert "SECRET_BETA_KEY" not in resp.text
        assert "beta-leaf1" not in resp.text

    # Explain endpoint for Job B -> 403 Forbidden
    r_exp = client.post(f"/api/v1/jobs/{job_b_id}/explain", headers=op_alpha)
    assert r_exp.status_code == 403, r_exp.text
    assert "SECRET_BETA_KEY" not in r_exp.text

    # Deploy endpoint for Job B -> 403 Forbidden
    r_dep = client.post(
        "/api/v1/jobs/deploy",
        json={"job_id": job_b_id, "confirmed_by": "attacker"},
        headers=op_alpha,
    )
    assert r_dep.status_code == 403, r_dep.text

    # Dry-run targeting Stand B device -> 403 Forbidden
    r_dry = client.post(
        "/api/v1/jobs/dry-run",
        json={"device_ids": [ids["beta_id"]]},
        headers=op_alpha,
    )
    assert r_dry.status_code == 403, r_dry.text

    # 2. Stand A Viewer attacking Stand B Job endpoints
    for ep in endpoints_to_test:
        resp = client.get(ep, headers=view_alpha)
        assert resp.status_code == 403, f"Viewer expected 403 for {ep}, got {resp.status_code}"
        assert "SECRET_BETA_KEY" not in resp.text

    # 3. Stand A Operator accessing their OWN Job A -> 200 OK
    r_a_get = client.get(f"/api/v1/jobs/{job_a_id}", headers=op_alpha)
    assert r_a_get.status_code == 200, r_a_get.text
    r_a_logs = client.get(f"/api/v1/jobs/{job_a_id}/logs", headers=op_alpha)
    assert r_a_logs.status_code == 200, r_a_logs.text
    r_a_stream = client.get(f"/api/v1/jobs/{job_a_id}/stream?once=true", headers=op_alpha)
    assert r_a_stream.status_code == 200, r_a_stream.text


def test_jobs_with_empty_targets_and_hostile_payloads_do_not_leak(
    client: TestClient, session: Session
) -> None:
    """Stress test empty targets, spoofed payloads, and orphaned targets against cross-stand leak."""
    ids = _setup_stands_and_devices(client)
    op_alpha = _create_scoped_user(client, "op-empty-payload", UserRole.OPERATOR, "stand:stand-alpha")
    op_beta = _create_scoped_user(client, "op-beta-user", UserRole.OPERATOR, "stand:stand-beta")

    # 1. Empty Job: targets=[] and payload=None
    empty_job = Job(type=JobType.DRY_RUN, created_by="system-test")
    session.add(empty_job)
    session.commit()
    empty_id = str(empty_job.id)

    for ep in (
        f"/api/v1/jobs/{empty_id}",
        f"/api/v1/jobs/{empty_id}/logs",
        f"/api/v1/jobs/{empty_id}/diff",
        f"/api/v1/jobs/{empty_id}/stream?once=true",
    ):
        r = client.get(ep, headers=op_alpha)
        assert r.status_code == 403, f"Expected 403 for empty target on {ep}, got {r.status_code}"

    # 2. Hostile Spoofed Payload with Empty Targets
    # Attacker sets payload claims stand-alpha, but targets list is empty
    spoofed_empty = Job(type=JobType.DRY_RUN, created_by="attacker")
    spoofed_empty.payload = {"stand": "stand-alpha", "devices": ["alpha-leaf1.croc.lab"]}  # type: ignore[attr-defined]
    session.add(spoofed_empty)
    session.commit()
    spoofed_empty_id = str(spoofed_empty.id)

    r_spoofed_empty = client.get(f"/api/v1/jobs/{spoofed_empty_id}", headers=op_alpha)
    assert r_spoofed_empty.status_code == 403, "Empty targets must raise 403 even if payload is spoofed"

    # 3. Foreign Stand in Payload with Stand Alpha targets (enforced by _assert_job_allowed)
    foreign_stand_job = Job(type=JobType.DRY_RUN, created_by="system-test")
    foreign_stand_job.payload = {"stand": "stand-beta"}  # type: ignore[attr-defined]
    foreign_stand_job.targets = [JobTarget(device_id=ids["alpha_id"], hostname="alpha-leaf1.croc.lab")]
    with pytest.raises(HTTPException) as exc_stand:
        _assert_job_allowed(foreign_stand_job, "stand:stand-alpha")
    assert exc_stand.value.status_code == 403
    assert "стенд 'stand-beta' не разрешён" in exc_stand.value.detail

    # 4. Foreign fabric_id in Payload
    foreign_fabric_job = Job(type=JobType.DRY_RUN, created_by="system-test")
    foreign_fabric_job.payload = {"fabric_id": "stand-beta"}  # type: ignore[attr-defined]
    foreign_fabric_job.targets = [JobTarget(device_id=ids["alpha_id"], hostname="alpha-leaf1.croc.lab")]
    with pytest.raises(HTTPException) as exc_fab:
        _assert_job_allowed(foreign_fabric_job, "stand:stand-alpha")
    assert exc_fab.value.status_code == 403

    # 5. Foreign device in Payload devices list
    foreign_dev_job = Job(type=JobType.DRY_RUN, created_by="system-test")
    foreign_dev_job.payload = {"devices": ["beta-leaf1.croc.lab"]}  # type: ignore[attr-defined]
    foreign_dev_job.targets = [JobTarget(device_id=ids["alpha_id"], hostname="alpha-leaf1.croc.lab")]
    with pytest.raises(HTTPException) as exc_dev:
        _assert_job_allowed(foreign_dev_job, "stand:stand-alpha")
    assert exc_dev.value.status_code == 403

    # 6. Target with deleted device (device_id=None) and foreign hostname
    orphaned_target_job = Job(type=JobType.DRY_RUN, created_by="system-test")
    orphaned_target_job.targets = [JobTarget(device_id=None, hostname="beta-leaf1.croc.lab")]
    session.add(orphaned_target_job)
    session.commit()

    r_orphaned = client.get(f"/api/v1/jobs/{orphaned_target_job.id}", headers=op_alpha)
    assert r_orphaned.status_code == 403, r_orphaned.text

    # 7. Target with empty hostname and device_id=None
    blank_target_job = Job(type=JobType.DRY_RUN, created_by="system-test")
    blank_target_job.targets = [JobTarget(device_id=None, hostname="")]
    session.add(blank_target_job)
    session.commit()

    r_blank = client.get(f"/api/v1/jobs/{blank_target_job.id}", headers=op_alpha)
    assert r_blank.status_code == 403, r_blank.text

    # 8. Mixed Targets: Target 1 (Stand Alpha) + Target 2 (Stand Beta)
    mixed_job = Job(type=JobType.DRY_RUN, created_by="system-test")
    mixed_job.targets = [
        JobTarget(device_id=ids["alpha_id"], hostname="alpha-leaf1.croc.lab"),
        JobTarget(device_id=ids["beta_id"], hostname="beta-leaf1.croc.lab"),
    ]
    session.add(mixed_job)
    session.commit()
    mixed_id = str(mixed_job.id)

    # Stand Alpha operator -> blocked (contains Stand Beta target)
    r_mix_a = client.get(f"/api/v1/jobs/{mixed_id}", headers=op_alpha)
    assert r_mix_a.status_code == 403, r_mix_a.text

    # Stand Beta operator -> blocked (contains Stand Alpha target)
    r_mix_b = client.get(f"/api/v1/jobs/{mixed_id}", headers=op_beta)
    assert r_mix_b.status_code == 403, r_mix_b.text

    # Global Admin -> allowed
    r_mix_admin = client.get(f"/api/v1/jobs/{mixed_id}", headers=ADMIN_AUTH)
    assert r_mix_admin.status_code == 200, r_mix_admin.text


def test_job_list_history_zero_cross_stand_leak(
    client: TestClient, session: Session
) -> None:
    """Verify GET /api/v1/jobs strictly excludes foreign, empty, and mixed jobs."""
    ids = _setup_stands_and_devices(client)
    op_alpha = _create_scoped_user(client, "op-list-a", UserRole.OPERATOR, "stand:stand-alpha")

    # Pure Stand A job
    pure_a = Job(type=JobType.DRY_RUN, created_by="test-a")
    pure_a.targets = [JobTarget(device_id=ids["alpha_id"], hostname="alpha-leaf1.croc.lab")]
    session.add(pure_a)

    # Pure Stand B job
    pure_b = Job(type=JobType.DRY_RUN, created_by="test-b")
    pure_b.targets = [JobTarget(device_id=ids["beta_id"], hostname="beta-leaf1.croc.lab")]
    session.add(pure_b)

    # Empty job
    empty_j = Job(type=JobType.DRY_RUN, created_by="empty-j")
    session.add(empty_j)

    # Mixed job
    mixed_j = Job(type=JobType.DRY_RUN, created_by="mixed-j")
    mixed_j.targets = [
        JobTarget(device_id=ids["alpha_id"], hostname="alpha-leaf1.croc.lab"),
        JobTarget(device_id=ids["beta_id"], hostname="beta-leaf1.croc.lab"),
    ]
    session.add(mixed_j)
    session.commit()

    # List jobs as Stand A Operator
    r_list = client.get("/api/v1/jobs", headers=op_alpha)
    assert r_list.status_code == 200, r_list.text
    returned_ids = {item["id"] for item in r_list.json()}

    assert str(pure_a.id) in returned_ids, "Pure Stand A job must be visible"
    assert str(pure_b.id) not in returned_ids, "Pure Stand B job must NOT be visible"
    assert str(empty_j.id) not in returned_ids, "Empty job must NOT be visible"
    assert str(mixed_j.id) not in returned_ids, "Mixed job must NOT be visible"

    # Query with foreign stand param -> 403 Forbidden
    r_param_beta = client.get("/api/v1/jobs?fabric_id=stand-beta", headers=op_alpha)
    assert r_param_beta.status_code == 403, r_param_beta.text

    r_param_site_beta = client.get("/api/v1/jobs?site=stand-beta", headers=op_alpha)
    assert r_param_site_beta.status_code == 403, r_param_site_beta.text


def test_concurrent_rapid_cross_stand_authorization_stress(
    client: TestClient, session: Session
) -> None:
    """Stress test concurrent cross-stand authorization checks under rapid requests."""
    ids = _setup_stands_and_devices(client)
    op_alpha = _create_scoped_user(client, "op-stress-a", UserRole.OPERATOR, "stand:stand-alpha")

    # Stand A Job (SUCCESS so /diff is accessible)
    job_a = Job(type=JobType.DRY_RUN, status=JobStatus.SUCCESS, created_by="stress-test")
    job_a.targets = [JobTarget(device_id=ids["alpha_id"], hostname="alpha-leaf1.croc.lab")]
    session.add(job_a)

    # Stand B Job (SUCCESS so status doesn't mask auth checks)
    job_b = Job(type=JobType.DRY_RUN, status=JobStatus.SUCCESS, created_by="stress-test")
    job_b.targets = [JobTarget(device_id=ids["beta_id"], hostname="beta-leaf1.croc.lab")]
    session.add(job_b)
    session.commit()

    job_a_id = str(job_a.id)
    job_b_id = str(job_b.id)

    attack_endpoints = [
        f"/api/v1/jobs/{job_b_id}",
        f"/api/v1/jobs/{job_b_id}/logs",
        f"/api/v1/jobs/{job_b_id}/diff",
        f"/api/v1/jobs/{job_b_id}/stream?once=true",
        "/api/v1/jobs?fabric_id=stand-beta",
    ]
    legit_endpoints = [
        f"/api/v1/jobs/{job_a_id}",
        f"/api/v1/jobs/{job_a_id}/logs",
        f"/api/v1/jobs/{job_a_id}/diff",
        f"/api/v1/jobs/{job_a_id}/stream?once=true",
        "/api/v1/jobs?fabric_id=stand-alpha",
    ]

    results: list[tuple[str, int, str]] = []

    def _worker_attack(ep: str) -> tuple[str, int, str]:
        resp = client.get(ep, headers=op_alpha)
        return ("attack", resp.status_code, resp.text)

    def _worker_legit(ep: str) -> tuple[str, int, str]:
        resp = client.get(ep, headers=op_alpha)
        return ("legit", resp.status_code, resp.text)

    tasks = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        # Submit 150 attack requests and 50 legit requests interleaved
        for _ in range(30):
            for a_ep in attack_endpoints:
                tasks.append(pool.submit(_worker_attack, a_ep))
            for l_ep in legit_endpoints:
                tasks.append(pool.submit(_worker_legit, l_ep))

        for t in concurrent.futures.as_completed(tasks):
            results.append(t.result())

    # Analyze results
    attack_results = [r for r in results if r[0] == "attack"]
    legit_results = [r for r in results if r[0] == "legit"]

    assert len(attack_results) == 150
    assert len(legit_results) == 150

    # 100% of attack requests MUST be 403 Forbidden
    for _, status_code, body in attack_results:
        assert status_code == 403, f"Cross-stand leak! Expected 403, got {status_code}: {body}"
        assert "beta-leaf1" not in body

    # 100% of legitimate requests MUST be 200 OK
    for _, status_code, body in legit_results:
        assert status_code == 200, f"False positive rejection! Expected 200, got {status_code}: {body}"

