from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from netops.enums import DeviceStatus, JobStatus, LogLevel, TargetStatus
from netops.models import Job, JobLog

logger = logging.getLogger(__name__)

_PY_LEVELS = {
    LogLevel.INFO: logging.INFO,
    LogLevel.WARNING: logging.WARNING,
    LogLevel.ERROR: logging.ERROR,
}


class JobRecorder:
    def __init__(self, session: Session, job: Job) -> None:
        self._session = session
        self._job = job

    @property
    def job(self) -> Job:
        return self._job

    def info(self, step: str, message: str, *, hostname: str | None = None) -> None:
        self._log(LogLevel.INFO, step, message, hostname)

    def warning(self, step: str, message: str, *, hostname: str | None = None) -> None:
        self._log(LogLevel.WARNING, step, message, hostname)

    def error(self, step: str, message: str, *, hostname: str | None = None) -> None:
        self._log(LogLevel.ERROR, step, message, hostname)

    def progress(self, completed: int, total: int) -> None:
        self._job.progress = min(99, completed * 100 // total) if total else 0
        self._session.commit()

    def start(self) -> None:
        self._job.transition_to(JobStatus.RUNNING)
        self.info("job", f"{self._job.type} job started")

    def finish(self) -> None:
        self._skip_unprocessed_targets("Device was not processed")
        failed = [target.hostname for target in self._job.targets if target.status.is_failure]
        if failed:
            self.fail(
                f"{len(failed)} of {len(self._job.targets)} device(s) failed: {', '.join(failed)}"
            )
            return
        self._job.transition_to(JobStatus.SUCCESS)
        self.info("job", "Job finished successfully")

    def fail(self, error: str) -> None:
        self._skip_unprocessed_targets("Job failed before this device was processed")
        for target in self._job.targets:
            if target.device is not None and target.device.status is DeviceStatus.IN_PROGRESS:
                target.device.status = DeviceStatus.UNKNOWN
        self._job.transition_to(JobStatus.FAILED, error=error)
        self.error("job", error)

    def _skip_unprocessed_targets(self, reason: str) -> None:
        for target in self._job.targets:
            if target.status is TargetStatus.PENDING:
                target.mark(TargetStatus.SKIPPED, reason)

    # Коммитим после каждой строки лога и пушим в кольцевой буфер StateManager для SSE-стриминга.
    def _log(self, level: LogLevel, step: str, message: str, hostname: str | None) -> None:
        entry = JobLog(
            job_id=self._job.id, level=level, step=step, message=message, hostname=hostname
        )
        self._session.add(entry)
        self._session.commit()
        try:
            from netops.services.state import get_state_manager

            sm = get_state_manager()
            key = f"netops:job_stream:{self._job.id}"
            buf = sm.get_json(key)
            items = buf if isinstance(buf, list) else []
            items.append(
                {
                    "id": entry.id,
                    "step": step,
                    "hostname": hostname,
                    "level": level.value,
                    "message": message,
                    "created_at": entry.created_at.isoformat() if entry.created_at else None,
                    "progress": self._job.progress,
                    "status": self._job.status.value,
                }
            )
            sm.set_json(key, items[-200:], ex=600)
        except Exception:
            _ = None
        logger.log(
            _PY_LEVELS[level],
            "job=%s step=%s%s %s",
            self._job.id,
            step,
            f" host={hostname}" if hostname else "",
            message,
        )
