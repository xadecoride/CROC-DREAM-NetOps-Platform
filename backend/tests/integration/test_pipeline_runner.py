from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from sqlalchemy.orm import Session

from netops.db import utcnow
from netops.enums import DeviceStatus, IntentSource, JobStatus, JobType, TargetStatus
from netops.models import Device, Job, JobTarget
from netops.network import ConfigDiff, DeviceTarget, FetchResult, JinjaConfigRenderer
from netops.pipeline import PIPELINES, fail_stale_jobs
from netops.services import JobService
from tests.conftest import RunJob


class SpyCollector:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.requested: list[str] = []

    def fetch_running_configs(self, targets: Sequence[DeviceTarget]) -> Mapping[str, FetchResult]:
        self.requested.extend(target.hostname for target in targets)
        if self.error is not None:
            raise self.error
        return {}


class ExplodingDiffEngine:
    def compare(self, platform: object, running: str, intended: str) -> ConfigDiff:
        raise ValueError("unparseable config")


def _dry_run(job_service: JobService, *devices: Device) -> Job:
    return job_service.create_dry_run(
        [device.id for device in devices], IntentSource.GIT_MAIN, requested_by="tester"
    )


def _targets(session: Session, job_id: uuid.UUID) -> dict[str, JobTarget]:
    return {target.hostname: target for target in session.get_one(Job, job_id).targets}


def test_lint_failure_never_reaches_the_network(
    devices: dict[str, Device],
    job_service: JobService,
    run_job: RunJob,
    session: Session,
    intent_repo: Path,
) -> None:
    path = intent_repo / "devices" / "spine-2.croc.lab.yaml"
    data = yaml.safe_load(path.read_text())
    data["bgp"]["router_id"] = "10.255.0.1"
    path.write_text(yaml.safe_dump(data))
    collector = SpyCollector()
    job = _dry_run(job_service, devices["leaf-1.croc.lab"])

    assert run_job(job.id, collector=collector) is JobStatus.FAILED
    assert collector.requested == []
    job = session.get_one(Job, job.id)
    assert job.error == "Pre-flight lint failed: 2 issue(s) in the intent"
    assert any("Router ID 10.255.0.1 is shared" in log.message for log in job.logs)
    assert job.targets[0].status is TargetStatus.SKIPPED
    assert job.progress == 0


def test_device_without_intent(job_service: JobService, run_job: RunJob, session: Session) -> None:
    device = Device(
        hostname="border-1.croc.lab",
        management_ip="172.20.20.31",
        platform="cisco_iosxe",
        role="border",
        auth_profile="lab",
    )
    session.add(device)
    session.commit()
    job = _dry_run(job_service, device)

    assert run_job(job.id) is JobStatus.FAILED
    target = _targets(session, job.id)["border-1.croc.lab"]
    assert target.status is TargetStatus.FAILED
    assert target.error == "No intent for border-1.croc.lab in the repository"


def test_unknown_auth_profile(
    devices: dict[str, Device], job_service: JobService, run_job: RunJob, session: Session
) -> None:
    session.get_one(Device, devices["leaf-1.croc.lab"].id).auth_profile = "vault"
    session.commit()
    job = _dry_run(job_service, devices["leaf-1.croc.lab"], devices["leaf-2.croc.lab"])

    assert run_job(job.id) is JobStatus.FAILED
    targets = _targets(session, job.id)
    assert targets["leaf-1.croc.lab"].error == "Auth profile 'vault' is not configured"
    assert targets["leaf-2.croc.lab"].status is TargetStatus.SUCCESS


def test_template_errors_are_per_device(
    devices: dict[str, Device],
    job_service: JobService,
    run_job: RunJob,
    session: Session,
    tmp_path: Path,
) -> None:
    templates = tmp_path / "templates"
    (templates / "cisco_iosxe").mkdir(parents=True)
    (templates / "cisco_iosxe" / "base.j2").write_text("hostname {{ intent.missing }}\n")
    job = _dry_run(job_service, devices["leaf-1.croc.lab"], devices["spine-1.croc.lab"])

    assert run_job(job.id, renderer=JinjaConfigRenderer(templates)) is JobStatus.FAILED
    targets = _targets(session, job.id)
    assert "cisco_iosxe/base.j2" in (targets["leaf-1.croc.lab"].error or "")
    assert "No templates found for platform arista_eos" in (targets["spine-1.croc.lab"].error or "")


def test_collector_crash_marks_devices_unreachable(
    devices: dict[str, Device], job_service: JobService, run_job: RunJob, session: Session
) -> None:
    job = _dry_run(job_service, devices["leaf-1.croc.lab"])
    collector = SpyCollector(error=TimeoutError("nornir inventory timeout"))

    assert run_job(job.id, collector=collector) is JobStatus.FAILED
    target = _targets(session, job.id)["leaf-1.croc.lab"]
    assert target.error == "Collection failed: nornir inventory timeout"
    assert session.get_one(Device, devices["leaf-1.croc.lab"].id).status is DeviceStatus.UNREACHABLE


def test_missing_collector_result(
    devices: dict[str, Device], job_service: JobService, run_job: RunJob, session: Session
) -> None:
    job = _dry_run(job_service, devices["leaf-1.croc.lab"])
    assert run_job(job.id, collector=SpyCollector()) is JobStatus.FAILED
    assert _targets(session, job.id)["leaf-1.croc.lab"].error == (
        "Cannot collect running-config: no result from the collector"
    )


