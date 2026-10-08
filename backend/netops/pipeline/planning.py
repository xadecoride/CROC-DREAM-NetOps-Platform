from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from netops.enums import SnapshotKind
from netops.errors import PipelineError
from netops.intent import IntentSnapshot, IntentValidationError, InventoryDevice
from netops.models import ConfigSnapshot, Device, Job, JobTarget
from netops.network.base import ChangePlan, ConfigDiff, DeviceTarget, HealthExpectations
from netops.pipeline.recorder import JobRecorder
from netops.toolchain import Toolchain

logger = logging.getLogger(__name__)

_PLANNING_STAGES = 3


def load_intent(toolchain: Toolchain, recorder: JobRecorder) -> IntentSnapshot:
    recorder.info("preflight", f"Validating intent repository {toolchain.intents.root}")
    try:
        snapshot = toolchain.intents.load()
    except IntentValidationError as exc:
        for issue in exc.issues:
            recorder.error("preflight", str(issue), hostname=issue.hostname)
        raise PipelineError(
            f"Pre-flight lint failed: {len(exc.issues)} issue(s) in the intent"
        ) from exc
    recorder.info("preflight", f"Intent is valid ({len(snapshot.intents)} device(s))")
    return snapshot


@dataclass(slots=True)
class DevicePlan:
    device: Device
    target: DeviceTarget | None = None
    expectations: HealthExpectations | None = None
    intended: str | None = None
    running: str | None = None
    diff: ConfigDiff | None = None
    error: str | None = None
    unreachable: bool = False

    @property
    def hostname(self) -> str:
        return self.device.hostname

    def change_plan(self) -> ChangePlan:
        if self.diff is None or self.intended is None:
            raise PipelineError(f"{self.hostname}: no change plan was computed")
        return ChangePlan(
            remediation=self.diff.remediation, rollback=self.diff.rollback, intended=self.intended
        )

    def fail(self, error: str, *, unreachable: bool = False) -> None:
        self.error = error
        self.unreachable = unreachable


class ChangePlanner:
    def __init__(self, toolchain: Toolchain, recorder: JobRecorder) -> None:
        self._toolchain = toolchain
        self._recorder = recorder

    # Прогресс по этапам: рендер, сбор running-config, дифф. 100% ставит завершение задачи.
    def plan(self, devices: Sequence[Device], snapshot: IntentSnapshot) -> list[DevicePlan]:
        plans = [self._render(device, snapshot) for device in devices]
        self._recorder.progress(1, _PLANNING_STAGES)
        self._collect([plan for plan in plans if plan.error is None])
        self._recorder.progress(2, _PLANNING_STAGES)
        for plan in plans:
            if plan.error is not None:
                self._recorder.error("plan", plan.error, hostname=plan.hostname)
            elif plan.running is not None and plan.intended is not None:
                self._diff(plan, plan.running, plan.intended)
        return plans

    def _render(self, device: Device, snapshot: IntentSnapshot) -> DevicePlan:
        plan = DevicePlan(device=device)
        intent = snapshot.intent_for(device.hostname)
        if intent is None:
            plan.fail(f"No intent for {device.hostname} in the repository")
            return plan
        plan.expectations = HealthExpectations.from_intent(intent)
        spec = InventoryDevice.model_validate(device)
        try:
            plan.target = self._toolchain.target_for(spec)
        except PipelineError as exc:
            plan.fail(str(exc))
            return plan
        try:
            rendered = self._toolchain.renderer.render(spec, intent)
        except Exception as exc:
            logger.debug("Rendering failed for %s", device.hostname, exc_info=True)
            plan.fail(f"Rendering failed: {exc}")
            return plan
        plan.intended = self._toolchain.normalizer.normalize(device.platform, rendered)
        self._recorder.info(
            "render",
            f"Rendered intended config ({_count_lines(plan.intended)} lines)",
            hostname=device.hostname,
        )
        return plan

    def _collect(self, plans: Sequence[DevicePlan]) -> None:
        if not plans:
            return
        self._recorder.info("collect", f"Collecting running-config from {len(plans)} device(s)")
        targets = [plan.target for plan in plans if plan.target is not None]
        try:
            results = self._toolchain.collector.fetch_running_configs(targets)
        except Exception as exc:
            logger.warning("Collector failed", exc_info=True)
            for plan in plans:
                plan.fail(f"Collection failed: {exc}", unreachable=True)
            return

        for plan in plans:
            result = results.get(plan.hostname)
            if result is None or result.config is None:
                reason = result.error if result is not None else "no result from the collector"
                plan.fail(f"Cannot collect running-config: {reason}", unreachable=True)
                continue
            plan.running = self._toolchain.normalizer.normalize(plan.device.platform, result.config)

    def _diff(self, plan: DevicePlan, running: str, intended: str) -> None:
        try:
            diff = self._toolchain.diff_engine.compare(plan.device.platform, running, intended)
        except Exception as exc:
            logger.debug("Diff failed for %s", plan.hostname, exc_info=True)
            plan.fail(f"Diff failed: {exc}")
            self._recorder.error("diff", f"Diff failed: {exc}", hostname=plan.hostname)
            return
        plan.diff = diff
        summary = (
            "Device matches the intended config"
            if diff.in_sync
            else f"{_count_lines(diff.remediation)} remediation line(s), "
            f"{len(diff.unauthorized_lines)} unauthorized, {len(diff.missing_lines)} missing"
        )
        self._recorder.info("diff", summary, hostname=plan.hostname)


def persist_plan(session: Session, job: Job, row: JobTarget, plan: DevicePlan) -> None:
    if plan.running is not None:
        row.running_snapshot = _snapshot(
            session, job, plan.device, SnapshotKind.RUNNING, plan.running
        )
    if plan.intended is not None:
        row.intended_snapshot = _snapshot(
            session, job, plan.device, SnapshotKind.INTENDED, plan.intended
        )
    if plan.diff is not None:
        row.remediation_config = plan.diff.remediation
        row.rollback_config = plan.diff.rollback
    if plan.expectations is not None:
        row.health_expectations = plan.expectations.to_json()


def _snapshot(
    session: Session, job: Job, device: Device, kind: SnapshotKind, content: str
) -> ConfigSnapshot:
    snapshot = ConfigSnapshot.capture(
        device_id=device.id, kind=kind, content=content, job_id=job.id
    )
    session.add(snapshot)
    return snapshot


def _count_lines(text: str) -> int:
    return len(text.splitlines())
