"""Emergency Factory Freeze (Kill Switch) state management."""

from __future__ import annotations

from typing import Any

from netops.constants import const
from netops.services.state import get_state_manager


def _norm_fabric_id(fabric_id: str | None) -> str | None:
    if not fabric_id:
        return None
    cleaned = (
        fabric_id.strip()
        .removeprefix("stand:")
        .removeprefix("fabric:")
        .removeprefix("site:")
        .strip()
    )
    return cleaned or None


def _enrich_freeze_state(state: dict[str, Any]) -> dict[str, Any]:
    out = dict(state)
    out.setdefault("frozen_by", out.get("user") or out.get("updated_by"))
    out.setdefault("frozen_at", out.get("timestamp"))
    return out


def _reset_local_freeze() -> None:
    get_state_manager().reset_factory_freeze()


def is_factory_frozen(fabric_id: str | None = None) -> bool:
    return get_state_manager().is_factory_frozen(fabric_id=_norm_fabric_id(fabric_id))


def get_freeze_state(fabric_id: str | None = None) -> dict[str, Any]:
    state = get_state_manager().get_factory_freeze(fabric_id=_norm_fabric_id(fabric_id))
    return _enrich_freeze_state(state)


def set_factory_freeze(
    frozen: bool,
    reason: str | None = None,
    user: str | None = None,
    fabric_id: str | None = None,
) -> dict[str, Any]:
    mgr = get_state_manager()
    clean_fab = _norm_fabric_id(fabric_id)
    if not frozen:
        if clean_fab:
            mgr.release_sync_lock(f"fabric:{clean_fab}")
        else:
            mgr.clear_sync_locks()
    state = mgr.set_factory_freeze(
        frozen=frozen,
        reason=reason or "",
        user=user or str(const("audit.default_freeze_user")),
        fabric_id=clean_fab,
    )
    return _enrich_freeze_state(state)
