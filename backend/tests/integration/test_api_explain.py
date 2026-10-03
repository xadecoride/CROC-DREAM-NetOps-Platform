"""Integration tests for POST /api/v1/jobs/{job_id}/explain endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING

from netops.enums import JobStatus, JobType, UserRole
from netops.models import Device, Job, JobTarget
from netops.services.jobs import SCHEDULER_USER
from tests.conftest import auth

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session


def test_explain_job_diff_endpoint(
    client: TestClient,
    session: Session,
    devices: list[Device],
) -> None:
    device = session.query(Device).first()
    assert device is not None

    job = Job(
        type=JobType.DRY_RUN,
        status=JobStatus.SUCCESS,
        progress=100,
        created_by=SCHEDULER_USER,
    )
    session.add(job)
    session.flush()

    patch = "router bgp 65000\n neighbor 10.0.1.1 password fabric-secret\n"
    target = JobTarget(
        job_id=job.id,
        device_id=device.id,
        hostname=device.hostname,
        remediation_config=patch,
        rollback_config="no router bgp 65000\n",
    )
    session.add(target)
    session.commit()

    # 1. Unauthenticated -> 401
    res_unauth = client.post(f"/api/v1/jobs/{job.id}/explain")
    assert res_unauth.status_code == 401

    # 2. Authenticated Viewer -> 200 OK
    res = client.post(
        f"/api/v1/jobs/{job.id}/explain",
        headers=auth(UserRole.VIEWER),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["risk_level"] in ["HIGH", "CRITICAL"]
    assert data["is_safe"] is False
    assert len(data["key_points"]) > 0
    assert "summary" in data
    assert "recommendations" in data
