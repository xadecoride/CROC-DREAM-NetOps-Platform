"""Commutation state management (link overrides, custom links) and hop-by-hop ECMP path tracing."""

from __future__ import annotations

import collections
from typing import Any

from sqlalchemy.orm import Session

from netops.models import Device
from netops.services.state import get_state_manager
from netops.services.topology_resilience import (
    CUSTOM_LINKS_KEY,
    LINK_OVERRIDES_KEY,
    compute_auto_revert_at,
    prune_expired_link_overrides,
)
from netops.settings import Settings, get_settings


def get_link_overrides(settings: Settings | None = None) -> dict[str, dict[str, Any]]:
    eff = settings or get_settings()
    sm = get_state_manager(eff)
    data = sm.get_db_state(LINK_OVERRIDES_KEY, {})
    raw = data if isinstance(data, dict) else {}
    return prune_expired_link_overrides(raw, eff)


def get_custom_links(settings: Settings | None = None) -> list[dict[str, Any]]:
    sm = get_state_manager(settings or get_settings())
    data = sm.get_db_state(CUSTOM_LINKS_KEY, [])
    return data if isinstance(data, list) else []


def toggle_topology_link(
    link_id: str,
    admin_state: str | None = None,
    reason: str | None = None,
    settings: Settings | None = None,
    *,
    auto_revert_seconds: int | None = None,
) -> dict[str, Any]:
    eff = settings or get_settings()
    sm = get_state_manager(eff)
    overrides = dict(get_link_overrides(eff))
    custom = list(get_custom_links(eff))
    cur = overrides.get(link_id) or next((c for c in custom if c.get("id") == link_id), None) or {}
    next_state = (
        admin_state.upper()
        if admin_state in ("UP", "SHUTDOWN", "up", "shutdown")
        else ("UP" if cur.get("admin_state") == "SHUTDOWN" else "SHUTDOWN")
    )
    auto_revert_at = compute_auto_revert_at(auto_revert_seconds) if next_state == "SHUTDOWN" else None
    entry = {
        "link_id": link_id,
        "admin_state": next_state,
        "reason": reason or ("Отключено оператором" if next_state == "SHUTDOWN" else "Связь активна"),
        "auto_revert_at": auto_revert_at,
    }
    overrides[link_id] = entry
    sm.set_db_state(LINK_OVERRIDES_KEY, overrides, database_url=eff.database_url)
    if any(c.get("id") == link_id for c in custom):
        for c in custom:
            if c.get("id") == link_id:
                c["admin_state"] = next_state
                c["oper_status"] = "SHUTDOWN" if next_state == "SHUTDOWN" else "UP"
        sm.set_db_state(CUSTOM_LINKS_KEY, custom, database_url=eff.database_url)
    return entry


def upsert_custom_link(
    payload: dict[str, Any], settings: Settings | None = None
) -> dict[str, Any]:
    eff = settings or get_settings()
    sm = get_state_manager(eff)
    custom = list(get_custom_links(eff))
    default_stand = getattr(eff, "default_fabric_id", "croc-dc1-pod1") or "croc-dc1-pod1"
    src_id = int(payload["source_id"])
    dst_id = int(payload["target_id"])
    src_port = str(payload.get("source_port") or "eth1").strip()
    dst_port = str(payload.get("target_port") or "eth1").strip()
    lo, hi = (src_id, dst_id) if src_id <= dst_id else (dst_id, src_id)
    link_id = str(payload.get("id") or f"link-{lo}-{hi}")
    st1 = str(payload.get("source_stand") or payload.get("fabric_id") or default_stand)
    st2 = str(payload.get("target_stand") or payload.get("fabric_id") or st1)
    layer = str(payload.get("layer") or ("INTER_STAND" if st1 != st2 else "L3_SUBNET"))
    is_inter = st1 != st2 or layer == "INTER_STAND"
    admin_state = str(payload.get("admin_state") or "UP").upper()
    entry = {
        "id": link_id,
        "source_id": src_id,
        "target_id": dst_id,
        "source_hostname": str(payload.get("source_hostname") or f"node-{src_id}"),
        "target_hostname": str(payload.get("target_hostname") or f"node-{dst_id}"),
        "source_port": src_port,
        "target_port": dst_port,
        "layer": layer,
        "layers": list(dict.fromkeys([layer, "L2_LLDP", *(["INTER_STAND"] if is_inter else [])])),
        "subnet": payload.get("subnet"),
        "protocol": str(payload.get("protocol") or "LLDP + eBGP"),
        "bgp_asn": str(payload.get("bgp_asn") or ""),
        "vxlan_vni": payload.get("vxlan_vni"),
        "fabric_id": st1,
        "source_stand": st1,
        "target_stand": st2,
        "is_inter_stand": is_inter,
        "load_pct": float(payload.get("load_pct") or 0.0),
        "admin_state": admin_state,
        "oper_status": "SHUTDOWN" if admin_state == "SHUTDOWN" else "UP",
        "is_custom": True,
        "description": str(payload.get("description") or "Пользовательская коммутация"),
    }
    custom = [c for c in custom if c.get("id") != link_id]
    custom.append(entry)
    sm.set_db_state(CUSTOM_LINKS_KEY, custom, database_url=eff.database_url)
    overrides = dict(get_link_overrides(eff))
    if link_id in overrides:
        overrides.pop(link_id, None)
        sm.set_db_state(LINK_OVERRIDES_KEY, overrides, database_url=eff.database_url)
    return entry