def test_failed_dry_run_keeps_the_progress_it_reached(
    devices: dict[str, Device], job_service: JobService, run_job: RunJob, session: Session
) -> None:
    job = _dry_run(job_service, devices["leaf-1.croc.lab"])
    assert run_job(job.id, collector=SpyCollector()) is JobStatus.FAILED
    assert session.get_one(Job, job.id).progress == 66


def test_diff_errors_are_per_device(
    devices: dict[str, Device], job_service: JobService, run_job: RunJob, session: Session
) -> None:
    job = _dry_run(job_service, devices["leaf-1.croc.lab"])
    assert run_job(job.id, diff_engine=ExplodingDiffEngine()) is JobStatus.FAILED
    target = _targets(session, job.id)["leaf-1.croc.lab"]
    assert target.error == "Diff failed: unparseable config"
    assert target.running_snapshot is not None
    assert target.intended_snapshot is not None


def test_dry_run_snapshots_are_hashed(
    devices: dict[str, Device], job_service: JobService, run_job: RunJob, session: Session
) -> None:
    job = _dry_run(job_service, devices["spine-1.croc.lab"])
    run_job(job.id)
    target = _targets(session, job.id)["spine-1.croc.lab"]
    assert target.running_snapshot is not None
    assert target.running_snapshot.sha256 == target.intended_snapshot.sha256  # type: ignore[union-attr]
    assert target.running_snapshot.job_id == job.id


class TestRunner:
    def test_unknown_job(self, run_job: RunJob) -> None:
        assert run_job(uuid.uuid4()) is None

    def test_job_is_executed_once(
        self, devices: dict[str, Device], job_service: JobService, run_job: RunJob, session: Session
    ) -> None:
        job = _dry_run(job_service, devices["spine-1.croc.lab"])
        assert run_job(job.id) is JobStatus.SUCCESS
        logs = len(session.get_one(Job, job.id).logs)

        assert run_job(job.id) is JobStatus.SUCCESS
        assert len(session.get_one(Job, job.id).logs) == logs

    def test_crash_fails_the_job_and_releases_devices(
        self,
        devices: dict[str, Device],
        job_service: JobService,
        run_job: RunJob,
        session: Session,
        lab_path: Path,
        intent_repo: Path,
    ) -> None:
        path = intent_repo / "devices" / "leaf-1.croc.lab.yaml"
        data = yaml.safe_load(path.read_text())
        data["interfaces"][1]["description"] = "changed"
        path.write_text(yaml.safe_dump(data))
        dry_run = _dry_run(job_service, devices["leaf-1.croc.lab"])
        run_job(dry_run.id)
        deploy = job_service.create_deploy(dry_run.id, confirmed_by="duty", requested_by="tester")

        class CrashingNormalizer:
            def normalize(self, platform: object, text: str) -> str:
                raise KeyError("boom")

        status = run_job(deploy.id, normalizer=CrashingNormalizer())

        assert status is JobStatus.FAILED
        job = session.get_one(Job, deploy.id)
        assert job.error == "Internal error: KeyError: 'boom'"
        assert session.get_one(Device, devices["leaf-1.croc.lab"].id).status is DeviceStatus.UNKNOWN


def test_scan_skips_devices_being_deployed(
    devices: dict[str, Device], job_service: JobService, run_job: RunJob, session: Session
) -> None:
    session.get_one(Device, devices["leaf-1.croc.lab"].id).status = DeviceStatus.IN_PROGRESS
    session.commit()
    job = job_service.create_drift_scan(None, requested_by="tester")

    assert run_job(job.id) is JobStatus.SUCCESS
    targets = _targets(session, job.id)
    assert targets["leaf-1.croc.lab"].status is TargetStatus.SKIPPED
    assert targets["leaf-2.croc.lab"].status is TargetStatus.SUCCESS
    assert session.get_one(Device, devices["leaf-1.croc.lab"].id).status is DeviceStatus.IN_PROGRESS


class TestStaleJobs:
    def _job(self, session: Session, status: JobStatus, age: timedelta) -> Job:
        moment = utcnow() - age
        job = Job(
            type=JobType.DRIFT_SCAN,
            status=status,
            created_by="tester",
            created_at=moment,
            started_at=moment if status is JobStatus.RUNNING else None,
        )
        session.add(job)
        session.commit()
        return job

    def test_abandoned_jobs_are_failed(self, session: Session) -> None:
        stuck_running = self._job(session, JobStatus.RUNNING, timedelta(hours=2))
        lost_pending = self._job(session, JobStatus.PENDING, timedelta(hours=2))
        fresh = self._job(session, JobStatus.RUNNING, timedelta(minutes=5))

        failed = fail_stale_jobs(session, timeout=timedelta(hours=1))

        assert set(failed) == {stuck_running.id, lost_pending.id}
        session.expire_all()
        assert session.get_one(Job, stuck_running.id).status is JobStatus.FAILED
        assert "did not finish within 3600s" in (session.get_one(Job, lost_pending.id).error or "")
        assert session.get_one(Job, fresh.id).status is JobStatus.RUNNING

    def test_devices_are_released(self, session: Session, devices: dict[str, Device]) -> None:
        device = session.get_one(Device, devices["leaf-1.croc.lab"].id)
        device.status = DeviceStatus.IN_PROGRESS
        job = self._job(session, JobStatus.RUNNING, timedelta(hours=2))
        job.targets.append(JobTarget(device_id=device.id, hostname=device.hostname))
        session.commit()

        fail_stale_jobs(session, timeout=timedelta(hours=1))
        session.expire_all()
        assert session.get_one(Device, device.id).status is DeviceStatus.UNKNOWN


@pytest.mark.parametrize("job_type", list(JobType))
def test_every_job_type_has_a_pipeline(job_type: JobType) -> None:
    assert job_type in PIPELINES
