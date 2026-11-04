"""Database retention, SystemState compaction, and pre-collection log/memory cleaner.

Ensures unbounded operational tables (JobLog, DriftRecord, finished Job, unreferenced
ConfigSnapshot), SystemState JSON lists, in-memory state caches, and container `*.log` files
are deterministically compacted before telemetry collection or on schedule (analogous to
VictoriaMetrics retention, while never touching `lab/running/*.cfg`).
"""

from __future__ import annotations

import contextlib
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from netops.db import utcnow
from netops.enums import JobStatus
from netops.models import ConfigSnapshot, DriftRecord, Job, JobLog, JobTarget
from netops.models.system_state import SystemState

logger = logging.getLogger(__name__)

_SYSTEM_STATE_LIST_LIMITS: dict[str, int] = {
    "emergency_audit": 200,
    "chaos_events": 50,
    "auth:security_events": 50,
}

_TAIL_KEEP_BYTES = 2 * 1024 * 1024


def _compact_system_state_lists(session: Session) -> int:
    """Trim bounded SystemState JSON list keys in-place so audit arrays never bloat."""
    compacted = 0
    with contextlib.suppress(Exception):
        for key, max_items in _SYSTEM_STATE_LIST_LIMITS.items():
            row = session.get(SystemState, key)
            if row is not None and isinstance(row.value, list) and len(row.value) > max_items:
                row.value = list(row.value[-max_items:])
                row.updated_at = utcnow()
                compacted += 1
    return compacted


def purge_expired_records(
    session: Session,
    *,
    job_log_retention: timedelta = timedelta(days=14),
    now: datetime | None = None,
) -> dict[str, int]:
    """Purge expired JobLog, DriftRecord, finished Job, and old ConfigSnapshot rows."""
    ref_now = now or utcnow()
    cutoff = ref_now - job_log_retention

    job_log_res = session.execute(delete(JobLog).where(JobLog.created_at < cutoff))
    session.execute(delete(DriftRecord).where(DriftRecord.checked_at < cutoff))
    old_jobs_subq = (
        select(Job.id)
        .where(
            Job.status.in_((JobStatus.SUCCESS, JobStatus.FAILED)),
            Job.finished_at.is_not(None),
            Job.finished_at < cutoff,
        )
        .scalar_subquery()
    )
    session.execute(delete(JobLog).where(JobLog.job_id.in_(old_jobs_subq)))
    session.execute(delete(JobTarget).where(JobTarget.job_id.in_(old_jobs_subq)))
    session.execute(delete(Job).where(Job.id.in_(old_jobs_subq)))

    run_snap_ids = select(JobTarget.running_snapshot_id).where(
        JobTarget.running_snapshot_id.is_not(None)
    )
    int_snap_ids = select(JobTarget.intended_snapshot_id).where(
        JobTarget.intended_snapshot_id.is_not(None)
    )
    session.execute(
        delete(ConfigSnapshot).where(
            ConfigSnapshot.created_at < cutoff,
            ConfigSnapshot.id.not_in(run_snap_ids),
            ConfigSnapshot.id.not_in(int_snap_ids),
        )
    )
    _compact_system_state_lists(session)
    session.commit()
    return {"job_logs_purged": int(job_log_res.rowcount or 0)}


def _sweep_expired_memory_state() -> int:
    """Evict expired entries from in-memory StateManager stores to prevent memory leaks."""
    from netops.services.state import get_state_manager

    swept = 0
    now_ts = time.time()
    with contextlib.suppress(Exception):
        mgr = get_state_manager()
        with mgr._lock:
            for k, (exp, _) in list(mgr._local_kv.items()):
                if exp is not None and now_ts > exp:
                    mgr._local_kv.pop(k, None)
                    swept += 1
            for k, (exp, _) in list(mgr._local_locks.items()):
                if now_ts > exp:
                    mgr._local_locks.pop(k, None)
                    swept += 1
            for k, (exp, _) in list(mgr._local_git_sot.items()):
                if now_ts > exp:
                    mgr._local_git_sot.pop(k, None)
                    swept += 1
            for k, (exp, _) in list(mgr._local_templates.items()):
                if now_ts > exp:
                    mgr._local_templates.pop(k, None)
                    swept += 1
    return swept


def _truncate_oversized_logs(log_dir: Path, max_log_bytes: int) -> int:
    """Tail-truncate any *.log file in log_dir exceeding max_log_bytes (keeps last 2 MiB)."""
    truncated = 0
    if not log_dir.is_dir():
        return 0
    keep_bytes = min(_TAIL_KEEP_BYTES, max(1024, max_log_bytes))
    for log_file in log_dir.glob("*.log"):
        if not log_file.is_file() or log_file.is_symlink():
            continue
        with contextlib.suppress(OSError):
            size = log_file.stat().st_size
            if size > max_log_bytes:
                with open(log_file, "rb") as rf:
                    rf.seek(max(0, size - keep_bytes))
                    tail = rf.read()
                with open(log_file, "wb") as wf:
                    wf.write(tail)
                truncated += 1
    return truncated


def pre_collect_cleanup(
    session: Session | None = None,
    log_dir: str | Path = "/var/log/netops",
    max_log_bytes: int = 10 * 1024 * 1024,
) -> dict[str, int]:
    """Run lightweight pre-collection cleanup: sweep expired RAM keys, compact logs (DB purge — только daily beat)."""
    swept = _sweep_expired_memory_state()
    logs_truncated = _truncate_oversized_logs(Path(log_dir), max_log_bytes)
    job_logs_purged = 0
    return {
        "memory_keys_swept": swept,
        "logs_truncated": logs_truncated,
        "job_logs_purged": job_logs_purged,
    }
