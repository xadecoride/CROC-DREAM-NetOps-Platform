from __future__ import annotations

from datetime import datetime
from typing import Protocol

from sqlalchemy.orm import Session

from netops.db import utcnow
from netops.enums import DeviceStatus, DriftStatus, TargetStatus
from netops.errors import PipelineError
from netops.intent import InventoryDevice
from netops.models import Device, DriftRecord, Job, JobTarget
from netops.network.base import ChangePlan, HealthExpectations
from netops.pipeline.deployment import DeploymentExecutor
from netops.pipeline.planning import ChangePlanner, DevicePlan, load_intent, persist_plan
from netops.pipeline.recorder import JobRecorder
from netops.toolchain import Toolchain


class Pipeline(Protocol):
    def __init__(self, session: Session, toolchain: Toolchain, recorder: JobRecorder) -> None: ...

    def run(self, job: Job) -> None: ...


def serialize_job_target_diffs(job: Job) -> list[dict[str, str]]:
    return [
        {
            "hostname": row.hostname,
            "remediation_patch": row.remediation_config or "",
            "intended_config": row.intended_snapshot.content if row.intended_snapshot else "",
        }
        for row in job.targets
    ]


class _BasePipeline:
    def __init__(self, session: Session, toolchain: Toolchain, recorder: JobRecorder) -> None:
        self._session = session
        self._toolchain = toolchain
        self._recorder = recorder

    def _live_targets(self, job: Job) -> list[tuple[JobTarget, Device]]:
        live: list[tuple[JobTarget, Device]] = []
        for row in job.targets:
            if row.device is None:
                row.mark(TargetStatus.FAILED, "Device was deleted")
            else:
                live.append((row, row.device))
        return live

    def _plan(
        self, job: Job, pairs: list[tuple[JobTarget, Device]]
    ) -> list[tuple[JobTarget, DevicePlan]]:
        snapshot = load_intent(self._toolchain, self._recorder)
        planned: list[tuple[JobTarget, Device]] = []
        for row, device in pairs:
            is_server = (
                str(getattr(device.role, "value", device.role)) == "server"
                or str(getattr(device.platform, "value", device.platform)) == "linux_server"
            )
            is_mon_only = (
                str(getattr(device.management_mode, "value", device.management_mode))
                == "MONITORING_ONLY"
            )
            if (is_server or is_mon_only) and snapshot.intent_for(device.hostname) is None:
                self._recorder.info(
                    "plan", "Skipped: device without intent (monitoring only)", hostname=device.hostname
                )
                row.mark(TargetStatus.SKIPPED, "Monitoring-only device without intent")
                continue
            planned.append((row, device))
        plans = ChangePlanner(self._toolchain, self._recorder).plan(
            [device for _, device in planned], snapshot
        )
        results = []
        for (row, _), plan in zip(planned, plans, strict=True):
            persist_plan(self._session, job, row, plan)
            results.append((row, plan))
        self._session.commit()
        return results

    def _auto_commit_diff_to_git(self, job: Job) -> None:
        try:
            from netops.services.git_sot import git_commit_and_push_diff
            from netops.settings import get_settings

            repo_root = getattr(self._toolchain.intents, "root", None)
            if repo_root is None:
                return
            settings = get_settings()
            rt = settings.apply_runtime_overlay()
            device_diffs = serialize_job_target_diffs(job)
            res = git_commit_and_push_diff(
                repo_root,
                job_id=str(job.id),
                job_type=str(getattr(job.type, "value", job.type)),
                device_diffs=device_diffs,
                branch=str(rt.get("github_branch") or "main"),
                remote_url=str(rt.get("github_repo_url") or settings.github_repo_url or ""),
            )
            if res.get("status") != "skipped_pytest":
                self._recorder.info(
                    "git-sot",
                    f"Auto-commit to Git SoT: {res.get('branch', 'main')}@{res.get('commit_short', 'HEAD')} (pushed={res.get('pushed', False)})",
                )
        except Exception as exc:
            self._recorder.warning("git-sot", f"Auto-commit to Git SoT skipped: {exc}")

    @staticmethod
    def _is_device_sync_locked(device: Device) -> bool:
        from netops.services.state import get_state_manager

        state_mgr = get_state_manager()
        fab = getattr(device, "fabric_id", None)
        return bool(
            (device.id is not None and state_mgr.is_sync_locked(f"device:{device.id}"))
            or state_mgr.is_sync_locked(f"device:{device.hostname}")
            or (fab and state_mgr.is_sync_locked(f"fabric:{fab}"))
        )

    def _check_target_lock(self, row: JobTarget, device: Device) -> bool:
        if self._is_device_sync_locked(device):
            self._recorder.warning(
                "lock",
                "Skipped: target is locked after rollback/emergency patch (reset lab or wait for lock TTL)",
                hostname=device.hostname,
            )
            row.mark(TargetStatus.SKIPPED, "Target is locked during active deployment/rollback")
            return True
        return False


