from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from sqlalchemy import String, cast, false, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from netops.enums import DeviceRole, DeviceStatus, Platform
from netops.errors import ConflictError, NotFoundError
from netops.intent.models import Inventory
from netops.models import Device
from netops.models.drift import DriftRecord
from netops.schemas.devices import DeviceCreate, DeviceUpdate, InventorySyncResult
from netops.services.telemetry import metric_series

_INVENTORY_FIELDS = (
    "management_ip",
    "management_port",
    "platform",
    "role",
    "auth_profile",
    "fabric_id",
    "site",
    "rack",
    "unit",
)


def _fabric_or_site(value: str) -> Any:
    """fabric_id==X OR site==X как UNION двух index-seek веток (ix_devices_fabric_id/site)."""
    ids = select(Device.id).where(Device.fabric_id == value).union(
        select(Device.id).where(Device.site == value)
    )
    return Device.id.in_(ids)


def _allowed_clause(tokens: Sequence[tuple[str, str]]) -> Any:
    """SQL-эквивалент api.routes.devices.device_allowed (RBAC allowed_system) для пагинации."""
    plat = cast(Device.platform, String)
    conds: list[Any] = []
    for token, target in tokens:
        names = {token, target}
        conds += [
            func.lower(Device.hostname).in_(names),
            cast(Device.id, String) == token,
            func.lower(Device.fabric_id).in_(names),
            func.lower(Device.site).in_(names),
            func.lower(plat).in_(names),
        ]
        if target in ("huawei", "arista", "frr", "server"):
            conds.append(func.lower(plat).contains(target))
    return or_(*conds) if conds else false()


