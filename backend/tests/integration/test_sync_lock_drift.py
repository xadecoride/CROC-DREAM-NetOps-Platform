"""Integration tests verifying sync locks and rollback protection in DriftScanPipeline."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from netops.enums import JobStatus, TargetStatus, UserRole
from netops.models import Device, Job, JobTarget
from netops.network.base import ChangePlan
from netops.pipeline.deployment import DeploymentExecutor
from netops.services import JobService
from netops.services.state import get_state_manager, reset_state_manager
from tests.conftest import RunJob, auth

ADMIN = auth(UserRole.ADMIN)
OPERATOR = auth(UserRole.OPERATOR)


@pytest.fixture(autouse=True)
def clean_state():
    reset_state_manager()
    yield
    reset_state_manager()


def _targets(session: Session, job_id: uuid.UUID) -> dict[str, JobTarget]:
    return {target.hostname: target for target in session.get_one(Job, job_id).targets}


def test_drift_scan_skips_device_with_active_sync_lock(
    devices: dict[str, Device],
    job_service: JobService,
    run_job: RunJob,
    session: Session,
) -> None:
    leaf1 = devices["leaf-1.croc.lab"]
    leaf2 = devices["leaf-2.croc.lab"]

    state_mgr = get_state_manager()
    # Lock leaf1
    assert state_mgr.acquire_sync_lock(
        f"device:{leaf1.id}",
        holder="test_lock_holder",
        ttl=600,
        reason="Manual testing lock",
    ) is True

    job = job_service.create_drift_scan([leaf1.id, leaf2.id], requested_by="tester")
    status = run_job(job.id)
    assert status is JobStatus.SUCCESS

    targets = _targets(session, job.id)
    # Locked leaf1 must be SKIPPED
    assert targets[leaf1.hostname].status is TargetStatus.SKIPPED
    assert "locked during active deployment/rollback" in str(targets[leaf1.hostname].error)

    # Unlocked leaf2 must succeed
    assert targets[leaf2.hostname].status is TargetStatus.SUCCESS


def test_drift_scan_skips_device_when_fabric_is_locked(
    devices: dict[str, Device],
    job_service: JobService,
    run_job: RunJob,
    session: Session,
) -> None:
    leaf1 = devices["leaf-1.croc.lab"]
    state_mgr = get_state_manager()

    # Lock whole stand/fabric
    assert state_mgr.acquire_sync_lock(
        f"fabric:{leaf1.fabric_id}",
        holder="stand_maintenance",
        ttl=600,
        reason="Stand maintenance in progress",
    ) is True

    job = job_service.create_drift_scan([leaf1.id], requested_by="tester")
    run_job(job.id)

    targets = _targets(session, job.id)
    assert targets[leaf1.hostname].status is TargetStatus.SKIPPED
    assert "locked during active deployment/rollback" in str(targets[leaf1.hostname].error)


def test_subsequent_drift_scan_proceeds_after_lock_released(
    devices: dict[str, Device],
    job_service: JobService,
    run_job: RunJob,
    session: Session,
) -> None:
    leaf1 = devices["leaf-1.croc.lab"]
    state_mgr = get_state_manager()

    # 1. Lock leaf1 and run scan -> skipped
    state_mgr.acquire_sync_lock(f"device:{leaf1.id}", holder="holder-1", ttl=600)
    job1 = job_service.create_drift_scan([leaf1.id], requested_by="tester")
    run_job(job1.id)
    targets1 = _targets(session, job1.id)
    assert targets1[leaf1.hostname].status is TargetStatus.SKIPPED

    # 2. Release lock
    assert state_mgr.release_sync_lock(f"device:{leaf1.id}", holder="holder-1") is True
    assert state_mgr.is_sync_locked(f"device:{leaf1.id}") is False

    # 3. Next scan proceeds and succeeds
    job2 = job_service.create_drift_scan([leaf1.id], requested_by="tester")
    run_job(job2.id)
    targets2 = _targets(session, job2.id)
    assert targets2[leaf1.hostname].status is TargetStatus.SUCCESS


def test_rollback_engages_sync_lock_and_protects_against_drift(
    devices: dict[str, Device],
    job_service: JobService,
    run_job: RunJob,
    session: Session,
    toolchain,
) -> None:
    leaf1 = devices["leaf-1.croc.lab"]
    state_mgr = get_state_manager()
    assert state_mgr.is_sync_locked(f"device:{leaf1.id}") is False

    # Simulate deployment rollback via DeploymentExecutor._rollback
    recorder = MagicMock()
    executor = DeploymentExecutor(session, toolchain, recorder)
    target = toolchain.target_for(leaf1)
    plan = ChangePlan(
        remediation="interface Loopback10\n",
        rollback="no interface Loopback10\n",
        intended="hostname leaf-1.croc.lab\n",
    )

    rolled_back = executor._rollback(leaf1, target, plan)
    assert rolled_back is True

    # Two-Phase Rollback Protection: sync_lock must be actively held
    assert state_mgr.is_sync_locked(f"device:{leaf1.id}") is True
    assert state_mgr.is_sync_locked(f"fabric:{leaf1.fabric_id}") is True
    lock_info = state_mgr.get_sync_lock_info(f"device:{leaf1.id}")
    assert lock_info is not None
    assert "Two-Phase Rollback" in lock_info["reason"]

    # Drift scan must skip this rolled-back device
    drift_job = job_service.create_drift_scan([leaf1.id], requested_by="tester")
    run_job(drift_job.id)
    targets = _targets(session, drift_job.id)
    assert targets[leaf1.hostname].status is TargetStatus.SKIPPED
    assert "locked during active deployment/rollback" in str(targets[leaf1.hostname].error)


def test_emergency_patch_engages_sync_lock_and_protects_against_drift(
    client: TestClient,
    devices: dict[str, Device],
    job_service: JobService,
    run_job: RunJob,
    session: Session,
) -> None:
    leaf1 = devices["leaf-1.croc.lab"]
    state_mgr = get_state_manager()
    assert state_mgr.is_sync_locked(f"device:{leaf1.id}") is False

    # Apply emergency patch via API
    resp = client.post(
        "/api/v1/emergency/patch",
        json={
            "device": leaf1.hostname,
            "patch": "description Emergency Patch Active",
            "reason": "Fixing production flap",
        },
        headers=ADMIN,
    )
    assert resp.status_code == 200, resp.text

    # Lock must be engaged
    assert (
        state_mgr.is_sync_locked(f"device:{leaf1.id}") is True
        or state_mgr.is_sync_locked(f"device:{leaf1.hostname}") is True
    )

    # Drift scan must skip the device
    drift_job = job_service.create_drift_scan([leaf1.id], requested_by="tester")
    run_job(drift_job.id)
    targets = _targets(session, drift_job.id)
    assert targets[leaf1.hostname].status is TargetStatus.SKIPPED
    assert "locked during active deployment/rollback" in str(targets[leaf1.hostname].error)