class DryRunPipeline(_BasePipeline):
    def run(self, job: Job) -> None:
        any_failed = False
        for row, plan in self._plan(job, self._live_targets(job)):
            if plan.error is not None:
                any_failed = True
                row.mark(TargetStatus.FAILED, plan.error)
                if plan.unreachable:
                    plan.device.status = DeviceStatus.UNREACHABLE
            else:
                row.mark(TargetStatus.SUCCESS)
        self._session.commit()
        if not any_failed:
            self._auto_commit_diff_to_git(job)



def order_targets_into_waves(targets: list[JobTarget]) -> list[tuple[str, list[JobTarget]]]:
    """Order deployment targets into topological waves: Canary Leaf -> Leaf Batch -> Spine Core."""
    leaves: list[JobTarget] = []
    spines: list[JobTarget] = []
    for row in targets:
        role_val = str(getattr(getattr(row.device, "role", None), "value", getattr(row.device, "role", ""))).lower()
        if role_val == "spine" or ("spine" in (row.hostname or "").lower() and role_val != "leaf"):
            spines.append(row)
        else:
            leaves.append(row)

    waves: list[tuple[str, list[JobTarget]]] = []
    if leaves:
        waves.append(("wave-1-canary-leaf", [leaves[0]]))
        if len(leaves) > 1:
            waves.append(("wave-2-leaf-batch", leaves[1:]))
    if spines:
        waves.append(("wave-3-spine-core", spines))
    return waves


# Устройства обновляем волнами (Canary Leaf -> Leaf Batch -> Spine); после первой ошибки раскатку останавливаем.
class DeployPipeline(_BasePipeline):
    def run(self, job: Job) -> None:
        executor = DeploymentExecutor(self._session, self._toolchain, self._recorder)
        failed_on: str | None = None
        waves = order_targets_into_waves(list(job.targets))
        ordered_rows = [row for _, wave_rows in waves for row in wave_rows]
        total = len(ordered_rows)
        index = 0
        for wave_name, wave_rows in waves:
            if failed_on is None and wave_rows:
                self._recorder.info(
                    "wave",
                    f"Starting {wave_name} ({len(wave_rows)} target(s): {', '.join(r.hostname for r in wave_rows)})",
                )
            for row in wave_rows:
                index += 1
                if failed_on is not None:
                    row.mark(TargetStatus.SKIPPED, f"Rollout stopped after the failure on {failed_on}")
                    continue
                if "spine" in wave_name or "leaf" in wave_name:
                    self._recorder.info(
                        "drain",
                        f"BGP Graceful Drain (GSHUT 65535:0) pre-check before deploying {row.hostname}",
                        hostname=row.hostname,
                    )
                if not self._deploy_row(executor, row):
                    failed_on = row.hostname
                self._recorder.progress(index, total)
        self._session.commit()
        if failed_on is None:
            self._auto_commit_diff_to_git(job)

    def _deploy_row(self, executor: DeploymentExecutor, row: JobTarget) -> bool:
        device = row.device
        if device is None:
            row.mark(TargetStatus.FAILED, "Device was deleted")
            return False
        if self._is_device_sync_locked(device):
            row.mark(
                TargetStatus.FAILED,
                "Target is locked after rollback/emergency patch (reset lab or wait for lock TTL)",
            )
            return False
        if row.intended_snapshot is None or row.running_snapshot is None:
            row.mark(TargetStatus.FAILED, "Dry-run results are incomplete; run a new dry-run")
            return False
        try:
            target = self._toolchain.target_for(InventoryDevice.model_validate(device))
        except PipelineError as exc:
            row.mark(TargetStatus.FAILED, str(exc))
            return False
        plan = ChangePlan(
            remediation=row.remediation_config or "",
            rollback=row.rollback_config or "",
            intended=row.intended_snapshot.content,
        )
        expectations = (
            HealthExpectations.from_json(row.health_expectations)
            if row.health_expectations is not None
            else None
        )
        return executor.deploy(
            row,
            device,
            target,
            plan,
            expectations=expectations,
            expected_running_sha256=row.running_snapshot.sha256,
        )


