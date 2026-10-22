from __future__ import annotations

import contextlib
import uuid
import zlib
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from netops.db import build_engine, build_session_factory
from netops.models import Device
from netops.pipeline import JobRunner, fail_stale_jobs
from netops.services import JobDispatcher, JobService
from netops.services.retention import pre_collect_cleanup, purge_expired_records
from netops.services.state import get_state_manager
from netops.settings import Settings, get_settings
from netops.telemetry import collector, targets
from netops.toolchain import build_toolchain
from netops.worker.celery_app import (
    COLLECT_METRICS_TASK,
    COLLECT_STAND_TASK,
    DISPATCH_DRIFT_SCAN_TASK,
    DISPATCH_METRICS_TASK,
    FAIL_STALE_JOBS_TASK,
    GENERATE_REGULAR_REPORT_TASK,
    PURGE_EXPIRED_RECORDS_TASK,
    REFRESH_COLLECTOR_TARGETS_TASK,
    RUN_JOB_TASK,
    SCHEDULED_DRIFT_SCAN_TASK,
    CeleryJobDispatcher,
    create_celery_app,
)

celery_app = create_celery_app(get_settings())


@dataclass(frozen=True)
class WorkerRuntime:
    settings: Settings
    session_factory: sessionmaker[Session]
    runner: JobRunner
    dispatcher: JobDispatcher


# Создаётся лениво в каждом процессе воркера, уже после fork.
@lru_cache(maxsize=1)
def get_runtime() -> WorkerRuntime:
    settings = get_settings()
    session_factory = build_session_factory(build_engine(settings.database_url))
    return WorkerRuntime(
        settings=settings,
        session_factory=session_factory,
        runner=JobRunner(session_factory, lambda: build_toolchain(settings)),
        dispatcher=CeleryJobDispatcher(celery_app),
    )


@celery_app.task(name=RUN_JOB_TASK)  # type: ignore[untyped-decorator]
def run_job(job_id: str) -> str | None:
    status = get_runtime().runner.run(uuid.UUID(job_id))
    return status.value if status is not None else None


@celery_app.task(name=SCHEDULED_DRIFT_SCAN_TASK)  # type: ignore[untyped-decorator]
def scheduled_drift_scan(fabric_id: str | None = None) -> str | None:
    runtime = get_runtime()
    with runtime.session_factory() as session:
        job = JobService(session, runtime.dispatcher).schedule_drift_scan(fabric_id)
        return str(job.id) if job is not None else None


def _stand_ids(session: Session) -> list[str]:
    return sorted(set(session.scalars(select(Device.fabric_id))))


# Beat-диспетчер: drift scan по стендам со стаггером crc32(стенд) % interval (hash() рандомизирован).
@celery_app.task(name=DISPATCH_DRIFT_SCAN_TASK)  # type: ignore[untyped-decorator]
def dispatch_drift_scans() -> int:
    runtime = get_runtime()
    interval = runtime.settings.drift_scan_interval_seconds
    with runtime.session_factory() as session:
        stands = _stand_ids(session)
    for stand in stands:
        scheduled_drift_scan.apply_async(
            args=[stand], countdown=zlib.crc32(stand.encode()) % interval, expires=interval
        )
    return len(stands)


@celery_app.task(name=COLLECT_METRICS_TASK)  # type: ignore[untyped-decorator]
def collect_metrics() -> int:
    runtime = get_runtime()
    with runtime.session_factory() as session:
        samples = collector.collect(
            session, build_toolchain(runtime.settings), include_fabric_label=True
        )
        collector.publish(samples, ttl_seconds=runtime.settings.metrics_interval_seconds * 3)
        return len(samples)


# Beat-диспетчер: платформенные ряды + collect_stand на каждый стенд (expires=interval).
@celery_app.task(name=DISPATCH_METRICS_TASK)  # type: ignore[untyped-decorator]
def dispatch_metrics() -> int:
    runtime = get_runtime()
    interval = runtime.settings.metrics_interval_seconds
    with runtime.session_factory() as session:
        stands = _stand_ids(session)
        with contextlib.suppress(Exception):
            pre_collect_cleanup()
        collector.publish(
            collector.platform_samples(session),
            ttl_seconds=interval * 3,
            merge=True,
            live_fabrics=set(stands),
        )
    for stand in stands:
        collect_stand.apply_async(args=[stand], expires=interval)
    return len(stands)


# Один проход по стенду; single-flight Redis-лок не даёт наложиться медленным проходам.
@celery_app.task(name=COLLECT_STAND_TASK)  # type: ignore[untyped-decorator]
def collect_stand(fabric_id: str) -> int:
    runtime = get_runtime()
    interval = runtime.settings.metrics_interval_seconds
    sm = get_state_manager()
    lock, holder = f"telemetry:{fabric_id}", f"collect_stand:{uuid.uuid4()}"
    if not sm.acquire_sync_lock(lock, holder, ttl=interval * 3, reason="telemetry collection"):
        return -1
    try:
        with runtime.session_factory() as session:
            samples = collector.device_samples(
                session,
                build_toolchain(runtime.settings),
                max_workers=16,
                include_fabric_label=True,
                fabric_id=fabric_id,
            )
        collector.publish(samples, ttl_seconds=interval * 3, merge=True, merge_fabric=fabric_id)
        return len(samples)
    finally:
        sm.release_sync_lock(lock, holder)


@celery_app.task(name=REFRESH_COLLECTOR_TARGETS_TASK)  # type: ignore[untyped-decorator]
def refresh_collector_targets() -> dict[str, int]:
    runtime = get_runtime()
    with runtime.session_factory() as session:
        plan = targets.refresh_collector_targets(
            session, build_toolchain(runtime.settings), runtime.settings
        )
        return plan["protocols"]


@celery_app.task(name=FAIL_STALE_JOBS_TASK)  # type: ignore[untyped-decorator]
def fail_stale() -> list[str]:
    runtime = get_runtime()
    with runtime.session_factory() as session:
        failed = fail_stale_jobs(
            session, timeout=timedelta(seconds=runtime.settings.job_timeout_seconds)
        )
        return [str(job_id) for job_id in failed]


@celery_app.task(name=PURGE_EXPIRED_RECORDS_TASK)  # type: ignore[untyped-decorator]
def purge_expired() -> dict[str, int]:
    runtime = get_runtime()
    with runtime.session_factory() as session:
        return purge_expired_records(session)


@celery_app.task(name=GENERATE_REGULAR_REPORT_TASK)  # type: ignore[untyped-decorator]
def generate_regular_report() -> dict[str, object]:
    from datetime import UTC, datetime

    from sqlalchemy import select

    from netops.enums import DeviceStatus
    from netops.models import Device

    runtime = get_runtime()
    with runtime.session_factory() as session:
        devices = session.scalars(select(Device)).all()
        total = len(devices)
        in_sync = sum(1 for d in devices if d.status == DeviceStatus.IN_SYNC)
        drifted = sum(1 for d in devices if d.status == DeviceStatus.DRIFT_DETECTED)
        unreachable = sum(1 for d in devices if d.status == DeviceStatus.UNREACHABLE)
        unknown = sum(1 for d in devices if d.status == DeviceStatus.UNKNOWN)
        compliance_pct = round((in_sync / total) * 100.0, 2) if total > 0 else 0.0
        summary = {
            "generated_at": datetime.now(UTC).isoformat(),
            "total_devices": total,
            "in_sync": in_sync,
            "drift_detected": drifted,
            "unreachable": unreachable,
            "unknown": unknown,
            "compliance_pct": compliance_pct,
        }
        get_state_manager().save_state("regular_report", summary)
        return summary
