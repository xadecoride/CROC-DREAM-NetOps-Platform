"""Topology Core BFS Reachability and Operational Status Resolution."""

from __future__ import annotations

import collections
from typing import Any


def analyze_core_reachability(
    nodes_out: list[dict[str, Any]],
    links_map: dict[str, dict[str, Any]],
) -> None:
    """Analyze fabric BFS reachability from spine devices and assign oper_status."""
    node_link_counts: dict[int, list[int]] = collections.defaultdict(lambda: [0, 0])
    up_adj: dict[int, set[int]] = collections.defaultdict(set)
    for link in links_map.values():
        sa, sb = link.get("source_id"), link.get("target_id")
        is_shut = link.get("admin_state") == "SHUTDOWN" or link.get("oper_status") == "SHUTDOWN"
        for nid in (sa, sb):
            if isinstance(nid, int):
                node_link_counts[nid][0] += 1
                if is_shut:
                    node_link_counts[nid][1] += 1
        if not is_shut and isinstance(sa, int) and isinstance(sb, int):
            up_adj[sa].add(sb)
            up_adj[sb].add(sa)

    spine_ids = {
        int(n["id"]) for n in nodes_out if n.get("role") == "spine" and n.get("id") is not None
    }
    fabric_reachable: set[int] = set()
    if spine_ids:
        queue = collections.deque(s for s in spine_ids if up_adj.get(s))
        while queue:
            cur = queue.popleft()
            if cur in fabric_reachable:
                continue
            fabric_reachable.add(cur)
            for nxt in up_adj.get(cur, ()):
                if nxt not in fabric_reachable:
                    queue.append(nxt)

    for node in nodes_out:
        nid = node.get("id")
        tot, shut = node_link_counts.get(nid, [0, 0])
        node["total_links"] = tot
        node["shutdown_links"] = shut
        isolated_from_core = bool(spine_ids and shut > 0 and nid not in fabric_reachable)
        if (tot > 0 and shut == tot) or isolated_from_core:
            node["status"] = "UNREACHABLE"
            node["oper_status"] = "DOWN"
        elif shut > 0 or node.get("drained"):
            if node.get("status") != "UNREACHABLE":
                node["status"] = "DRIFT_DETECTED"
            node["oper_status"] = "DEGRADED"
