from __future__ import annotations

import logging
import time

from sqlalchemy.orm import Session

from netops.db import utcnow
from netops.enums import DeviceStatus, DriftStatus, TargetStatus
from netops.models import Device, DriftRecord, JobTarget, sha256_hex
from netops.network.base import ChangePlan, DeviceTarget, HealthExpectations, HealthSnapshot
from netops.network.health import HealthVerdict, evaluate_health
from netops.network.ssh_locks import SSHLockUnavailableError
from netops.pipeline.recorder import JobRecorder
from netops.toolchain import Toolchain

logger = logging.getLogger(__name__)


class DeploymentExecutor:
    def __init__(self, session: Session, toolchain: Toolchain, recorder: JobRecorder) -> None:
        self._session = session
        self._toolchain = toolchain
        self._recorder = recorder

    def deploy(
        self,
        row: JobTarget,
        device: Device,
        target: DeviceTarget,
        plan: ChangePlan,
        *,
        expectations: HealthExpectations | None = None,
        expected_running_sha256: str | None = None,
    ) -> bool:
        previous_status = device.status
        self._set_status(device, DeviceStatus.IN_PROGRESS)

        # Патч считался от конкретного running-config; если конфиг на устройстве поменялся,
        # применять его нельзя.
        if expected_running_sha256 is not None:
            stale_reason = self._check_not_stale(device, target, expected_running_sha256)
            if stale_reason is not None:
                return self._fail(row, device, previous_status, stale_reason)

        self._recorder.info("pre-check", "Capturing health state", hostname=device.hostname)
        try:
            before = self._toolchain.health_probe.snapshot(target, expectations)
        except Exception as exc:
            return self._fail(row, device, previous_status, f"Pre-check failed: {exc}")

        timeout = self._toolchain.confirm_timeout_seconds
        self._recorder.info(
            "apply",
            f"Applying {len(plan.remediation.splitlines())} line(s), confirm timeout {timeout}s",
            hostname=device.hostname,
        )
        try:
            self._toolchain.deployer.apply(target, plan, confirm_timeout=timeout)
        except SSHLockUnavailableError as exc:
            return self._fail(
                row,
                device,
                previous_status,
                f"Apply aborted (SSH lock busy — no changes sent): {exc}",
            )
        except Exception as exc:
            rolled_back = self._rollback(device, target, plan)
            status = previous_status if rolled_back else DeviceStatus.UNKNOWN
            return self._fail(row, device, status, f"Apply failed: {exc}")

        verdict = self._post_check(device, target, before, expectations)
        if not verdict.healthy:
            for problem in verdict.problems:
                self._recorder.error("post-check", problem, hostname=device.hostname)
            if self._rollback(device, target, plan):
                row.mark(
                    TargetStatus.ROLLED_BACK, "Post-check failed: " + "; ".join(verdict.problems)
                )
                self._set_status(device, previous_status)
            else:
                row.mark(
                    TargetStatus.FAILED,
                    "Post-check failed and rollback failed; manual intervention required",
                )
                self._set_status(device, DeviceStatus.UNKNOWN)
            return False

        try:
            self._toolchain.deployer.confirm(target)
        except SSHLockUnavailableError as exc:
            return self._fail(
                row,
                device,
                DeviceStatus.UNKNOWN,
                f"Commit confirmation failed (SSH lock busy: {exc}); the device reverts when the timer expires",
            )
        except Exception as exc:
            return self._fail(
                row,
                device,
                DeviceStatus.UNKNOWN,
                f"Commit confirmation failed: {exc}; the device reverts when the timer expires",
            )

        now = utcnow()
        row.mark(TargetStatus.SUCCESS)
        device.last_checked_at = now
        self._session.add(
            DriftRecord(
                device_id=device.id,
                job_id=row.job_id,
                status=DriftStatus.IN_SYNC,
                checked_at=now,
            )
        )
        self._set_status(device, DeviceStatus.IN_SYNC)
        self._recorder.info("commit", "Change committed", hostname=device.hostname)
        return True

    def _post_check(
        self,
        device: Device,
        target: DeviceTarget,
        before: HealthSnapshot,
        expectations: HealthExpectations | None,
    ) -> HealthVerdict:
        attempts = self._toolchain.post_check_attempts
        interval = self._toolchain.post_check_interval_seconds
        self._recorder.info(
            "post-check", "Verifying health after the change", hostname=device.hostname
        )
        verdict = self._check_health(target, before, expectations)
        attempt = 1
        # BGP сходится не сразу: повторяем проверку, не выходя за таймер commit confirmed.
        while not verdict.healthy and attempt < attempts:
            self._recorder.warning(
                "post-check",
                f"Attempt {attempt}/{attempts} failed ({'; '.join(verdict.problems)}); "
                f"retrying in {interval:g}s",
                hostname=device.hostname,
            )
            time.sleep(interval)
            attempt += 1
            verdict = self._check_health(target, before, expectations)

        if not verdict.healthy:
            return verdict

        # Telemetry Guard: detect silent operational failures, blackholes & storms
        if getattr(self._toolchain, "ai_guard", None) is not None:
            self._recorder.info(
                "ai-guard", "Telemetry Guard: Verifying telemetry envelope during trial window", hostname=device.hostname
            )
            ai_verdict = self._toolchain.ai_guard.verify_execution(device)
            if not ai_verdict.healthy:
                self._recorder.error(
                    "ai-guard", f"Telemetry anomaly: {ai_verdict.problem}", hostname=device.hostname
                )
                return HealthVerdict((f"Telemetry Guard: {ai_verdict.problem}",))
            self._recorder.info(
                "ai-guard", f"Telemetry Guard: {ai_verdict.message}", hostname=device.hostname
            )

        return verdict


    def _check_health(
        self,
        target: DeviceTarget,
        before: HealthSnapshot,
        expectations: HealthExpectations | None,
    ) -> HealthVerdict:
        try:
            after = self._toolchain.health_probe.snapshot(target, expectations)
        except Exception as exc:
            return HealthVerdict((f"Post-check could not run: {exc}",))
        return evaluate_health(
            before,
            after,
            expected=expectations,
            max_ping_loss_percent=self._toolchain.max_ping_loss_percent,
        )

    def _check_not_stale(self, device: Device, target: DeviceTarget, expected: str) -> str | None:
        self._recorder.info(
            "verify",
            "Checking that the running-config is unchanged since the dry-run",
            hostname=device.hostname,
        )
        try:
            result = self._toolchain.collector.fetch_running_configs([target]).get(device.hostname)
        except Exception as exc:
            return f"Cannot verify the running-config: {exc}"
        if result is None or result.config is None:
            reason = result.error if result is not None else "no result from the collector"
            return f"Cannot verify the running-config: {reason}"
        current = self._toolchain.normalizer.normalize(device.platform, result.config)
        if sha256_hex(current) != expected:
            return "Running-config changed since the dry-run; run a new dry-run"
        return None

    def _rollback(self, device: Device, target: DeviceTarget, plan: ChangePlan) -> bool:
        self._recorder.warning("rollback", "Rolling back the change", hostname=device.hostname)
        try:
            self._toolchain.deployer.rollback(target, plan)
        except SSHLockUnavailableError as exc:
            logger.warning("Rollback SSH lock unavailable on %s: %s", device.hostname, exc)
            self._recorder.error(
                "rollback",
                f"Rollback failed (SSH lock busy): {exc}",
                hostname=device.hostname,
            )
            return False
        except Exception as exc:
            logger.warning("Rollback failed on %s", device.hostname, exc_info=True)
            self._recorder.error("rollback", f"Rollback failed: {exc}", hostname=device.hostname)
            return False

        # Two-Phase Rollback Protection: engage sync_lock on rolled-back device & fabric
        try:
            from netops.services.state import get_state_manager

            state_mgr = get_state_manager()
            job_obj = getattr(self._recorder, "_job", None)
            job_id_str = str(getattr(job_obj, "id", None) or getattr(plan, "job_id", None) or "rollback")
            holder_id = f"rollback:{job_id_str}"
            reason = "Two-Phase Rollback: target rolled back; locked pending SoT reconciliation"
            if device.id is not None:
                state_mgr.acquire_sync_lock(f"device:{device.id}", holder=holder_id, ttl=600, reason=reason)
            state_mgr.acquire_sync_lock(f"device:{device.hostname}", holder=holder_id, ttl=600, reason=reason)
            if getattr(device, "fabric_id", None):
                state_mgr.acquire_sync_lock(f"fabric:{device.fabric_id}", holder=holder_id, ttl=600, reason=reason)
            self._recorder.warning(
                "rollback",
                f"Sync lock engaged on device:{device.id} and fabric:{getattr(device, 'fabric_id', None)} (TTL 600s)",
                hostname=device.hostname,
            )
        except Exception as lock_exc:
            logger.warning("Failed to engage sync_lock on %s: %s", device.hostname, lock_exc)

        self._recorder.info("rollback", "Change rolled back", hostname=device.hostname)
        return True


    def _fail(self, row: JobTarget, device: Device, status: DeviceStatus, reason: str) -> bool:
        row.mark(TargetStatus.FAILED, reason)
        self._set_status(device, status)
        self._recorder.error("deploy", reason, hostname=device.hostname)
        return False

    def _set_status(self, device: Device, status: DeviceStatus) -> None:
        device.status = status
        self._session.commit()
