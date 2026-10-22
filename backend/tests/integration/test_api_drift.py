from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from netops.enums import DeviceStatus, DriftStatus, JobStatus, UserRole
from netops.models import Device, DriftRecord
from tests.conftest import RecordingDispatcher, RunJob, auth
from tests.integration.helpers import append_to_running_config

OPERATOR = auth(UserRole.OPERATOR)
VIEWER = auth(UserRole.VIEWER)

ROGUE_LOOPBACK = "interface Loopback99\n"


def _scan(client: TestClient, body: dict[str, object] | None = None) -> uuid.UUID:
    response = client.post("/api/v1/drift/scan", json=body, headers=OPERATOR)
    assert response.status_code == 202, response.text
    return uuid.UUID(response.json()["job_id"])


def _report(client: TestClient, **params: str) -> dict[str, dict[str, object]]:
    response = client.get("/api/v1/drift/report", params=params, headers=VIEWER)
    assert response.status_code == 200, response.text
    return {item["hostname"]: item for item in response.json()}


def test_scan_classifies_every_device(
    client: TestClient,
    devices: dict[str, Device],
    lab_path: Path,
    run_job: RunJob,
    session: Session,
) -> None:
    append_to_running_config(lab_path, "leaf-2.croc.lab", ROGUE_LOOPBACK)
    (lab_path / "spine-2.croc.lab.cfg").unlink()

    job_id = _scan(client)
    assert run_job(job_id) is JobStatus.FAILED

    statuses = {d.hostname: session.get_one(Device, d.id).status for d in devices.values()}
    assert statuses == {
        "leaf-1.croc.lab": DeviceStatus.IN_SYNC,
        "leaf-2.croc.lab": DeviceStatus.DRIFT_DETECTED,
        "spine-1.croc.lab": DeviceStatus.IN_SYNC,
        "spine-2.croc.lab": DeviceStatus.UNREACHABLE,
    }
    assert all(session.get_one(Device, d.id).last_checked_at for d in devices.values())

    report = _report(client)
    assert report["leaf-2.croc.lab"]["status"] == "DRIFT_DETECTED"
    assert report["leaf-2.croc.lab"]["unauthorized_lines"] == [ROGUE_LOOPBACK.strip()]
    assert report["leaf-2.croc.lab"]["missing_lines"] == []
    assert "no interface Loopback99" in str(report["leaf-2.croc.lab"]["remediation_patch"])
    assert report["spine-2.croc.lab"]["status"] == "UNREACHABLE"
    assert "unreachable" in str(report["spine-2.croc.lab"]["error"])
    assert report["leaf-1.croc.lab"]["job_id"] == str(job_id)

    drifted = _report(client, status="DRIFT_DETECTED")
    assert list(drifted) == ["leaf-2.croc.lab"]

    logs = client.get(f"/api/v1/jobs/{job_id}", headers=VIEWER).json()["logs"]
    assert any(log["step"] == "drift" and log["hostname"] == "leaf-2.croc.lab" for log in logs)


def test_scan_selected_devices(
    client: TestClient, devices: dict[str, Device], run_job: RunJob
) -> None:
    job_id = _scan(client, {"device_ids": [devices["spine-1.croc.lab"].id]})
    assert run_job(job_id) is JobStatus.SUCCESS
    assert list(_report(client)) == ["spine-1.croc.lab"]


def test_scan_requires_devices(client: TestClient) -> None:
    response = client.post("/api/v1/drift/scan", headers=OPERATOR)
    assert response.status_code == 409


def test_scan_rejects_empty_device_list(client: TestClient) -> None:
    response = client.post("/api/v1/drift/scan", json={"device_ids": []}, headers=OPERATOR)
    assert response.status_code == 422


def test_report_shows_only_the_latest_check(
    client: TestClient, devices: dict[str, Device], session: Session
) -> None:
    device = devices["leaf-1.croc.lab"]
    now = datetime.now(UTC)
    session.add_all(
        [
            DriftRecord(
                device_id=device.id,
                status=DriftStatus.DRIFT_DETECTED,
                checked_at=now - timedelta(hours=2),
                unauthorized_lines=["x"],
            ),
            DriftRecord(device_id=device.id, status=DriftStatus.IN_SYNC, checked_at=now),
        ]
    )
    session.commit()

    assert _report(client)["leaf-1.croc.lab"]["status"] == "IN_SYNC"
    assert _report(client, status="DRIFT_DETECTED") == {}
    until = (now - timedelta(hours=1)).isoformat()
    assert _report(client, until=until)["leaf-1.croc.lab"]["status"] == "DRIFT_DETECTED"
    assert _report(client, since=(now + timedelta(minutes=1)).isoformat()) == {}
    naive_until = (now - timedelta(hours=1)).replace(tzinfo=None).isoformat()
    assert _report(client, until=naive_until)["leaf-1.croc.lab"]["status"] == "DRIFT_DETECTED"