def delete_custom_link(link_id: str, settings: Settings | None = None) -> bool:
    eff = settings or get_settings()
    sm = get_state_manager(eff)
    custom = list(get_custom_links(eff))
    filtered = [c for c in custom if c.get("id") != link_id]
    sm.set_db_state(CUSTOM_LINKS_KEY, filtered, database_url=eff.database_url)
    overrides = dict(get_link_overrides(eff))
    had_override = link_id in overrides
    if had_override:
        overrides.pop(link_id, None)
        sm.set_db_state(LINK_OVERRIDES_KEY, overrides, database_url=eff.database_url)
    return len(filtered) < len(custom) or had_override


def _bfs_shortest_paths(
    adj: dict[int, list[tuple[int, dict[str, Any]]]],
    source_id: int,
    target_id: int,
    nodes_by_id: dict[int, dict[str, Any]],
    *,
    avoid_drained_transit: bool,
) -> list[tuple[list[int], list[dict[str, Any]]]]:
    queue: collections.deque[tuple[int, list[int], list[dict[str, Any]]]] = collections.deque(
        [(source_id, [source_id], [])]
    )
    shortest_paths: list[tuple[list[int], list[dict[str, Any]]]] = []
    min_len: int | None = None

    while queue:
        curr, path_nodes, path_links = queue.popleft()
        if min_len is not None and len(path_nodes) > min_len:
            continue
        if curr == target_id:
            min_len = len(path_nodes)
            shortest_paths.append((path_nodes, path_links))
            continue
        for nxt, edge in adj.get(curr, []):
            if nxt in path_nodes:
                continue
            nxt_node = nodes_by_id.get(nxt, {})
            if nxt != target_id and (
                nxt_node.get("role") == "server" or nxt_node.get("platform") == "linux_server"
            ):
                continue
            if avoid_drained_transit and nxt != target_id and nxt_node.get("drained"):
                continue
            queue.append((nxt, [*path_nodes, nxt], [*path_links, edge]))
    return shortest_paths


