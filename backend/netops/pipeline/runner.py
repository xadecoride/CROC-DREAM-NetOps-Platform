from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping
from datetime import timedelta
from types import MappingProxyType

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from netops.db import utcnow
from netops.enums import JobStatus, JobType
from netops.errors import PipelineError
from netops.models import Job
from netops.pipeline.pipelines import (
    DeployPipeline,
    DriftRemediationPipeline,
    DriftScanPipeline,
    DryRunPipeline,
    Pipeline,
)
from netops.pipeline.recorder import JobRecorder
from netops.toolchain import Toolchain

logger = logging.getLogger(__name__)

PIPELINES: Mapping[JobType, type[Pipeline]] = MappingProxyType(
    {
        JobType.DRY_RUN: DryRunPipeline,
        JobType.DEPLOY: DeployPipeline,
        JobType.DRIFT_SCAN: DriftScanPipeline,
        JobType.DRIFT_REMEDIATE: DriftRemediationPipeline,
    }
)


class JobRunner:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        toolchain_factory: Callable[[], Toolchain],
    ) -> None:
        self._session_factory = session_factory
        self._toolchain_factory = toolchain_factory

    def run(self, job_id: uuid.UUID) -> JobStatus | None:
        with self._session_factory() as session:
            # Блокировка строки: повторно доставленное сообщение Celery не запустит задачу дважды.
            job = session.get(Job, job_id, with_for_update=True)
            if job is None:
                logger.warning("Job %s does not exist", job_id)
                return None
            if job.status is not JobStatus.PENDING:
                logger.info("Job %s is already %s; skipping", job_id, job.status)
                return job.status

            recorder = JobRecorder(session, job)
            recorder.start()

            from netops.services.freeze import get_freeze_state, is_factory_frozen

            if job.type in (JobType.DEPLOY, JobType.DRIFT_REMEDIATE):
                if is_factory_frozen():
                    reason = get_freeze_state().get("reason") or "Экстренная блокировка"
                    recorder.fail(
                        f"Отклонено: Фабрика заморожена оператором (Kill Switch: {reason})"
                    )
                    return job.status

                target_fabrics: set[str] = set()
                for target in job.targets:
                    if target.device:
                        dev = target.device
                        fab = getattr(dev, "fabric_id", None) or getattr(dev, "site", None)
                        if fab:
                            target_fabrics.add(fab)
                frozen_stands = [
                    fab for fab in sorted(target_fabrics) if is_factory_frozen(fabric_id=fab)
                ]
                if frozen_stands:
                    stand_names = ", ".join(frozen_stands)
                    first_state = get_freeze_state(fabric_id=frozen_stands[0])
                    reason = first_state.get("reason") or "Экстренная блокировка"
                    recorder.fail(
                        f"Отклонено: Стенд {stand_names} заморожен оператором "
                        f"(Kill Switch: {reason})"
                    )
                    return job.status

            try:
                pipeline = PIPELINES[job.type](session, self._toolchain_factory(), recorder)

                pipeline.run(job)
            except PipelineError as exc:
                session.rollback()
                recorder.fail(str(exc))
            except Exception as exc:
                logger.exception("Job %s crashed", job_id)
                session.rollback()
                recorder.fail(f"Internal error: {type(exc).__name__}: {exc}")
            else:
                recorder.finish()

            try:
                import html

                from netops.services.notifier import send_telegram_message
                from netops.settings import get_settings

                settings = get_settings()
                if settings.telegram_bot_token and job.created_by != "scheduler":
                    targets_str = ", ".join(
                        f"{html.escape(str(t.hostname))} ({html.escape(str(t.status.value))})"
                        for t in job.targets
                    ) or "все узлы"
                    err_html = (
                        f"\n<b>Ошибка:</b> <code>{html.escape(str(job.error))}</code>"
                        if job.error
                        else ""
                    )
                    send_telegram_message(
                        settings,
                        f"<b>[NetOps Pipeline | {html.escape(str(job.type.value))}]</b> "
                        f"Статус: <b>{html.escape(str(job.status.value))}</b>\n"
                        f"<b>Автор:</b> <code>{html.escape(str(job.created_by))}</code>\n"
                        f"<b>Узлы:</b> <code>{targets_str}</code>"
                        + err_html,
                    )
            except Exception:
                _ = None
            return job.status


def fail_stale_jobs(session: Session, *, timeout: timedelta) -> list[uuid.UUID]:
    cutoff = utcnow() - timeout
    stale = session.scalars(
        select(Job)
        .where(
            or_(
                (Job.status == JobStatus.RUNNING) & (Job.started_at < cutoff),
                (Job.status == JobStatus.PENDING) & (Job.created_at < cutoff),
            )
        )
        .with_for_update(skip_locked=True)
    ).all()
    for job in stale:
        JobRecorder(session, job).fail(
            f"Job did not finish within {int(timeout.total_seconds())}s and was abandoned"
        )
    return [job.id for job in stale]
