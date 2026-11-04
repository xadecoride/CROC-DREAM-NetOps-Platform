"""Emergency direct patching and two-tier fabric reset routes (Admin only)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import paramiko
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from netops.api.deps import Admin, ContainerDep
from netops.network.base import ChangePlan
from netops.toolchain import build_toolchain

logger = logging.getLogger(__name__)

router = APIRouter(tags=["emergency"])


class EmergencyPatchRequest(BaseModel):
    device: str = Field(description="Device hostname")
    patch: str = Field(description="Raw configuration lines to apply immediately")
    reason: str = Field(default="Emergency intervention", description="Audit reason for manual patch")


class EmergencyPatchResponse(BaseModel):
    status: str
    device: str
    applied_lines: list[str]
    output: str
    reason: str


class ResetResponse(BaseModel):
    status: str
    message: str
    details: str = ""


@router.post(
    "/patch",
    response_model=EmergencyPatchResponse,
    summary="Apply emergency configuration patch directly (Admin only)",
)
def apply_emergency_patch(
    req: EmergencyPatchRequest,
    container: ContainerDep,
    admin: Admin,
) -> dict[str, Any]:
    from netops.api.routes.devices import assert_device_allowed
    from netops.api.routes.emergency import record_audit, resolve_inventory_device

    toolchain = build_toolchain(container.settings)
    device_obj = resolve_inventory_device(container, req.device)
    if not device_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Device '{req.device}' not found in active inventory",
        )

    assert_device_allowed(device_obj, admin.allowed_system)

    target = toolchain.target_for(device_obj)
    lines = [
        line.strip()
        for line in req.patch.splitlines()
        if line.strip() and not line.strip().startswith("!")
    ]

    if not lines:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Patch does not contain any valid configuration statements",
        )

    with container.session_factory() as session:
        from sqlalchemy import select

        from netops.enums import JobStatus, JobType
        from netops.models import Device, Job, JobTarget
        from netops.services.state import get_state_manager

        active_job = session.scalar(
            select(Job.id)
            .join(JobTarget)
            .where(
                JobTarget.hostname == device_obj.hostname,
                Job.type.in_((JobType.DEPLOY, JobType.DRIFT_REMEDIATE)),
                Job.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
            )
        )
        if active_job is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Device '{device_obj.hostname}' is locked by active pipeline job {active_job}",
            )

        state_mgr = get_state_manager()
        lock_holder = f"emergency_patch:{admin.username}"
        locked_keys: list[str] = []
        host_lock_key = f"device:{device_obj.hostname}"
        state_mgr.release_sync_lock(host_lock_key, holder=lock_holder)
        if not state_mgr.acquire_sync_lock(
            host_lock_key, holder=lock_holder, ttl=600, reason=req.reason
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Device '{device_obj.hostname}' is currently locked by another operation",
            )
        locked_keys.append(host_lock_key)
        db_device = session.scalar(select(Device).where(Device.hostname == device_obj.hostname))
        if db_device is not None:
            id_lock_key = f"device:{db_device.id}"
            state_mgr.release_sync_lock(id_lock_key, holder=lock_holder)
            if not state_mgr.acquire_sync_lock(
                id_lock_key, holder=lock_holder, ttl=600, reason=req.reason
            ):
                state_mgr.release_sync_lock(host_lock_key)
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Device '{device_obj.hostname}' is currently locked by another operation",
                )
            locked_keys.append(id_lock_key)

    if container.settings.network_driver == "offline":
        cfg_path = container.settings.offline_lab_path / f"{device_obj.hostname}.cfg"
        existing = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
        patch_text = "\n".join(lines)
        cfg_path.write_text(f"{existing.rstrip()}\n{patch_text}\n", encoding="utf-8")
        record_audit(
            user=admin.username,
            action="emergency_patch",
            device=device_obj.hostname,
            payload=patch_text,
            reason=req.reason,
            success=True,
            output="Applied to offline configuration store",
            settings=container.settings,
        )
        return {
            "status": "ok",
            "device": device_obj.hostname,
            "applied_lines": lines,
            "output": "Configuration patch successfully applied to offline configuration.",
            "reason": req.reason,
        }

    try:
        from netops.network.scrapli_driver import _ParamikoConnAdapter

        with _ParamikoConnAdapter(target, timeout=35) as conn:
            res = conn.send_configs(lines)
            if getattr(res, "failed", False):
                raise RuntimeError(res.result or "Device rejected configuration commands")
            record_audit(
                user=admin.username,
                action="emergency_patch",
                device=device_obj.hostname,
                payload="\n".join(lines),
                reason=req.reason,
                success=True,
                output=res.result,
                settings=container.settings,
            )
            return {
                "status": "ok",
                "device": device_obj.hostname,
                "applied_lines": lines,
                "output": res.result,
                "reason": req.reason,
            }
    except Exception as exc:
        for k in locked_keys:
            state_mgr.release_sync_lock(k)
        record_audit(
            user=admin.username,
            action="emergency_patch",
            device=device_obj.hostname,
            payload="\n".join(lines),
            reason=req.reason,
            success=False,
            output=str(exc),
            settings=container.settings,
        )
        logger.exception("Failed to apply emergency patch on %s", device_obj.hostname)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Patch application failed on {device_obj.hostname}: {exc}",
        ) from exc


@router.post(
    "/soft-reset",
    response_model=ResetResponse,
    summary="Soft Reset: Re-converge fabric to Golden Baseline Intent (Admin only)",
)
def soft_reset_fabric(
    container: ContainerDep,
    admin: Admin,
) -> ResetResponse:
    from datetime import UTC, datetime

    from sqlalchemy import select

    from netops.api.routes.devices import device_allowed
    from netops.api.routes.emergency import record_audit
    from netops.enums import DeviceStatus, DriftStatus
    from netops.models import Device as DeviceModel
    from netops.models import DriftRecord
    from netops.services.ai_guard import AiTelemetryGuard

    toolchain = build_toolchain(container.settings)
    snapshot = container.intents.load()

    deployed: list[str] = []
    aligned_hosts: list[str] = []
    errors: list[str] = []
    is_offline = container.settings.network_driver == "offline"

    for dev in snapshot.inventory.devices:
        if not device_allowed(dev, admin.allowed_system):
            continue
        try:
            AiTelemetryGuard.inject_anomaly(dev.hostname, "clear")
            intent = snapshot.intent_for(dev.hostname)
            if intent is None:
                errors.append(f"{dev.hostname}: missing device intent YAML")
                continue
            target = toolchain.target_for(dev)
            rendered = toolchain.renderer.render(dev, intent)
            intended = toolchain.normalizer.normalize(dev.platform, rendered)
            intended_for_plan = rendered if is_offline else intended

            fetch_res = toolchain.collector.fetch_running_configs([target]).get(dev.hostname)
            if not fetch_res or not fetch_res.config:
                if is_offline:
                    cfg_path = container.settings.offline_lab_path / f"{dev.hostname}.cfg"
                    cfg_path.parent.mkdir(parents=True, exist_ok=True)
                    cfg_path.write_text(
                        rendered if rendered.endswith("\n") else rendered + "\n",
                        encoding="utf-8",
                    )
                    deployed.append(f"{dev.hostname} (remediated)")
                    aligned_hosts.append(dev.hostname)
                else:
                    errors.append(f"{dev.hostname}: failed to fetch running-config")
                continue

            running = toolchain.normalizer.normalize(dev.platform, fetch_res.config)
            diff = toolchain.diff_engine.compare(dev.platform, running, intended)

            if diff.has_changes:
                plan = ChangePlan(
                    remediation=diff.remediation,
                    rollback=diff.rollback,
                    intended=intended_for_plan,
                )
                toolchain.deployer.apply(
                    target, plan, confirm_timeout=container.settings.commit_confirm_timeout_seconds
                )
                toolchain.deployer.confirm(target)
                deployed.append(f"{dev.hostname} (remediated)")
            elif is_offline and fetch_res.config.strip() != rendered.strip():
                cfg_path = container.settings.offline_lab_path / f"{dev.hostname}.cfg"
                cfg_path.write_text(
                    rendered if rendered.endswith("\n") else rendered + "\n",
                    encoding="utf-8",
                )
                deployed.append(f"{dev.hostname} (remediated)")
            else:
                deployed.append(f"{dev.hostname} (already aligned)")
            aligned_hosts.append(dev.hostname)
        except Exception as exc:
            logger.exception("Soft reset error on %s", dev.hostname)
            errors.append(f"{dev.hostname}: {exc}")

    if aligned_hosts:
        from netops.services.state import get_state_manager
        from netops.services.topology_resilience import DRAINED_NODES_KEY, LINK_OVERRIDES_KEY

        state_mgr = get_state_manager()
        state_mgr.set_db_state(LINK_OVERRIDES_KEY, {}, database_url=container.settings.database_url)
        state_mgr.set_db_state(DRAINED_NODES_KEY, {}, database_url=container.settings.database_url)
        for host in aligned_hosts:
            state_mgr.release_sync_lock(f"device:{host}")
        now = datetime.now(UTC)
        with container.session_factory() as session:
            db_devs = session.scalars(
                select(DeviceModel).where(DeviceModel.hostname.in_(aligned_hosts))
            ).all()
            for db_dev in db_devs:
                db_dev.status = DeviceStatus.IN_SYNC
                db_dev.last_checked_at = now
                if db_dev.id is not None:
                    state_mgr.release_sync_lock(f"device:{db_dev.id}")
                    session.add(
                        DriftRecord(
                            device_id=db_dev.id,
                            status=DriftStatus.IN_SYNC,
                            checked_at=now,
                        )
                    )
                if getattr(db_dev, "fabric_id", None):
                    state_mgr.release_sync_lock(f"fabric:{db_dev.fabric_id}")
            session.commit()

    msg = f"Soft reset completed on {len(deployed)} nodes."
    if errors:
        msg += f" Encountered {len(errors)} issues."

    details_str = "; ".join(deployed + [f"ERR: {e}" for e in errors])
    record_audit(
        user=admin.username,
        action="soft_reset",
        device="fabric",
        payload=f"Aligned {len(aligned_hosts)} devices",
        reason="Soft Reset to Golden Baseline",
        success=not errors,
        output=details_str or msg,
        settings=container.settings,
    )

    return ResetResponse(
        status="ok" if not errors else "partial",
        message=msg,
        details=details_str,
    )


@router.post(
    "/hard-reset",
    response_model=ResetResponse,
    summary="Hard Reset: Redeploy Containerlab topology on server (Admin only)",
)
def hard_reset_containerlab(
    container: ContainerDep,
    admin: Admin,
) -> ResetResponse:
    from netops.api.routes.emergency import record_audit

    scope = (admin.allowed_system or "*").strip()
    clean_scope = (
        scope.removeprefix("stand:").removeprefix("fabric:").removeprefix("site:").strip()
    )
    if clean_scope not in ("*", "all", "", "croc-dc1-pod1"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Hard Reset of main Containerlab topology is outside your stand scope ('{scope}').",
        )

    cfg = container.settings
    if not cfg.clab_host or not cfg.clab_user or (not cfg.clab_password and not cfg.clab_key_path):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Containerlab remote host/credentials not configured in NETOPS_CLAB_*",
        )

    host = cfg.clab_host
    port = cfg.clab_port
    user = cfg.clab_user
    pwd = cfg.clab_password.get_secret_value() if cfg.clab_password else None

    client = paramiko.SSHClient()
    if os.getenv("NETOPS_SSH_STRICT_HOST_KEY", "").lower() in ("1", "true", "yes"):
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.WarningPolicy())
    try:
        connect_kwargs: dict[str, Any] = {
            "hostname": host,
            "port": port,
            "username": user,
            "timeout": 15,
        }
        if pwd:
            connect_kwargs["password"] = pwd
        if cfg.clab_key_path and os.path.exists(cfg.clab_key_path):
            connect_kwargs["key_filename"] = cfg.clab_key_path

        import shlex

        client.connect(**connect_kwargs)
        q_user = shlex.quote(str(user))
        q_topo = shlex.quote(str(cfg.clab_topology_path))
        topo_dir = shlex.quote(str(Path(cfg.clab_topology_path).parent))
        cmd = (
            f"if [ -f {topo_dir}/reset_lab.sh ]; then bash {topo_dir}/reset_lab.sh {q_topo}; "
            f"elif [ -f /home/{q_user}/clab-quickstart/reset_lab.sh ]; then bash /home/{q_user}/clab-quickstart/reset_lab.sh {q_topo}; "
            f"else containerlab deploy -t {q_topo} --reconfigure; fi"
        )
        _stdin, stdout, _stderr = client.exec_command(cmd, timeout=120)
        out = stdout.read().decode("utf-8", errors="replace")
        err = _stderr.read().decode("utf-8", errors="replace")
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            raise RuntimeError(
                f"Remote command exited with status {exit_status}: {(err or out).strip()}"
            )

        details_out = out[-1000:] if len(out) > 1000 else out
        record_audit(
            user=admin.username,
            action="hard_reset",
            device="containerlab",
            payload=str(cfg.clab_topology_path),
            reason="Hard Reset Containerlab Topology",
            success=True,
            output=details_out,
            settings=cfg,
        )
        return ResetResponse(
            status="ok",
            message="Hard Reset: Containerlab topology successfully restored.",
            details=details_out,
        )
    except Exception as exc:
        record_audit(
            user=admin.username,
            action="hard_reset",
            device="containerlab",
            payload=str(cfg.clab_topology_path),
            reason="Hard Reset Containerlab Topology",
            success=False,
            output=str(exc),
            settings=cfg,
        )
        logger.exception("Hard reset failed via SSH")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Hard reset failed: {exc}",
        ) from exc

    finally:
        client.close()