class TestRemediation:
    def test_remediate_drifted_device(
        self,
        client: TestClient,
        devices: dict[str, Device],
        lab_path: Path,
        run_job: RunJob,
        session: Session,
    ) -> None:
        append_to_running_config(lab_path, "leaf-2.croc.lab", ROGUE_LOOPBACK)
        run_job(_scan(client))
        device_id = devices["leaf-2.croc.lab"].id

        response = client.post(
            "/api/v1/drift/remediate", json={"device_id": device_id}, headers=OPERATOR
        )
        assert response.status_code == 202
        job_id = uuid.UUID(response.json()["job_id"])
        assert run_job(job_id) is JobStatus.SUCCESS

        assert "Loopback99" not in (lab_path / "leaf-2.croc.lab.cfg").read_text()
        assert session.get_one(Device, device_id).status is DeviceStatus.IN_SYNC
        assert _report(client)["leaf-2.croc.lab"]["status"] == "IN_SYNC"

        job = client.get(f"/api/v1/jobs/{job_id}", headers=VIEWER).json()
        assert job["type"] == "DRIFT_REMEDIATE"
        assert job["confirmed_by"] == "operator-user"

        run_job(_scan(client))
        assert session.get_one(Device, device_id).status is DeviceStatus.IN_SYNC

    def test_only_drifted_devices_can_be_remediated(
        self, client: TestClient, devices: dict[str, Device]
    ) -> None:
        response = client.post(
            "/api/v1/drift/remediate",
            json={"device_id": devices["leaf-1.croc.lab"].id},
            headers=OPERATOR,
        )
        assert response.status_code == 409
        assert "only drifted devices" in response.json()["detail"]

    def test_unknown_device(self, client: TestClient) -> None:
        response = client.post("/api/v1/drift/remediate", json={"device_id": 42}, headers=OPERATOR)
        assert response.status_code == 404

    def test_drift_fixed_by_hand_before_remediation(
        self,
        client: TestClient,
        devices: dict[str, Device],
        lab_path: Path,
        run_job: RunJob,
        session: Session,
    ) -> None:
        config = (lab_path / "leaf-2.croc.lab.cfg").read_text()
        append_to_running_config(lab_path, "leaf-2.croc.lab", ROGUE_LOOPBACK)
        run_job(_scan(client))
        (lab_path / "leaf-2.croc.lab.cfg").write_text(config)

        device_id = devices["leaf-2.croc.lab"].id
        job_id = client.post(
            "/api/v1/drift/remediate", json={"device_id": device_id}, headers=OPERATOR
        ).json()["job_id"]
        assert run_job(uuid.UUID(job_id)) is JobStatus.SUCCESS

        job = client.get(f"/api/v1/jobs/{job_id}", headers=VIEWER).json()
        assert job["targets"][0]["status"] == "SKIPPED"
        assert session.get_one(Device, device_id).status is DeviceStatus.IN_SYNC

    def test_second_remediation_is_rejected_while_first_is_pending(
        self,
        client: TestClient,
        devices: dict[str, Device],
        lab_path: Path,
        run_job: RunJob,
        dispatcher: RecordingDispatcher,
    ) -> None:
        append_to_running_config(lab_path, "leaf-2.croc.lab", ROGUE_LOOPBACK)
        run_job(_scan(client))
        body = {"device_id": devices["leaf-2.croc.lab"].id}
        assert (
            client.post("/api/v1/drift/remediate", json=body, headers=OPERATOR).status_code == 202
        )
        assert (
            client.post("/api/v1/drift/remediate", json=body, headers=OPERATOR).status_code == 409
        )
        assert len(dispatcher.job_ids) == 2

    def test_safe_and_risky_drift_breakdown(
        self,
        client: TestClient,
        devices: dict[str, Device],
        session: Session,
    ) -> None:
        now = datetime.now(UTC)
        device = devices["leaf-1.croc.lab"]
        session.add(
            DriftRecord(
                device_id=device.id,
                status=DriftStatus.DRIFT_DETECTED,
                checked_at=now,
                unauthorized_lines=["description Backup Link", "router bgp 65000", "interface GigabitEthernet2"],
                missing_lines=["ntp server 1.1.1.1"],
            )
        )
        session.commit()

        item = _report(client)["leaf-1.croc.lab"]
        assert item["status"] == "DRIFT_DETECTED"
        assert item["risk_level"] == "HIGH"
        assert "description Backup Link" in item["safe_lines"]
        assert "ntp server 1.1.1.1" in item["safe_lines"]
        assert "router bgp 65000" in item["risky_lines"]
        assert "interface GigabitEthernet2" in item["risky_lines"]
