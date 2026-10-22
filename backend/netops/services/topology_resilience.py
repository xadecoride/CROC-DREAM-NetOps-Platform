"""Topology Resilience, SPOF Blast-Radius Analyzer, BGP Graceful Drain, and Named Checkpoints."""

from __future__ import annotations

import collections
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from netops.services.state import get_state_manager
from netops.settings import Settings, get_settings

DRAINED_NODES_KEY = "topology_drained_nodes"
CHECKPOINTS_KEY = "topology_checkpoints"
LINK_OVERRIDES_KEY = "topology_link_overrides"
CUSTOM_LINKS_KEY = "topology_custom_links"


def get_drained_nodes(settings: Settings | None = None) -> dict[str, dict[str, Any]]:
    sm = get_state_manager(settings or get_settings())
    data = sm.get_db_state(DRAINED_NODES_KEY, {})
    return data if isinstance(data, dict) else {}


def toggle_node_drain(
    device_id: int,
    drained: bool | None = None,
    reason: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    eff = settings or get_settings()
    sm = get_state_manager(eff)
    drained_map = dict(get_drained_nodes(eff))
    key = str(device_id)
    cur = drained_map.get(key) or {}
    next_drained = bool(drained) if drained is not None else not bool(cur.get("drained"))
    entry = {
        "device_id": int(device_id),
        "drained": next_drained,
        "reason": reason
        or (
            "BGP GSHUT 65535:0 (Плановое обслуживание узла)"
            if next_drained
            else "Узел возвращён в ротацию"
        ),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if next_drained:
        drained_map[key] = entry
    else:
        drained_map.pop(key, None)
    sm.set_db_state(DRAINED_NODES_KEY, drained_map, database_url=eff.database_url)
    return entry


def prune_expired_link_overrides(
    overrides: dict[str, dict[str, Any]],
    settings: Settings | None = None,
) -> dict[str, dict[str, Any]]:
    now = datetime.now(UTC)
    changed = False
    cleaned: dict[str, dict[str, Any]] = {}
    for lid, entry in overrides.items():
        if not isinstance(entry, dict):
            continue
        rev_at_str = entry.get("auto_revert_at")
        if rev_at_str and entry.get("admin_state") == "SHUTDOWN":
            with contextlib.suppress(Exception):
                rev_dt = datetime.fromisoformat(str(rev_at_str))
                if rev_dt.tzinfo is None:
                    rev_dt = rev_dt.replace(tzinfo=UTC)
                if now >= rev_dt:
                    cleaned[lid] = {
                        **entry,
                        "admin_state": "UP",
                        "reason": "Авто-возврат по истечении таймера Dead-Man",
                        "auto_revert_at": None,
                    }
                    changed = True
                    continue
        cleaned[lid] = entry
    if changed:
        eff = settings or get_settings()
        sm = get_state_manager(eff)
        sm.set_db_state(LINK_OVERRIDES_KEY, cleaned, database_url=eff.database_url)
    return cleaned


def compute_auto_revert_at(auto_revert_seconds: int | None) -> str | None:
    if not auto_revert_seconds or auto_revert_seconds <= 0:
        return None
    return (datetime.now(UTC) + timedelta(seconds=int(auto_revert_seconds))).isoformat()


def _connected_components(
    node_ids: set[int], active_links: list[dict[str, Any]]
) -> list[set[int]]:
    adj: dict[int, set[int]] = {nid: set() for nid in node_ids}
    for lnk in active_links:
        u, v = int(lnk["source_id"]), int(lnk["target_id"])
        if u in adj and v in adj:
            adj[u].add(v)
            adj[v].add(u)
    visited: set[int] = set()
    comps: list[set[int]] = []
    for nid in node_ids:
        if nid in visited:
            continue
        comp: set[int] = set()
        queue = collections.deque([nid])
        visited.add(nid)
        while queue:
            cur = queue.popleft()
            comp.add(cur)
            for nb in adj.get(cur, ()):
                if nb not in visited:
                    visited.add(nb)
                    queue.append(nb)
        comps.append(comp)
    return comps


def analyze_link_spof(graph: dict[str, Any], link_id: str) -> dict[str, Any]:
    """Detect if disabling `link_id` increases disconnected components or isolates nodes."""
    nodes = graph.get("nodes") or []
    links = graph.get("links") or []
    by_id = {int(n["id"]): str(n["hostname"]) for n in nodes if "id" in n}
    spine_ids = {int(n["id"]) for n in nodes if n.get("role") == "spine" and "id" in n}
    node_ids = set(by_id.keys())
    if not node_ids:
        return {"spof_warning": False, "isolated_nodes": []}

    active_before = [
        lnk
        for lnk in links
        if lnk.get("oper_status") not in ("SHUTDOWN", "DOWN")
        and lnk.get("admin_state") != "SHUTDOWN"
    ]
    active_after = [lnk for lnk in active_before if str(lnk.get("id")) != str(link_id)]

    comps_before = _connected_components(node_ids, active_before)
    comps_after = _connected_components(node_ids, active_after)
    if len(comps_after) <= len(comps_before):
        return {"spof_warning": False, "isolated_nodes": []}

    isolated_hosts: list[str] = []
    for before_comp in comps_before:
        sub_comps = [c for c in comps_after if c <= before_comp]
        if len(sub_comps) <= 1:
            continue
        main_sub = max(sub_comps, key=lambda c: (len(c & spine_ids), len(c)))
        for sub in sub_comps:
            if sub is main_sub:
                continue
            for nid in sorted(sub):
                isolated_hosts.append(by_id[nid])

    if not isolated_hosts:
        target_link = next((lnk for lnk in links if str(lnk.get("id")) == str(link_id)), None)
        if target_link:
            isolated_hosts.append(str(target_link.get("target_hostname") or target_link.get("target_id")))

    return {
        "spof_warning": True,
        "isolated_nodes": isolated_hosts,
    }


def sync_link_to_lab_config(
    link: dict[str, Any] | None,
    admin_state: str,
    settings: Settings | None = None,
) -> list[str]:
    import os

    if not link:
        return []
    eff = settings or get_settings()
    lab_dir = Path(getattr(eff, "offline_lab_path", "") or "")
    if not lab_dir.is_dir():
        return []
    if os.environ.get("PYTEST_CURRENT_TEST") and "tmp" not in str(lab_dir):
        return []
    synced: list[str] = []
    endpoints = [
        (str(link.get("source_hostname") or ""), str(link.get("source_port") or "")),
        (str(link.get("target_hostname") or ""), str(link.get("target_port") or "")),
    ]
    for hostname, port in endpoints:
        if not hostname or not port:
            continue
        cfg_file = lab_dir / f"{hostname}.cfg"
        if not cfg_file.is_file():
            continue
        with contextlib.suppress(Exception):
            text = cfg_file.read_text(encoding="utf-8")
            marker = f"! [TOPOLOGY-LINK-SHUTDOWN {port}]"
            if admin_state.upper() == "SHUTDOWN":
                if marker not in text:
                    cfg_file.write_text(f"{text.rstrip()}\n{marker}\n", encoding="utf-8")
                synced.append(hostname)
            else:
                if marker in text:
                    lines = [ln for ln in text.splitlines() if ln.strip() != marker]
                    cfg_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
                synced.append(hostname)
    return synced


def _format_checkpoint_summary(cp: dict[str, Any]) -> dict[str, Any]:
    lab_configs = cp.get("lab_configs")
    return {
        "id": cp.get("id"),
        "name": cp.get("name"),
        "created_at": cp.get("created_at"),
        "links_count": len(cp.get("custom_links") or []) + len(cp.get("link_overrides") or {}),
        "drained_count": len(cp.get("drained_nodes") or {}),
        "configs_count": len(lab_configs) if isinstance(lab_configs, dict) else 0,
    }


def _load_checkpoints_state(
    settings: Settings | None = None,
) -> tuple[Settings, Any, list[dict[str, Any]]]:
    eff = settings or get_settings()
    sm = get_state_manager(eff)
    raw = sm.get_db_state(CHECKPOINTS_KEY, [])
    return eff, sm, (list(raw) if isinstance(raw, list) else [])


def list_topology_checkpoints(settings: Settings | None = None) -> list[dict[str, Any]]:
    _, _, checkpoints = _load_checkpoints_state(settings)
    return [_format_checkpoint_summary(cp) for cp in checkpoints if isinstance(cp, dict)]


def create_topology_checkpoint(name: str, settings: Settings | None = None) -> dict[str, Any]:
    eff, sm, checkpoints = _load_checkpoints_state(settings)

    raw_overrides = sm.get_db_state(LINK_OVERRIDES_KEY, {})
    link_overrides = prune_expired_link_overrides(
        raw_overrides if isinstance(raw_overrides, dict) else {}, eff
    )
    custom_links = sm.get_db_state(CUSTOM_LINKS_KEY, [])
    drained_nodes = get_drained_nodes(eff)

    lab_configs: dict[str, str] = {}
    lab_dir = Path(getattr(eff, "offline_lab_path", "") or "")
    if lab_dir.is_dir():
        for cfg_path in sorted(lab_dir.glob("*.cfg")):
            with contextlib.suppress(Exception):
                lab_configs[cfg_path.name] = cfg_path.read_text(encoding="utf-8")

    cp_id = f"cp-{uuid.uuid4().hex[:8]}"
    created_at = datetime.now(UTC).isoformat()
    entry = {
        "id": cp_id,
        "name": (name or "").strip() or f"Checkpoint {created_at[:19]}",
        "created_at": created_at,
        "link_overrides": link_overrides,
        "custom_links": custom_links if isinstance(custom_links, list) else [],
        "drained_nodes": drained_nodes,
        "lab_configs": lab_configs,
    }
    checkpoints.insert(0, entry)
    checkpoints = checkpoints[:25]
    sm.set_db_state(CHECKPOINTS_KEY, checkpoints, database_url=eff.database_url)
    return _format_checkpoint_summary(entry)


def restore_topology_checkpoint(
    checkpoint_id: str, settings: Settings | None = None
) -> dict[str, Any]:
    eff, sm, checkpoints = _load_checkpoints_state(settings)
    target = next((cp for cp in checkpoints if isinstance(cp, dict) and cp.get("id") == checkpoint_id), None)
    if target is None:
        raise KeyError(f"Checkpoint '{checkpoint_id}' not found")

    restored_overrides: dict[str, dict[str, Any]] = {}
    for lid, ovr in (target.get("link_overrides") or {}).items():
        if isinstance(ovr, dict):
            restored_overrides[str(lid)] = {**ovr, "auto_revert_at": None}

    sm.set_db_state(
        LINK_OVERRIDES_KEY,
        restored_overrides,
        database_url=eff.database_url,
    )
    sm.set_db_state(
        CUSTOM_LINKS_KEY,
        target.get("custom_links") or [],
        database_url=eff.database_url,
    )
    sm.set_db_state(
        DRAINED_NODES_KEY,
        target.get("drained_nodes") or {},
        database_url=eff.database_url,
    )

    lab_configs = target.get("lab_configs") or {}
    lab_dir = Path(getattr(eff, "offline_lab_path", "") or "")
    if isinstance(lab_configs, dict) and lab_dir.is_dir():
        for fname, content in lab_configs.items():
            if "/" in fname or "\\" in fname or not str(fname).endswith(".cfg"):
                continue
            with contextlib.suppress(Exception):
                (lab_dir / fname).write_text(str(content), encoding="utf-8")

    return {
        "restored": True,
        "checkpoint": _format_checkpoint_summary(target),
    }


def delete_topology_checkpoint(checkpoint_id: str, settings: Settings | None = None) -> bool:
    eff, sm, checkpoints = _load_checkpoints_state(settings)
    filtered = [cp for cp in checkpoints if isinstance(cp, dict) and cp.get("id") != checkpoint_id]
    sm.set_db_state(CHECKPOINTS_KEY, filtered, database_url=eff.database_url)
    return len(filtered) < len(checkpoints)
