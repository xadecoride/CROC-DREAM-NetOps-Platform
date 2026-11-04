from __future__ import annotations

import asyncio
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from netops.api.deps import ContainerDep, JobServiceDep, Operator, Viewer
from netops.enums import JobStatus, JobType
from netops.errors import NotFoundError
from netops.models import Job, JobLog
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
from netops.services.llm import RiskExplanation, explain_change_with_llm

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post(
    "/dry-run",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Холостой прогон: рендер intent, сбор running-config и расчёт диффа",
)
def start_dry_run(request: DryRunRequest, service: JobServiceDep, user: Operator) -> JobAccepted:
    if user.allowed_system:
        from sqlalchemy import select

        from netops.api.routes.devices import assert_device_allowed
        from netops.models import Device

        stmt = select(Device)
        if request.device_ids:
            stmt = stmt.where(Device.id.in_(request.device_ids))
        for dev in service._session.scalars(stmt):
            assert_device_allowed(dev, user.allowed_system)
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
    if user.allowed_system:
        dry_job = service.get(request.job_id)
        _assert_job_allowed(dry_job, user.allowed_system)
    job = service.create_deploy(
        request.job_id, confirmed_by=request.confirmed_by, requested_by=user.username
    )
    return JobAccepted(job_id=job.id, status=job.status)


@router.get("", response_model=list[JobSummary], summary="История задач")
def list_jobs(
    service: JobServiceDep,
    user: Viewer,
    job_type: Annotated[JobType | None, Query(alias="type")] = None,
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
    fabric_id: Annotated[str | None, Query(description="Фильтр по стенду/фабрике")] = None,
    site: Annotated[str | None, Query(description="Фильтр по площадке/стенду")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Job]:
    from netops.api.routes.devices import assert_stand_allowed, device_allowed

    stand = fabric_id or site
    if user.allowed_system and stand:
        assert_stand_allowed(fabric_id or "", site, user.allowed_system)

    jobs = list(
        service.list_jobs(job_type=job_type, status=status_filter, limit=limit, offset=offset)
    )
    if stand:
        jobs = [
            j
            for j in jobs
            if any(
                t.device is not None and stand in {t.device.fabric_id, t.device.site}
                for t in j.targets
            )
        ]
    if user.allowed_system:
        jobs = [
            j
            for j in jobs
            if j.targets
            and all(
                t.device is not None and device_allowed(t.device, user.allowed_system)
                for t in j.targets
            )
        ]
    return jobs


def _assert_job_allowed(job: Job, allowed_system: str | None) -> None:
    if not allowed_system or allowed_system in ("*", "all"):
        return
    from netops.api.routes.devices import assert_device_allowed, device_allowed, stand_allowed

    payload = getattr(job, "payload", None)
    if isinstance(payload, dict):
        stand = payload.get("stand") or payload.get("fabric_id")
        if stand and not stand_allowed(stand, None, allowed_system):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Доступ к задаче ограничен: стенд '{stand}' не разрешён",
            )
        devs = payload.get("devices") or []
        if isinstance(devs, list) and devs:
            for h in devs:
                h_name = h if isinstance(h, str) else getattr(h, "hostname", "")
                if h_name and not device_allowed({"hostname": h_name}, allowed_system):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Доступ к устройству {h_name} ограничен",
                    )

    if not job.targets:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ к задаче ограничен для вашего стенда",
        )

    for target in job.targets:
        if target.device is not None:
            assert_device_allowed(target.device, allowed_system)
        elif target.hostname:
            if not device_allowed({"hostname": target.hostname}, allowed_system):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Доступ к устройству {target.hostname} ограничен",
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Доступ к задаче ограничен",
            )


@router.get("/{job_id}", response_model=JobRead, summary="Статус, прогресс и логи задачи")
def get_job(job_id: uuid.UUID, service: JobServiceDep, user: Viewer) -> Job:
    job = service.get(job_id)
    _assert_job_allowed(job, user.allowed_system)
    return job


