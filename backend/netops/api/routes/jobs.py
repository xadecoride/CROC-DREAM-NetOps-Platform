from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from netops.api.deps import ContainerDep, JobServiceDep, Operator, Viewer
from netops.enums import JobStatus, JobType
from netops.errors import NotFoundError
from netops.models import Job, JobLog
from netops.services.llm import RiskExplanation, explain_change_with_llm
from netops.schemas.jobs import (
    DeployRequest,
    DeviceDiffRead,
    DryRunRequest,
    JobAccepted,
    JobDiffRead,
    JobLogRead,
    JobRead,
    JobSummary,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post(
    "/dry-run",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Холостой прогон: рендер intent, сбор running-config и расчёт диффа",
)
def start_dry_run(request: DryRunRequest, service: JobServiceDep, user: Operator) -> JobAccepted:
    job = service.create_dry_run(
        request.device_ids, request.intent_source, requested_by=user.username
    )
    return JobAccepted(job_id=job.id, status=job.status)


@router.post(
    "/deploy",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Подтвердить dry-run и применить изменения транзакционно",
)
def start_deploy(request: DeployRequest, service: JobServiceDep, user: Operator) -> JobAccepted:
    job = service.create_deploy(
        request.job_id, confirmed_by=request.confirmed_by, requested_by=user.username
    )
    return JobAccepted(job_id=job.id, status=job.status)


@router.get("", response_model=list[JobSummary], summary="История задач")
def list_jobs(
    service: JobServiceDep,
    _: Viewer,
    job_type: Annotated[JobType | None, Query(alias="type")] = None,
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Job]:
    return list(
        service.list_jobs(job_type=job_type, status=status_filter, limit=limit, offset=offset)
    )


@router.get("/{job_id}", response_model=JobRead, summary="Статус, прогресс и логи задачи")
def get_job(job_id: uuid.UUID, service: JobServiceDep, _: Viewer) -> Job:
    return service.get(job_id)


@router.get(
    "/{job_id}/logs",
    response_model=list[JobLogRead],
    summary="Новые строки лога задачи после after_id",
)
def get_job_logs(
    job_id: uuid.UUID,
    service: JobServiceDep,
    _: Viewer,
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> list[JobLog]:
    return list(service.logs(job_id, after_id=after_id, limit=limit))


@router.get(
    "/{job_id}/diff",
    response_model=JobDiffRead,
    summary="Текущий и целевой конфиг, патчи наката и отката",
)
def get_job_diff(job_id: uuid.UUID, service: JobServiceDep, _: Viewer) -> JobDiffRead:
    job = service.get_with_diff(job_id)
    return JobDiffRead(
        job_id=job.id,
        job_type=job.type,
        devices=[
            DeviceDiffRead(
                device_id=target.device_id,
                hostname=target.hostname,
                status=target.status,
                error=target.error,
                running_config=target.running_snapshot.content if target.running_snapshot else None,
                intended_config=(
                    target.intended_snapshot.content if target.intended_snapshot else None
                ),
                remediation_patch=target.remediation_config,
                rollback_patch=target.rollback_config,
            )
            for target in job.targets
        ],
    )


@router.post(
    "/{job_id}/explain",
    response_model=RiskExplanation,
    summary="AI risk analysis for job configuration changes",
)
async def explain_job_diff(
    job_id: uuid.UUID,
    service: JobServiceDep,
    container: ContainerDep,
    _: Viewer,
    hostname: Annotated[str | None, Query(description="Filter by specific hostname")] = None,
) -> RiskExplanation:
    job = service.get_with_diff(job_id)
    target = None
    if hostname:
        for t in job.targets:
            if t.hostname == hostname:
                target = t
                break
        if not target:
            raise NotFoundError(f"Target device {hostname!r} not found in job {job_id}")
    else:
        target = next((t for t in job.targets if t.remediation_config), job.targets[0] if job.targets else None)

    if not target:
        raise NotFoundError(f"No targets found in job {job_id}")

    platform_str = target.device.platform.value if (target.device and hasattr(target.device, "platform")) else "cisco_iosxe"

    return await explain_change_with_llm(
        settings=container.settings,
        hostname=target.hostname,
        platform=platform_str,
        remediation_patch=target.remediation_config or "",
        rollback_patch=target.rollback_config or "",
    )