class DriftScanPipeline(_BasePipeline):
    def run(self, job: Job) -> None:
        pairs = []
        for row, device in self._live_targets(job):
            if self._check_target_lock(row, device):
                continue

            if device.status is DeviceStatus.IN_PROGRESS:
                row.mark(TargetStatus.SKIPPED, "A deployment is in progress")
            else:
                pairs.append((row, device))

        checked_at = utcnow()

        for row, plan in self._plan(job, pairs):
            device = plan.device
            if plan.error is not None and not plan.unreachable:
                row.mark(TargetStatus.FAILED, plan.error)
                continue
            record = self._record(job, plan, checked_at)
            self._session.add(record)
            if device.status is not DeviceStatus.IN_PROGRESS:
                device.status = DeviceStatus(record.status.value)
                device.last_checked_at = checked_at
            if plan.unreachable:
                row.mark(TargetStatus.FAILED, plan.error)
            else:
                row.mark(TargetStatus.SUCCESS)
                if record.status is DriftStatus.DRIFT_DETECTED:
                    self._recorder.warning(
                        "drift", "Configuration drift detected", hostname=device.hostname
                    )
        self._session.commit()

    @staticmethod
    def _record(job: Job, plan: DevicePlan, checked_at: datetime) -> DriftRecord:
        if plan.diff is None:
            return DriftRecord(
                device_id=plan.device.id,
                job_id=job.id,
                status=DriftStatus.UNREACHABLE,
                checked_at=checked_at,
                error=plan.error,
            )
        diff = plan.diff
        return DriftRecord(
            device_id=plan.device.id,
            job_id=job.id,
            status=DriftStatus.IN_SYNC if diff.in_sync else DriftStatus.DRIFT_DETECTED,
            checked_at=checked_at,
            unauthorized_lines=list(diff.unauthorized_lines),
            missing_lines=list(diff.missing_lines),
            remediation_config=diff.remediation or None,
            rollback_config=diff.rollback or None,
        )


# Компенсирующий патч считаем заново от текущего running-config и катим через обычный
# транзакционный деплой.
class DriftRemediationPipeline(_BasePipeline):
    def run(self, job: Job) -> None:
        pairs = self._live_targets(job)
        if not pairs:
            raise PipelineError("The device to remediate no longer exists")

        for row, device in pairs:
            if self._check_target_lock(row, device):
                self._session.commit()
                return

        plans = self._plan(job, pairs)
        if not plans:
            self._session.commit()
            return
        row, plan = plans[0]


        device = plan.device
        if plan.error is not None:
            row.mark(TargetStatus.FAILED, plan.error)
            if plan.unreachable:
                device.status = DeviceStatus.UNREACHABLE
            self._session.commit()
            return
        if plan.diff is None or not plan.diff.has_changes:
            row.mark(TargetStatus.SKIPPED, "Device already matches its golden config")
            device.status = DeviceStatus.IN_SYNC
            device.last_checked_at = utcnow()
            self._session.commit()
            return
        if plan.target is None:
            raise PipelineError(f"{device.hostname}: no management target")
        executor = DeploymentExecutor(self._session, self._toolchain, self._recorder)
        if executor.deploy(
            row, device, plan.target, plan.change_plan(), expectations=plan.expectations
        ):
            self._auto_commit_diff_to_git(job)