@router.get(
    "/{job_id}/logs",
    response_model=list[JobLogRead],
    summary="Новые строки лога задачи после after_id",
)
def get_job_logs(
    job_id: uuid.UUID,
    service: JobServiceDep,
    user: Viewer,
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> list[JobLog]:
    if user.allowed_system:
        _assert_job_allowed(service.get(job_id), user.allowed_system)
    return list(service.logs(job_id, after_id=after_id, limit=limit))


@router.get(
    "/{job_id}/stream",
    summary="Live Server-Sent Events (SSE) stream for job progress and terminal logs",
)
async def stream_job_logs(
    job_id: uuid.UUID,
    service: JobServiceDep,
    container: ContainerDep,
    request: Request,
    user: Viewer,
    after_id: Annotated[int, Query(ge=0)] = 0,
    once: Annotated[bool, Query()] = False,
) -> StreamingResponse:
    initial_job = service.get(job_id)
    _assert_job_allowed(initial_job, user.allowed_system)
    service._session.close()

    def _poll_step(cur_after_id: int) -> tuple[list[tuple[int, dict]], dict | None, dict | None]:
        from netops.services.jobs import JobService

        with container.session_factory() as poll_session:
            poll_svc = JobService(poll_session, container.dispatcher)
            job = poll_svc.get(job_id)
            new_logs = list(poll_svc.logs(job_id, after_id=cur_after_id, limit=200))
            items = [
                (
                    log.id,
                    {
                        "id": log.id,
                        "step": log.step,
                        "hostname": log.hostname,
                        "level": log.level.value if hasattr(log.level, "value") else str(log.level),
                        "message": log.message,
                        "created_at": log.created_at.isoformat() if log.created_at else None,
                        "progress": job.progress,
                        "status": job.status.value,
                    },
                )
                for log in new_logs
            ]
            is_terminal = job.status in {JobStatus.SUCCESS, JobStatus.FAILED}
            status_ev = (
                {
                    "event": "status",
                    "job_id": str(job.id),
                    "status": job.status.value,
                    "progress": job.progress,
                    "error": job.error,
                }
                if (is_terminal or once)
                else None
            )
            done_ev = (
                {
                    "event": "done",
                    "status": job.status.value,
                    "progress": job.progress,
                    "error": job.error,
                }
                if is_terminal
                else None
            )
            return items, status_ev, done_ev

    async def event_generator():
        last_id = after_id
        while True:
            if await request.is_disconnected():
                break

            items, status_data, final_data = await asyncio.to_thread(_poll_step, last_id)
            for log_id, data in items:
                last_id = max(last_id, log_id)
                yield f"data: {json.dumps(data)}\n\n"

            if status_data is not None:
                yield f"event: status\ndata: {json.dumps(status_data)}\n\n"
            if final_data is not None:
                yield f"event: done\ndata: {json.dumps(final_data)}\n\n"
                break
            if once:
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/{job_id}/diff",
    response_model=JobDiffRead,
    summary="Текущий и целевой конфиг, патчи наката и отката",
)
def get_job_diff(
    job_id: uuid.UUID, service: JobServiceDep, container: ContainerDep, user: Viewer
) -> JobDiffRead:
    job = service.get(job_id)
    _assert_job_allowed(job, user.allowed_system)
    job = service.get_with_diff(job_id)
    toolchain = None
    devices_out: list[DeviceDiffRead] = []
    for target in job.targets:
        if target.device_id is None:
            continue
        intended_cfg = target.intended_snapshot.content if target.intended_snapshot else None
        if not intended_cfg and target.device is not None:
            try:
                from netops.intent import InventoryDevice
                from netops.toolchain import build_toolchain

                if toolchain is None:
                    toolchain = build_toolchain(container.settings)
                intent = toolchain.intents.load_device_intent(target.hostname)
                if intent is not None:
                    spec = InventoryDevice.model_validate(target.device)
                    rendered = toolchain.renderer.render(spec, intent)
                    intended_cfg = toolchain.normalizer.normalize(target.device.platform, rendered)
            except Exception:
                _ = None
        devices_out.append(
            DeviceDiffRead(
                device_id=target.device_id,
                hostname=target.hostname,
                status=target.status,
                error=target.error,
                running_config=target.running_snapshot.content if target.running_snapshot else None,
                intended_config=intended_cfg,
                remediation_patch=target.remediation_config,
                rollback_patch=target.rollback_config,
            )
        )
    return JobDiffRead(
        job_id=job.id,
        job_type=job.type,
        devices=devices_out,
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
    user: Viewer,
    hostname: Annotated[str | None, Query(description="Filter by specific hostname")] = None,
) -> RiskExplanation:
    job = service.get(job_id)
    _assert_job_allowed(job, user.allowed_system)
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
        target = next(
            (t for t in job.targets if t.remediation_config),
            job.targets[0] if job.targets else None,
        )

    if not target:
        raise NotFoundError(f"No targets found in job {job_id}")

    platform_str = (
        target.device.platform.value
        if (target.device and hasattr(target.device, "platform"))
        else "cisco_iosxe"
    )

    return await explain_change_with_llm(
        settings=container.settings,
        hostname=target.hostname,
        platform=platform_str,
        remediation_patch=target.remediation_config or "",
        rollback_patch=target.rollback_config or "",
    )
