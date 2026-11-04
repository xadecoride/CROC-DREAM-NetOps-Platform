"""Emergency operations and direct out-of-band management console routes.

Provides:
1. Direct CLI show/exec command runner across any active device.
2. Emergency direct patching without pre-flight gating (in emergency_reset).
3. Two-tier recovery (Soft Reset and Hard Reset in emergency_reset).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from netops.api.deps import ContainerDep, Operator, Viewer
from netops.api.routes.emergency_reset import (
    EmergencyPatchRequest,
    EmergencyPatchResponse,
    ResetResponse,
)
from netops.api.routes.emergency_reset import (
    router as emergency_reset_router,
)
from netops.toolchain import build_toolchain

logger = logging.getLogger(__name__)

__all__ = [
    "EmergencyCommandRequest",
    "EmergencyCommandResponse",
    "EmergencyPatchRequest",
    "EmergencyPatchResponse",
    "ResetResponse",
    "record_audit",
    "router",
]

router = APIRouter(prefix="/emergency", tags=["emergency"])


def record_audit(
    user: str,
    action: str,
    device: str,
    payload: str,
    reason: str,
    success: bool,
    output: str = "",
    settings: Any = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "user": user,
        "action": action,
        "device": device,
        "payload": payload,
        "reason": reason,
        "success": success,
        "output_preview": output[:200] if output else "",
    }
    try:
        from netops.services.state import get_state_manager

        state = get_state_manager(settings)
        db_url = getattr(settings, "database_url", None) if settings is not None else None
        loaded = state.get_db_state("emergency_audit", [], database_url=db_url)
        records = list(loaded) if isinstance(loaded, list) else []
        records.append(entry)
        state.set_db_state("emergency_audit", records[-200:], database_url=db_url)
    except Exception as exc:
        logger.warning("Failed to record emergency audit log: %s", exc)
    return entry


class EmergencyCommandRequest(BaseModel):
    device: str = Field(description="Device hostname e.g. spine-1.croc.lab")
    command: str = Field(
        description="CLI command e.g. show ip bgp summary or display ip routing-table"
    )


class EmergencyCommandResponse(BaseModel):
    status: str
    device: str
    command: str
    output: str
    failed: bool


@router.get(
    "/audit",
    response_model=list[dict[str, Any]],
    summary="Get emergency operations audit log",
)
def get_emergency_audit(principal: Viewer, container: ContainerDep) -> list[dict[str, Any]]:
    from netops.api.routes.devices import device_allowed
    from netops.services.state import get_state_manager

    db_url = container.settings.database_url
    loaded = get_state_manager(container.settings).get_db_state(
        "emergency_audit", [], database_url=db_url
    )
    records = list(loaded) if isinstance(loaded, list) else []
    scope = (principal.allowed_system or "*").strip()
    if scope not in ("*", "all", ""):
        clean_scope = (
            scope.removeprefix("stand:").removeprefix("fabric:").removeprefix("site:").strip()
        )
        filtered: list[dict[str, Any]] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            if rec.get("user") == principal.username:
                filtered.append(rec)
                continue
            dev_name = str(rec.get("device") or "")
            if dev_name in ("fabric", "containerlab") and clean_scope == "croc-dc1-pod1":
                filtered.append(rec)
                continue
            dev_obj = resolve_inventory_device(container, dev_name) if dev_name else None
            if dev_obj is not None and device_allowed(dev_obj, scope):
                filtered.append(rec)
        records = filtered
    return records[-50:]


def _load_device_intent_safe(settings: Any, hostname: str) -> Any | None:
    try:
        import yaml

        from netops.intent.models import DeviceIntent

        intent_path = Path(settings.intent_repo_path) / "devices" / f"{hostname}.yaml"
        if intent_path.is_file():
            data = yaml.safe_load(intent_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return DeviceIntent.model_validate(data)
    except Exception:
        _ = None
    return None


def _emulate_offline_command(settings: Any, device_obj: Any, command: str) -> str:
    cmd = command.strip().lower()
    lab_dir = settings.offline_lab_path
    cfg_path = lab_dir / f"{device_obj.hostname}.cfg"

    if cmd in ("show running-config", "show run", "display current-configuration"):
        if cfg_path.exists():
            return cfg_path.read_text(encoding="utf-8")
        return f"! No offline running configuration found for {device_obj.hostname}\n"

    intent = _load_device_intent_safe(settings, device_obj.hostname)

    if "bgp" in cmd and ("summary" in cmd or "peer" in cmd):
        bgp = getattr(intent, "bgp", None) if intent is not None else None
        router_id = str(getattr(bgp, "router_id", None) or device_obj.management_ip)
        local_as = int(getattr(bgp, "asn", 65001) or 65001)
        neighbors = list(getattr(bgp, "neighbors", None) or [])
        lines = [
            f"BGP router identifier {router_id}, local AS number {local_as}",
            "BGP table version is 1, main routing table version 1",
            "Neighbor        V           AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd",
        ]
        if neighbors:
            for nb in neighbors:
                peer_ip = getattr(nb, "peer_ip", None) or getattr(nb, "ip", "10.0.1.1")
                rem_as = getattr(nb, "remote_asn", None) or getattr(nb, "remote_as", 65000)
                lines.append(
                    f"{peer_ip!s:<15} 4 {int(rem_as):>12}     142     143        1    0    0 01:23:45            2"
                )
        else:
            lines.extend(
                [
                    "10.0.1.1        4        65000     142     143        1    0    0 01:23:45            2",
                    "10.0.2.1        4        65000     142     143        1    0    0 01:23:45            2",
                ]
            )
        return "\n".join(lines) + "\n"

    if "interface" in cmd and "brief" in cmd:
        ifaces = list(getattr(intent, "interfaces", None) or []) if intent is not None else []
        lines = [
            "Interface              IP-Address      OK? Method Status                Protocol",
        ]
        if ifaces:
            for iface in ifaces:
                raw_ip = getattr(iface, "ipv4_address", None) or getattr(iface, "ip", None)
                ip_str = str(raw_ip).split("/")[0] if raw_ip else "unassigned"
                is_down = getattr(iface, "shutdown", False) or not getattr(iface, "enabled", True)
                st = "administratively down" if is_down else "up"
                proto = "down" if is_down else "up"
                lines.append(f"{iface.name:<22} {ip_str:<15} YES manual {st:<21} {proto}")
        else:
            lines.extend(
                [
                    f"Loopback0              {device_obj.management_ip:<15} YES manual up                    up",
                    "GigabitEthernet1       10.0.1.2        YES manual up                    up",
                    "GigabitEthernet2       10.0.2.2        YES manual up                    up",
                ]
            )
        return "\n".join(lines) + "\n"

    if cmd.startswith("ping"):
        return (
            "Sending 5, 100-byte ICMP Echos to target, timeout is 2 seconds:\n"
            "!!!!!\n"
            "Success rate is 100 percent (5/5), round-trip min/avg/max = 1/2/4 ms\n"
        )

    if "version" in cmd:
        return (
            f"CROC DREAM NetOps Virtual Network OS ({device_obj.platform.value})\n"
            "Software Version 17.06.01a (RELEASE SOFTWARE)\n"
            "Technical Support: CROC NetOps Platform Team\n"
            "Uptime is 4 days, 12 hours, 31 minutes\n"
        )

    return f"% Command executed on {device_obj.hostname} (offline simulation):\n{command}\n[OK]\n"


_ALLOWED_CLI_PREFIXES = (
    "show ",
    "show",
    "display ",
    "display",
    "ping ",
    "traceroute ",
    "trace ",
    "terminal length ",
)

_FORBIDDEN_PIPE_KEYWORDS = (
    "redirect",
    "tee",
    "append",
    "delete",
    "format",
    "erase",
)


def _is_allowed_diagnostic_cli(command: str) -> bool:
    """Strict allowlist for read-only diagnostic CLI commands (blocks abbreviations like 'rel', 'conf t', 'clear', 'debug', 'undo')."""
    if not command or any(c in command for c in ("\n", "\r", ";", ">", "<")):
        return False
    cmd_clean = command.strip().lower()
    if not any(
        cmd_clean == p if not p.endswith(" ") else cmd_clean.startswith(p)
        for p in _ALLOWED_CLI_PREFIXES
    ):
        return False
    if "|" in cmd_clean:
        pipe_rhs = cmd_clean.split("|", 1)[1]
        if any(kw in pipe_rhs for kw in _FORBIDDEN_PIPE_KEYWORDS):
            return False
    return True


def resolve_inventory_device(container: Any, hostname: str) -> Any:
    try:
        snapshot = container.intents.load()
        found = next(
            (
                d
                for d in snapshot.inventory.devices
                if d.hostname == hostname or d.hostname.split(".")[0] == hostname
            ),
            None,
        )
        if found is not None:
            return found
    except Exception:
        _ = None

    try:
        with container.session_factory() as session:
            from sqlalchemy import select

            from netops.intent.models import InventoryDevice
            from netops.models import Device as DeviceModel

            db_dev = session.scalars(
                select(DeviceModel).where(DeviceModel.hostname == hostname)
            ).first()
            if db_dev is None and "." not in hostname:
                db_dev = next(
                    (
                        d
                        for d in session.scalars(select(DeviceModel)).all()
                        if d.hostname.split(".")[0] == hostname
                    ),
                    None,
                )
            if db_dev is not None:
                return InventoryDevice(
                    hostname=db_dev.hostname,
                    site=db_dev.site,
                    fabric_id=db_dev.fabric_id,
                    management_ip=db_dev.management_ip,
                    management_port=db_dev.management_port,
                    platform=db_dev.platform,
                    role=db_dev.role,
                    auth_profile=db_dev.auth_profile,
                )
    except Exception:
        _ = None
    return None


@router.post(
    "/command",
    response_model=EmergencyCommandResponse,
    summary="Execute live CLI command on network device (Operator/Admin)",
)
def execute_emergency_command(
    req: EmergencyCommandRequest,
    container: ContainerDep,
    principal: Operator,
) -> dict[str, Any]:
    if not _is_allowed_diagnostic_cli(req.command):
        record_audit(
            user=principal.username,
            action="cli_command_blocked",
            device=req.device,
            payload=req.command,
            reason="Blocked non-diagnostic or destructive command by CLI allowlist",
            success=False,
            output="Command rejected by CLI security guardrail",
            settings=container.settings,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Only read-only diagnostic commands (show, display, ping, traceroute) are allowed "
                "in diagnostic CLI. Use the Dry-Run/Deploy pipeline or Admin Emergency Patch."
            ),
        )
    toolchain = build_toolchain(container.settings)
    device_obj = resolve_inventory_device(container, req.device)
    if not device_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Device '{req.device}' not found in active inventory",
        )
    from netops.api.routes.devices import assert_device_allowed

    assert_device_allowed(device_obj, principal.allowed_system)

    if container.settings.network_driver == "offline":
        output = _emulate_offline_command(container.settings, device_obj, req.command)
        record_audit(
            user=principal.username,
            action="cli_command",
            device=device_obj.hostname,
            payload=req.command,
            reason="Interactive CLI (Offline Simulation)",
            success=True,
            output=output,
            settings=container.settings,
        )
        return {
            "status": "ok",
            "device": device_obj.hostname,
            "command": req.command,
            "output": output,
            "failed": False,
        }

    target = toolchain.target_for(device_obj)

    try:
        from netops.network.scrapli_driver import _ParamikoConnAdapter

        with _ParamikoConnAdapter(target, timeout=30) as conn:
            res = conn.send_command(req.command)
            record_audit(
                user=principal.username,
                action="cli_command",
                device=device_obj.hostname,
                payload=req.command,
                reason="Interactive CLI",
                success=not res.failed,
                output=res.result,
                settings=container.settings,
            )
            return {
                "status": "ok",
                "device": device_obj.hostname,
                "command": req.command,
                "output": res.result,
                "failed": res.failed,
            }
    except Exception as exc:
        record_audit(
            user=principal.username,
            action="cli_command",
            device=device_obj.hostname,
            payload=req.command,
            reason="Interactive CLI",
            success=False,
            output=str(exc),
            settings=container.settings,
        )
        logger.warning("Failed to execute emergency command on %s: %s", device_obj.hostname, exc)
        return {
            "status": "error",
            "device": device_obj.hostname,
            "command": req.command,
            "output": f"[SSH ERROR] {exc}",
            "failed": True,
        }


router.include_router(emergency_reset_router)
