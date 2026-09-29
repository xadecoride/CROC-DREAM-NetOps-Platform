"""Celery wiring: schedule, dispatcher and task bodies (executed in-process)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from netops.enums import JobStatus, JobType
from netops.models import Device, Job
from netops.pipeline import JobRunner
from netops.services import JobService
from netops.settings import Settings
from netops.toolchain import Toolchain
from netops.worker import tasks
from netops.worker.celery_app import (
    FAIL_STALE_JOBS_TASK,
    RUN_JOB_TASK,
    SCHEDULED_DRIFT_SCAN_TASK,
    CeleryJobDispatcher,
    create_celery_app,
)
from tests.conftest import RecordingDispatcher


@pytest.fixture
def runtime(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    session_factory: sessionmaker[Session],
    toolchain: Toolchain,
    dispatcher: RecordingDispatcher,
) -> tasks.WorkerRuntime:
    runtime = tasks.WorkerRuntime(
        settings=settings,
        session_factory=session_factory,
        runner=JobRunner(session_factory, lambda: toolchain),
        dispatcher=dispatcher,
    )
    monkeypatch.setattr(tasks, "get_runtime", lambda: runtime)
    return runtime


def test_beat_schedule_follows_settings(settings: Settings) -> None:
    app = create_celery_app(settings.model_copy(update={"drift_scan_interval_seconds": 900}))
    schedule = app.conf.beat_schedule
    assert schedule["drift-scan"] == {"task": SCHEDULED_DRIFT_SCAN_TASK, "schedule": 900.0}
    assert schedule["fail-stale-jobs"]["task"] == FAIL_STALE_JOBS_TASK
    assert app.conf.task_acks_late is True
    assert app.conf.worker_prefetch_multiplier == 1


def test_tasks_are_registered() -> None:
    registered = set(tasks.celery_app.tasks)
    assert {RUN_JOB_TASK, SCHEDULED_DRIFT_SCAN_TASK, FAIL_STALE_JOBS_TASK} <= registered


def test_celery_dispatcher_sends_the_job_by_name() -> None:
    sent: list[dict[str, Any]] = []

    class FakeCelery:
        def send_task(self, name: str, **options: Any) -> None:
            sent.append({"name": name, **options})

    job_id = uuid.uuid4()
    CeleryJobDispatcher(FakeCelery()).dispatch(job_id)
    assert sent == [{"name": RUN_JOB_TASK, "args": [str(job_id)], "task_id": str(job_id)}]


def test_run_job_task(
    runtime: tasks.WorkerRuntime, devices: dict[str, Device], session: Session
) -> None:
    job = JobService(session, runtime.dispatcher).create_drift_scan(None, requested_by="tester")
    assert tasks.run_job.apply(args=[str(job.id)]).get() == "SUCCESS"
    session.expire_all()
    assert session.get_one(Job, job.id).status is JobStatus.SUCCESS


def test_scheduled_scan_creates_one_scan_at_a_time(
    runtime: tasks.WorkerRuntime,
    devices: dict[str, Device],
    dispatcher: RecordingDispatcher,
    session: Session,
) -> None:
    job_id = tasks.scheduled_drift_scan.apply().get()
    assert job_id is not None
    job = session.get_one(Job, uuid.UUID(job_id))
    assert job.type is JobType.DRIFT_SCAN
    assert job.created_by == "scheduler"
    assert len(job.targets) == len(devices)
    assert dispatcher.job_ids == [job.id]

    # The previous scan is still pending: the next tick is skipped.
    assert tasks.scheduled_drift_scan.apply().get() is None
    assert len(dispatcher.job_ids) == 1


def test_scheduled_scan_without_devices(runtime: tasks.WorkerRuntime) -> None:
    assert tasks.scheduled_drift_scan.apply().get() is None


def test_fail_stale_task(runtime: tasks.WorkerRuntime) -> None:
    assert tasks.fail_stale.apply().get() == []
