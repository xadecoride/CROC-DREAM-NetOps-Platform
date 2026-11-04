from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from netops.enums import DeviceStatus, JobStatus, UserRole
from netops.models import Device, Job
from tests.conftest import RecordingDispatcher, RunJob, auth
from tests.integration.helpers import change_uplink_description

OPERATOR = auth(UserRole.OPERATOR)
VIEWER = auth(UserRole.VIEWER)


def _dry_run(client: TestClient, *device_ids: int) -> uuid.UUID:
    response = client.post(
        "/api/v1/jobs/dry-run",
        json={"device_ids": list(device_ids), "intent_source": "git_main"},
        headers=OPERATOR,
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "PENDING"
    return uuid.UUID(response.json()["job_id"])


def _deploy(client: TestClient, job_id: uuid.UUID) -> uuid.UUID:
    response = client.post(
        "/api/v1/jobs/deploy",
        json={"job_id": str(job_id), "confirmed_by": "duty-engineer"},
        headers=OPERATOR,
    )
    assert response.status_code == 202, response.text
    return uuid.UUID(response.json()["job_id"])


class TestDryRun:
    def test_in_sync_fabric(
        self,
        client: TestClient,
        devices: dict[str, Device],
        dispatcher: RecordingDispatcher,
        run_job: RunJob,
    ) -> None:
        job_id = _dry_run(client, *(device.id for device in devices.values()))
        assert dispatcher.job_ids == [job_id]

        pending = client.get(f"/api/v1/jobs/{job_id}", headers=VIEWER).json()
        assert pending["status"] == "PENDING"
        assert pending["created_by"] == "operator-user"
        assert client.get(f"/api/v1/jobs/{job_id}/diff", headers=VIEWER).status_code == 409

        assert run_job(job_id) is JobStatus.SUCCESS

        job = client.get(f"/api/v1/jobs/{job_id}", headers=VIEWER).json()
        assert job["status"] == "SUCCESS"
        assert job["progress"] == 100
        assert job["started_at"]
        assert job["finished_at"]
        assert {t["status"] for t in job["targets"]} == {"SUCCESS"}
        assert not any(t["has_changes"] for t in job["targets"])
        steps = [log["step"] for log in job["logs"]]
        assert steps[0] == "job"
        assert {"preflight", "render", "collect", "diff"} <= set(steps)

        diff = client.get(f"/api/v1/jobs/{job_id}/diff", headers=VIEWER).json()
        assert diff["job_type"] == "DRY_RUN"
        assert len(diff["devices"]) == 4
        for device in diff["devices"]:
            assert device["running_config"] == device["intended_config"]
            assert device["remediation_patch"] == ""
            assert "Building configuration" not in device["running_config"]

    def test_diff_shows_intended_change(
        self, client: TestClient, devices: dict[str, Device], intent_repo: Path, run_job: RunJob
    ) -> None:
        change_uplink_description(intent_repo, "leaf-1.croc.lab", "Uplink to spine-1 (400G)")
        job_id = _dry_run(client, devices["leaf-1.croc.lab"].id)
        run_job(job_id)

        [device] = client.get(f"/api/v1/jobs/{job_id}/diff", headers=VIEWER).json()["devices"]
        assert device["hostname"] == "leaf-1.croc.lab"
        assert " description Uplink to spine-1 (400G)" in device["intended_config"]
        assert " description Uplink to spine-1" in device["running_config"]
        assert "  description Uplink to spine-1 (400G)" in device["remediation_patch"]
        assert "  description Uplink to spine-1" in device["rollback_patch"]

    def test_unknown_devices(self, client: TestClient, devices: dict[str, Device]) -> None:
        response = client.post(
            "/api/v1/jobs/dry-run", json={"device_ids": [999, 998]}, headers=OPERATOR
        )
        assert response.status_code == 404
        assert response.json() == {"detail": "Unknown device id(s): 999, 998"}

    @pytest.mark.parametrize(
        "body",
        [{"device_ids": []}, {"device_ids": [1], "intent_source": "svn"}, {"device_ids": "all"}],
    )
    def test_request_validation(self, client: TestClient, body: dict[str, object]) -> None:
        assert client.post("/api/v1/jobs/dry-run", json=body, headers=OPERATOR).status_code == 422

    def test_duplicate_ids_are_collapsed(
        self, client: TestClient, devices: dict[str, Device], session: Session
    ) -> None:
        device_id = devices["leaf-1.croc.lab"].id
        job_id = _dry_run(client, device_id, device_id)
        assert len(session.get_one(Job, job_id).targets) == 1

    def test_queue_unavailable(
        self, client: TestClient, devices: dict[str, Device], dispatcher: RecordingDispatcher
    ) -> None:
        dispatcher.error = ConnectionError("redis is down")
        response = client.post(
            "/api/v1/jobs/dry-run",
            json={"device_ids": [devices["leaf-1.croc.lab"].id]},
            headers=OPERATOR,
        )
        assert response.status_code == 503

        [job] = client.get("/api/v1/jobs", headers=VIEWER).json()
        assert job["status"] == "FAILED"
        assert job["error"] == "Could not enqueue the job: redis is down"


class TestDeploy:
    def test_full_cycle(
        self,
        client: TestClient,
        devices: dict[str, Device],
        intent_repo: Path,
        lab_path: Path,
        run_job: RunJob,
        session: Session,
    ) -> None:
        change_uplink_description(intent_repo, "leaf-1.croc.lab", "Uplink to spine-1 (400G)")
        dry_run_id = _dry_run(client, devices["leaf-1.croc.lab"].id, devices["leaf-2.croc.lab"].id)
        run_job(dry_run_id)

        deploy_id = _deploy(client, dry_run_id)
        deploy = client.get(f"/api/v1/jobs/{deploy_id}", headers=VIEWER).json()
        assert deploy["type"] == "DEPLOY"
        assert deploy["parent_job_id"] == str(dry_run_id)
        assert deploy["confirmed_by"] == "duty-engineer"
        assert [t["hostname"] for t in deploy["targets"]] == ["leaf-1.croc.lab"]

        assert run_job(deploy_id) is JobStatus.SUCCESS
        assert "Uplink to spine-1 (400G)" in (lab_path / "leaf-1.croc.lab.cfg").read_text()
        assert session.get_one(Device, devices["leaf-1.croc.lab"].id).status is DeviceStatus.IN_SYNC

        steps = [
            log["step"]
            for log in client.get(f"/api/v1/jobs/{deploy_id}", headers=VIEWER).json()["logs"]
        ]
        assert (
            steps.index("pre-check")
            < steps.index("apply")
            < steps.index("post-check")
            < steps.index("commit")
        )

        diff = client.get(f"/api/v1/jobs/{deploy_id}/diff", headers=VIEWER).json()
        assert "(400G)" in diff["devices"][0]["remediation_patch"]

    def test_cannot_deploy_twice(
        self, client: TestClient, devices: dict[str, Device], intent_repo: Path, run_job: RunJob
    ) -> None:
        change_uplink_description(intent_repo, "leaf-1.croc.lab", "changed")
        dry_run_id = _dry_run(client, devices["leaf-1.croc.lab"].id)
        run_job(dry_run_id)
        deploy_id = _deploy(client, dry_run_id)

        response = client.post(
            "/api/v1/jobs/deploy",
            json={"job_id": str(dry_run_id), "confirmed_by": "x"},
            headers=OPERATOR,
        )
        assert response.status_code == 409
        assert str(deploy_id) in response.json()["detail"]

    def test_device_locked_by_another_change(
        self, client: TestClient, devices: dict[str, Device], intent_repo: Path, run_job: RunJob
    ) -> None:
        change_uplink_description(intent_repo, "leaf-1.croc.lab", "changed")
        first = _dry_run(client, devices["leaf-1.croc.lab"].id)
        second = _dry_run(client, devices["leaf-1.croc.lab"].id)
        run_job(first)
        run_job(second)
        _deploy(client, first)

        response = client.post(
            "/api/v1/jobs/deploy",
            json={"job_id": str(second), "confirmed_by": "x"},
            headers=OPERATOR,
        )
        assert response.status_code == 409
        assert "already in progress" in response.json()["detail"]

    def test_dry_run_without_changes(
        self, client: TestClient, devices: dict[str, Device], run_job: RunJob
    ) -> None:
        dry_run_id = _dry_run(client, devices["leaf-1.croc.lab"].id)
        run_job(dry_run_id)
        response = client.post(
            "/api/v1/jobs/deploy",
            json={"job_id": str(dry_run_id), "confirmed_by": "x"},
            headers=OPERATOR,
        )
        assert response.status_code == 409
        assert "no changes" in response.json()["detail"]

    def test_unfinished_or_failed_dry_run(
        self, client: TestClient, devices: dict[str, Device], lab_path: Path, run_job: RunJob
    ) -> None:
        dry_run_id = _dry_run(client, devices["leaf-1.croc.lab"].id)
        body = {"job_id": str(dry_run_id), "confirmed_by": "x"}
        assert client.post("/api/v1/jobs/deploy", json=body, headers=OPERATOR).status_code == 409

        (lab_path / "leaf-1.croc.lab.cfg").unlink()
        assert run_job(dry_run_id) is JobStatus.FAILED
        response = client.post("/api/v1/jobs/deploy", json=body, headers=OPERATOR)
        assert response.status_code == 409
        assert "only successful dry-runs" in response.json()["detail"]

    def test_only_dry_runs_can_be_deployed(
        self, client: TestClient, devices: dict[str, Device]
    ) -> None:
        scan = client.post("/api/v1/drift/scan", headers=OPERATOR).json()
        response = client.post(
            "/api/v1/jobs/deploy",
            json={"job_id": scan["job_id"], "confirmed_by": "x"},
            headers=OPERATOR,
        )
        assert response.status_code == 409
        assert "not a dry-run" in response.json()["detail"]

    def test_unknown_job(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/jobs/deploy",
            json={"job_id": str(uuid.uuid4()), "confirmed_by": "x"},
            headers=OPERATOR,
        )
        assert response.status_code == 404

    def test_confirmed_by_is_required(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/jobs/deploy",
            json={"job_id": str(uuid.uuid4()), "confirmed_by": ""},
            headers=OPERATOR,
        )
        assert response.status_code == 422


class TestHistory:
    def test_list_and_filters(
        self, client: TestClient, devices: dict[str, Device], run_job: RunJob
    ) -> None:
        first = _dry_run(client, devices["leaf-1.croc.lab"].id)
        run_job(first)
        scan = client.post("/api/v1/drift/scan", headers=OPERATOR).json()["job_id"]

        jobs = client.get("/api/v1/jobs", headers=VIEWER).json()
        assert [job["id"] for job in jobs] == [scan, str(first)]

        by_type = client.get("/api/v1/jobs", params={"type": "DRY_RUN"}, headers=VIEWER).json()
        assert [job["id"] for job in by_type] == [str(first)]
        by_status = client.get("/api/v1/jobs", params={"status": "PENDING"}, headers=VIEWER).json()
        assert [job["id"] for job in by_status] == [scan]

    def test_unknown_job(self, client: TestClient) -> None:
        missing = uuid.uuid4()
        assert client.get(f"/api/v1/jobs/{missing}", headers=VIEWER).status_code == 404
        assert client.get(f"/api/v1/jobs/{missing}/diff", headers=VIEWER).status_code == 404
        assert client.get("/api/v1/jobs/not-a-uuid", headers=VIEWER).status_code == 422


class TestIncrementalLogs:
    def test_only_newer_lines_are_returned(
        self, client: TestClient, devices: dict[str, Device], run_job: RunJob
    ) -> None:
        job_id = _dry_run(client, devices["spine-1.croc.lab"].id)
        run_job(job_id)

        everything = client.get(f"/api/v1/jobs/{job_id}/logs", headers=VIEWER).json()
        ids = [line["id"] for line in everything]
        assert ids == sorted(ids)
        assert everything[0]["message"] == "DRY_RUN job started"

        newer = client.get(
            f"/api/v1/jobs/{job_id}/logs", params={"after_id": ids[2]}, headers=VIEWER
        ).json()
        assert [line["id"] for line in newer] == ids[3:]
        page = client.get(f"/api/v1/jobs/{job_id}/logs", params={"limit": 2}, headers=VIEWER).json()
        assert [line["id"] for line in page] == ids[:2]
        after_last = client.get(
            f"/api/v1/jobs/{job_id}/logs", params={"after_id": ids[-1]}, headers=VIEWER
        )
        assert after_last.json() == []

    def test_unknown_job(self, client: TestClient) -> None:
        response = client.get(f"/api/v1/jobs/{uuid.uuid4()}/logs", headers=VIEWER)
        assert response.status_code == 404

    def test_sse_stream_once_and_after_id(
        self, client: TestClient, devices: dict[str, Device], run_job: RunJob
    ) -> None:
        from netops.services.state import get_state_manager

        job_id = _dry_run(client, devices["spine-1.croc.lab"].id)
        pending_stream = client.get(
            f"/api/v1/jobs/{job_id}/stream", params={"once": "true"}, headers=VIEWER
        )
        assert pending_stream.status_code == 200
        assert "text/event-stream" in pending_stream.headers["content-type"]
        assert "event: status" in pending_stream.text
        assert '"status": "PENDING"' in pending_stream.text

        assert run_job(job_id) is JobStatus.SUCCESS
        buf = get_state_manager().get_json(f"netops:job_stream:{job_id}")
        assert isinstance(buf, list) and len(buf) >= 2

        done_stream = client.get(
            f"/api/v1/jobs/{job_id}/stream", params={"once": "true"}, headers=VIEWER
        )
        assert done_stream.status_code == 200
        assert "DRY_RUN job started" in done_stream.text
        assert "event: done" in done_stream.text

        after_stream = client.get(
            f"/api/v1/jobs/{job_id}/stream",
            params={"once": "true", "after_id": buf[0]["id"]},
            headers=VIEWER,
        )
        assert after_stream.status_code == 200
        assert "DRY_RUN job started" not in after_stream.text
        assert "Job finished successfully" in after_stream.text
        assert "event: status" in after_stream.text
        assert "event: done" in after_stream.text
