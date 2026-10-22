from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from netops.enums import (
    DeviceRole,
    DeviceStatus,
    JobStatus,
    Platform,
    UserRole,
)
from netops.models import Device, Job
from netops.services import JobService
from netops.services.freeze import _reset_local_freeze, set_factory_freeze
from tests.conftest import RunJob, auth

ADMIN = auth(UserRole.ADMIN)
OPERATOR = auth(UserRole.OPERATOR)
VIEWER = auth(UserRole.VIEWER)


class TestDeviceFiltering:
    def test_list_and_filter_by_fabric_and_site(self, client: TestClient) -> None:
        # Sync inventory first so 4 default devices exist
        sync_resp = client.post("/api/v1/inventory/sync", headers=ADMIN)
        assert sync_resp.status_code == 200

        # Default devices all belong to croc-dc1-pod1
        resp = client.get("/api/v1/devices?fabric_id=croc-dc1-pod1", headers=VIEWER)
        assert resp.status_code == 200
        pod1_devices = resp.json()
        assert len(pod1_devices) == 4
        assert all(d["fabric_id"] == "croc-dc1-pod1" for d in pod1_devices)

        # Filter by non-existent fabric returns empty list
        resp = client.get("/api/v1/devices?fabric_id=nonexistent-stand", headers=VIEWER)
        assert resp.status_code == 200
        assert resp.json() == []

        # Create device in another fabric
        new_dev = {
            "hostname": "pod2-leaf1.croc.lab",
            "management_ip": "172.20.20.55",
            "platform": "arista_eos",
            "role": "leaf",
            "auth_profile": "lab",
            "fabric_id": "croc-dc2-pod1",
            "site": "dc2-spb",
        }
        create_resp = client.post("/api/v1/devices", json=new_dev, headers=ADMIN)
        assert create_resp.status_code == 201
        created = create_resp.json()
        assert created["fabric_id"] == "croc-dc2-pod1"
        assert created["site"] == "dc2-spb"

        # Query by fabric_id
        resp = client.get("/api/v1/devices?fabric_id=croc-dc2-pod1", headers=VIEWER)
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["hostname"] == "pod2-leaf1.croc.lab"

        # Query by site
        resp = client.get("/api/v1/devices?site=dc2-spb", headers=VIEWER)
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["hostname"] == "pod2-leaf1.croc.lab"

        # Update stand
        patch_resp = client.patch(
            f"/api/v1/devices/{created['id']}",
            json={"fabric_id": "croc-dc3-pod1"},
            headers=ADMIN,
        )
        assert patch_resp.status_code == 200

        # Check update
        resp = client.get(f"/api/v1/devices/{created['id']}", headers=VIEWER)
        assert resp.status_code == 200
        assert resp.json()["fabric_id"] == "croc-dc3-pod1"


class TestLintScoping:
    def test_lint_intent_scoped_endpoint(self, client: TestClient) -> None:
        # Default stand lint
        resp = client.get("/api/v1/intent/lint?fabric_id=croc-dc1-pod1", headers=VIEWER)
        assert resp.status_code == 200
        assert resp.json()["valid"] is True

        # Empty/non-existent stand lint
        resp = client.get("/api/v1/intent/lint?fabric_id=nonexistent", headers=VIEWER)
        assert resp.status_code == 200
        assert resp.json()["valid"] is True