def trace_commutation_path(
    devices: list[Device],
    session: Session,
    source_id: int,
    target_id: int,
    settings: Settings | None = None,
    *,
    fabric_id: str | None = None,
) -> dict[str, Any]:
    """Trace active L2/L3/BGP commutation path between two devices and compute ECMP alternatives."""
    from netops.services.topology import build_topology_graph

    graph = build_topology_graph(devices, session, settings, fabric_id=fabric_id)
    nodes_by_id = {int(n["id"]): n for n in graph["nodes"]}
    src = nodes_by_id.get(source_id)
    dst = nodes_by_id.get(target_id)
    if not src or not dst:
        return {
            "reachable": False,
            "source": src["hostname"] if src else str(source_id),
            "target": dst["hostname"] if dst else str(target_id),
            "ecmp_paths_count": 0,
            "ecmp_paths": [],
            "disabled_links": [],
            "hops": [],
            "links_used": [],
            "message": "Узел не найден в топологии",
        }

    adj: dict[int, list[tuple[int, dict[str, Any]]]] = collections.defaultdict(list)
    disabled_links: list[str] = []
    for link in graph["links"]:
        if link["oper_status"] in ("SHUTDOWN", "DOWN"):
            disabled_links.append(
                f"{link['source_hostname']}:{link['source_port']} ↔ {link['target_hostname']}:{link['target_port']} ({link['oper_status']})"
            )
            continue
        u, v = int(link["source_id"]), int(link["target_id"])
        adj[u].append((v, link))
        adj[v].append((u, link))

    shortest_paths = _bfs_shortest_paths(
        adj, source_id, target_id, nodes_by_id, avoid_drained_transit=True
    )
    if not shortest_paths:
        shortest_paths = _bfs_shortest_paths(
            adj, source_id, target_id, nodes_by_id, avoid_drained_transit=False
        )

    if not shortest_paths:
        return {
            "reachable": False,
            "source": src["hostname"],
            "target": dst["hostname"],
            "ecmp_paths_count": 0,
            "ecmp_paths": [],
            "disabled_links": disabled_links,
            "hops": [],
            "links_used": [],
            "message": f"Связь {src['hostname']} → {dst['hostname']} прервана (нет активного маршрута L2/L3/BGP)",
        }

    primary_nodes, primary_links = shortest_paths[0]
    hops: list[dict[str, Any]] = []
    for idx, nid in enumerate(primary_nodes):
        node = nodes_by_id[nid]
        in_link = primary_links[idx - 1] if idx > 0 else None
        out_link = primary_links[idx] if idx < len(primary_links) else None
        in_port = (
            (in_link["target_port"] if in_link["target_id"] == nid else in_link["source_port"])
            if in_link
            else "Local Ingress"
        )
        out_port = (
            (out_link["source_port"] if out_link["source_id"] == nid else out_link["target_port"])
            if out_link
            else "Local Delivery (RIB/Host)"
        )
        bgp = node.get("bgp") or {}
        stage_desc = []
        if idx == 0 and bgp.get("vxlan_vni"):
            stage_desc.append(f"VXLAN Encap VNI {bgp['vxlan_vni']} (VTEP {bgp.get('router_id')})")
        if out_link and out_link.get("acl_in"):
            stage_desc.append(f"ACL {out_link['acl_in']}")
        if bgp.get("asn"):
            stage_desc.append(f"BGP AS{bgp['asn']} (Router-ID {bgp.get('router_id')})")
        if node.get("drained"):
            stage_desc.append("BGP GSHUT 65535:0 (Drained / Maintenance)")
        if idx == len(primary_nodes) - 1 and bgp.get("vxlan_vni"):
            stage_desc.append(f"VXLAN Decap VNI {bgp['vxlan_vni']}")
        hops.append(
            {
                "step": idx + 1,
                "device_id": nid,
                "hostname": node["hostname"],
                "role": node["role"],
                "platform": node["platform"],
                "fabric_id": node["fabric_id"],
                "drained": bool(node.get("drained")),
                "ingress_port": in_port,
                "egress_port": out_port,
                "subnet": out_link.get("subnet") if out_link else (in_link.get("subnet") if in_link else None),
                "pipeline_stages": stage_desc or ["L2/L3 Fast-Forwarding"],
            }
        )

    return {
        "reachable": True,
        "source": src["hostname"],
        "target": dst["hostname"],
        "ecmp_paths_count": len(shortest_paths),
        "ecmp_paths": [
            " → ".join(nodes_by_id[n]["hostname"].split(".")[0] for n in p_nodes)
            for p_nodes, _ in shortest_paths
        ],
        "disabled_links": disabled_links,
        "hops": hops,
        "links_used": list(
            dict.fromkeys(lnk["id"] for _, p_links in shortest_paths for lnk in p_links)
        ),
        "message": (
            f"Маршрут активен ({len(primary_links)} хопа, доступно ECMP-путей: {len(shortest_paths)})"
        ),
    }