class DeviceService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_devices(
        self,
        *,
        status: DeviceStatus | None = None,
        role: DeviceRole | None = None,
        platform: Platform | None = None,
        fabric_id: str | None = None,
        site: str | None = None,
        limit: int = 100,
        offset: int = 0,
        allowed_tokens: Sequence[tuple[str, str]] | None = None,
    ) -> Sequence[Device]:
        query = select(Device).order_by(Device.hostname, Device.id).limit(limit).offset(offset)
        if allowed_tokens:
            query = query.where(_allowed_clause(allowed_tokens))
        if status is not None:
            query = query.where(Device.status == status)
        if role is not None:
            query = query.where(Device.role == role)
        if platform is not None:
            query = query.where(Device.platform == platform)
        if fabric_id is not None:
            query = query.where(_fabric_or_site(fabric_id))
        if site is not None:
            query = query.where(_fabric_or_site(site))
        devices = self._session.scalars(query).all()

        device_ids = [d.id for d in devices]
        drifts: dict[int, DriftRecord] = {}
        if device_ids:
            from sqlalchemy import func

            latest_ids = (
                select(func.max(DriftRecord.id))
                .where(DriftRecord.device_id.in_(device_ids))
                .group_by(DriftRecord.device_id)
            )
            drift_query = select(DriftRecord).where(DriftRecord.id.in_(latest_ids))
            for record in self._session.scalars(drift_query):
                drifts[record.device_id] = record

        topo_state = self._get_topology_runtime_state()
        for device in devices:
            self._enrich_device(device, drifts.get(device.id), topo_state.get(device.id))
        return devices

    def get(self, device_id: int) -> Device:
        device = self._session.get(Device, device_id)
        if device is None:
            raise NotFoundError(f"Device {device_id} not found")
        latest_drift = self._session.scalars(
            select(DriftRecord)
            .where(DriftRecord.device_id == device.id)
            .order_by(DriftRecord.checked_at.desc())
            .limit(1)
        ).first()
        topo_state = self._get_topology_runtime_state()
        self._enrich_device(device, latest_drift, topo_state.get(device.id))
        return device

    def get_by_hostname(self, hostname: str) -> Device | None:
        return self._session.scalar(select(Device).where(Device.hostname == hostname))

    def _get_topology_runtime_state(self) -> dict[int, dict[str, str]]:
        from netops.services.topology_commutation import get_link_overrides
        from netops.services.topology_resilience import get_drained_nodes

        overrides = get_link_overrides()
        drained = get_drained_nodes()
        has_shut = any(v.get("admin_state") == "SHUTDOWN" for v in overrides.values() if isinstance(v, dict))
        has_drain = any(v.get("drained") for v in drained.values() if isinstance(v, dict))
        if not has_shut and not has_drain:
            return {}
        from netops.services.topology import build_topology_graph

        all_devs = list(self._session.scalars(select(Device)).all())
        graph = build_topology_graph(all_devs, self._session)
        return {
            int(n["id"]): {"status": str(n["status"]), "oper_status": str(n["oper_status"])}
            for n in graph.get("nodes", [])
            if n.get("id") is not None
        }

    def _enrich_device(
        self,
        device: Device,
        latest_drift: DriftRecord | None = None,
        topo_node: dict[str, str] | None = None,
    ) -> Device:
        from sqlalchemy.orm.attributes import set_committed_value

        from netops.services.state import get_state_manager

        role_str = str(device.role.value if hasattr(device.role, "value") else device.role)
        sm = get_state_manager()
        sparkline: list[float] = []
        for m_name in ("uplink_util_pct", "cpu_pct", "memory_pct"):
            local_pts = sm.get_telemetry_series(device.hostname, m_name, points=24)
            if local_pts and any(v > 0 for v in local_pts):
                sparkline = local_pts[-24:]
                break
        if not sparkline:
            for m_name in ("uplink_util_pct", "cpu_pct", "memory_pct"):
                series = metric_series(device.hostname, role_str, m_name, points=24)
                if series and (not sparkline or any(v > 0 for v in series)):
                    sparkline = series
                if sparkline and any(v > 0 for v in sparkline):
                    break
        if device.status == DeviceStatus.UNKNOWN and latest_drift is not None:
            dr_val = getattr(latest_drift.status, "value", str(latest_drift.status))
            if dr_val in (DeviceStatus.IN_SYNC.value, DeviceStatus.DRIFT_DETECTED.value):
                set_committed_value(device, "status", DeviceStatus(dr_val))
        oper_status = self._derive_oper_status(device, sparkline, latest_drift)
        if topo_node:
            t_status = topo_node.get("status")
            t_oper = topo_node.get("oper_status")
            if t_status == "UNREACHABLE":
                set_committed_value(device, "status", DeviceStatus.UNREACHABLE)
                oper_status = "DOWN"
            elif t_status == "DRIFT_DETECTED" and device.status == DeviceStatus.IN_SYNC:
                set_committed_value(device, "status", DeviceStatus.DRIFT_DETECTED)
                if oper_status == "UP":
                    oper_status = "DEGRADED"
            if t_oper in ("DOWN", "DEGRADED") and oper_status == "UP":
                oper_status = t_oper  # type: ignore[assignment]
        from netops.services.vitality import evaluate_device_vitality

        state_timeline = self._build_state_timeline(oper_status, sparkline)
        vitality = evaluate_device_vitality(device, oper_status, latest_drift, self._session)
        device.oper_status = oper_status
        device.vitality_state = vitality["vitality_state"]
        device.down_reason = vitality["down_reason"]
        device.ai_diagnosis = vitality["ai_diagnosis"]
        device.using_backup_fallback = vitality["using_backup_fallback"]
        device.last_backup_at = vitality["last_backup_at"]
        device.backup_available = vitality["backup_available"]
        device.sparkline = sparkline
        device.state_timeline = state_timeline
        return device

    def _derive_oper_status(
        self,
        device: Device,
        sparkline: list[float],
        latest_drift: DriftRecord | None = None,
    ) -> Literal["UP", "DOWN", "DEGRADED", "UNKNOWN"]:
        if device.status == DeviceStatus.UNREACHABLE:
            return "DOWN"
        if device.status == DeviceStatus.UNKNOWN:
            return "UP" if (sparkline and any(v > 0 for v in sparkline)) else "UNKNOWN"
        if latest_drift:
            has_shutdown = any(
                "shutdown" in line.lower() and "no shutdown" not in line.lower()
                for line in (latest_drift.unauthorized_lines or [])
            ) or any(
                "no shutdown" in line.lower()
                for line in (latest_drift.missing_lines or [])
            )
            if has_shutdown:
                return "DEGRADED"
        if sparkline and sparkline[-1] >= 85.0:
            return "DEGRADED"
        return "UP"

    def _build_state_timeline(
        self,
        oper_status: str,
        sparkline: list[float],
    ) -> list[str]:
        timeline: list[str] = []
        for i in range(24):
            val = sparkline[i] if i < len(sparkline) else None
            if oper_status == "DOWN":
                timeline.append("DOWN")
            elif oper_status == "UNKNOWN":
                timeline.append("UNKNOWN")
            elif oper_status == "DEGRADED" or (val is not None and val >= 85.0):
                timeline.append("DEGRADED")
            elif val is not None:
                timeline.append("UP")
            else:
                timeline.append("UNKNOWN")
        return timeline

    def create(self, data: DeviceCreate) -> Device:
        device = Device(**_column_values(data.model_dump()))
        self._session.add(device)
        self._commit(f"Device {device.hostname}")
        return device

    def update(self, device_id: int, data: DeviceUpdate) -> Device:
        device = self.get(device_id)
        if device.status is DeviceStatus.IN_PROGRESS:
            raise ConflictError(f"Device {device.hostname} is being deployed; try again later")
        for field, value in _column_values(data.model_dump(exclude_unset=True)).items():
            setattr(device, field, value)
        self._commit(f"Device {device.hostname}")
        return device

    def delete(self, device_id: int) -> None:
        from sqlalchemy import delete

        from netops.models import Job, JobTarget

        device = self.get(device_id)
        if device.status is DeviceStatus.IN_PROGRESS:
            raise ConflictError(f"Device {device.hostname} is being deployed; try again later")
        hostname = device.hostname
        self._session.execute(
            delete(JobTarget).where(
                (JobTarget.device_id == device_id) | (JobTarget.hostname == hostname)
            )
        )
        self._session.delete(device)
        self._session.flush()
        self._session.execute(delete(Job).where(~Job.targets.any()))
        self._session.commit()

    def sync_inventory(self, inventory: Inventory) -> InventorySyncResult:
        existing = {device.hostname: device for device in self._session.scalars(select(Device))}
        result = InventorySyncResult()
        for spec in inventory.devices:
            values = _column_values(spec.model_dump(include={"hostname", *_INVENTORY_FIELDS}))
            values.setdefault("management_mode", "MANAGED")
            device = existing.get(spec.hostname)
            if device is None:
                self._session.add(Device(**values))
                result.created.append(spec.hostname)
                continue
            changed = False
            for field in _INVENTORY_FIELDS:
                if getattr(device, field) != values[field]:
                    setattr(device, field, values[field])
                    changed = True
            if getattr(device, "management_mode", None) != "MANAGED":
                device.management_mode = "MANAGED"
                changed = True
            (result.updated if changed else result.unchanged).append(spec.hostname)
        self._commit("Inventory")
        return result

    def _commit(self, subject: str) -> None:
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise ConflictError(
                f"{subject} conflicts with an existing device (hostname or management endpoint)"
            ) from exc


def _column_values(values: dict[str, Any]) -> dict[str, Any]:
    if "management_ip" in values:
        values["management_ip"] = str(values["management_ip"])
    return values