class TestDriftScanScoping:
    def test_drift_scan_scoped_by_fabric(self, client: TestClient, session: Session) -> None:
        # Sync inventory first so 4 default devices exist
        sync_resp = client.post("/api/v1/inventory/sync", headers=ADMIN)
        assert sync_resp.status_code == 200

        # 1. Scoped scan for existing fabric
        resp = client.post(
            "/api/v1/drift/scan",
            json={"fabric_id": "croc-dc1-pod1"},
            headers=OPERATOR,
        )
        assert resp.status_code == 202
        job_id = uuid.UUID(resp.json()["job_id"])
        job = session.get(Job, job_id)
        assert job is not None
        assert len(job.targets) == 4

        # 2. Scoped scan for empty fabric returns 409 Conflict
        resp = client.post(
            "/api/v1/drift/scan",
            json={"fabric_id": "empty-stand"},
            headers=OPERATOR,
        )
        assert resp.status_code == 409
        assert "no devices to scan" in resp.json()["detail"].lower()

        # 3. Report filtered by fabric
        resp = client.get("/api/v1/drift/report?fabric_id=croc-dc1-pod1", headers=VIEWER)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestFreezeScoping:
    def test_freeze_scoped_endpoints(self, client: TestClient) -> None:
        _reset_local_freeze()
        try:
            # 1. Initially unfrozen
            resp = client.get("/api/v1/system/freeze", headers=VIEWER)
            assert resp.status_code == 200
            assert resp.json()["frozen"] is False

            # 2. Freeze specific stand
            resp = client.post(
                "/api/v1/system/freeze",
                json={"frozen": True, "fabric_id": "stand-99", "reason": "Fiber cut"},
                headers=OPERATOR,
            )
            assert resp.status_code == 200
            assert resp.json()["frozen"] is True
            assert resp.json()["fabric_id"] == "stand-99"

            # 3. Query stand-99 freeze
            resp = client.get("/api/v1/system/freeze?fabric_id=stand-99", headers=VIEWER)
            assert resp.status_code == 200
            assert resp.json()["frozen"] is True
            assert resp.json()["reason"] == "Fiber cut"

            # 4. Query another stand (unfrozen)
            resp = client.get("/api/v1/system/freeze?fabric_id=croc-dc1-pod1", headers=VIEWER)
            assert resp.status_code == 200
            assert resp.json()["frozen"] is False

            # 5. Global freeze status lists frozen stand
            resp = client.get("/api/v1/system/freeze", headers=VIEWER)
            assert resp.status_code == 200
            assert resp.json()["frozen"] is False
            assert "stand-99" in resp.json().get("fabrics", {})

            # 6. Unfreeze stand-99 via DELETE
            resp = client.delete("/api/v1/system/freeze?fabric_id=stand-99", headers=OPERATOR)
            assert resp.status_code == 200
            assert resp.json()["frozen"] is False

            # 7. Verify stand-99 is now unfrozen
            resp = client.get("/api/v1/system/freeze?fabric_id=stand-99", headers=VIEWER)
            assert resp.status_code == 200
            assert resp.json()["frozen"] is False
        finally:
            _reset_local_freeze()


class TestRunnerFreezeScoping:
    def test_stand_freeze_blocks_only_target_stand(
        self,
        session: Session,
        job_service: JobService,
        run_job: RunJob,
        devices: dict[str, Device],
    ) -> None:
        _reset_local_freeze()
        try:
            # Add a second device belonging to a different stand
            pod2_dev = Device(
                hostname="pod2-leaf1.croc.lab",
                management_ip="172.20.20.77",
                platform=Platform.ARISTA_EOS,
                role=DeviceRole.LEAF,
                auth_profile="lab",
                fabric_id="croc-dc2-pod1",
                status=DeviceStatus.DRIFT_DETECTED,
            )
            session.add(pod2_dev)
            session.commit()

            # Mark a pod1 device as drifted too
            pod1_dev = devices["leaf-1.croc.lab"]
            pod1_dev.status = DeviceStatus.DRIFT_DETECTED
            session.commit()

            # Freeze only croc-dc2-pod1
            set_factory_freeze(True, reason="Pod 2 power failure", fabric_id="croc-dc2-pod1")

            # 1. Attempt remediation on Pod 2 device (must be REJECTED)
            job_pod2 = job_service.create_drift_remediation(pod2_dev.id, requested_by="tester")
            status2 = run_job(job_pod2.id)
            assert status2 is JobStatus.FAILED
            job2 = session.get_one(Job, job_pod2.id)
            assert "Стенд croc-dc2-pod1 заморожен" in (job2.error or "")

            # 2. Attempt remediation on Pod 1 device (must NOT be blocked by Pod 2 freeze)
            job_pod1 = job_service.create_drift_remediation(pod1_dev.id, requested_by="tester")
            _ = run_job(job_pod1.id)
            # Job proceeds to pipeline execution (not blocked by freeze)
            assert "Стенд" not in (session.get_one(Job, job_pod1.id).error or "")
            assert "Фабрика заморожена" not in (session.get_one(Job, job_pod1.id).error or "")

            # 3. Global freeze blocks both stands
            set_factory_freeze(True, reason="Total blackout")
            session.get_one(Device, pod1_dev.id).status = DeviceStatus.DRIFT_DETECTED
            session.commit()
            job_pod1_global = job_service.create_drift_remediation(
                pod1_dev.id, requested_by="tester"
            )
            status_g = run_job(job_pod1_global.id)
            assert status_g is JobStatus.FAILED
            err = session.get_one(Job, job_pod1_global.id).error or ""
            assert "Фабрика заморожена оператором" in err
        finally:
            _reset_local_freeze()
