"""Unified Multi-Layer Network Topology & Commutation Graph Engine.

Builds a real L1/L2 (Containerlab + LLDP), L3 (IPv4 point-to-point subnets + OSPF),
Control-Plane (BGP/EVPN/VXLAN), and Inter-Stand (DCI) topology graph with live link
toggling, custom link creation, and hop-by-hop packet commutation path tracing.
"""

from __future__ import annotations

import collections
import contextlib
import ipaddress
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from netops.intent.repository import IntentRepository
from netops.models import Device, Stand
from netops.services.topology_commutation import (
    CUSTOM_LINKS_KEY,
    LINK_OVERRIDES_KEY,
    delete_custom_link,
    get_custom_links,
    get_link_overrides,
    toggle_topology_link,
    trace_commutation_path,
    upsert_custom_link,
)
from netops.services.topology_core import analyze_core_reachability
from netops.services.topology_links import (
    canon_host as _canon_host,
)
from netops.services.topology_links import (
    device_load as _device_load,
)
from netops.services.topology_links import (
    extract_hw_specs as _extract_hw_specs,
)
from netops.services.topology_links import (
    load_clab_endpoints as _load_clab_endpoints,
)
from netops.services.topology_links import (
    make_base_link as _make_base_link,
)
from netops.services.topology_links import (
    merge_custom_links_and_overrides,
    synthesize_bgp_evpn_links,
    synthesize_clos_and_dci_links,
    synthesize_l2_lldp_links,
    synthesize_l3_subnet_links,
)
from netops.services.topology_links import (
    pair_key as _pair_key,
)
from netops.services.topology_resilience import get_drained_nodes
from netops.settings import Settings, get_settings

__all__ = [
    "CUSTOM_LINKS_KEY",
    "LINK_OVERRIDES_KEY",
    "_canon_host",
    "_device_load",
    "_extract_hw_specs",
    "_load_clab_endpoints",
    "_make_base_link",
    "_pair_key",
    "build_topology_graph",
    "delete_custom_link",
    "get_custom_links",
    "get_link_overrides",
    "toggle_topology_link",
    "trace_commutation_path",
    "upsert_custom_link",
]


def build_topology_graph(
    devices: list[Device],
    session: Session,
    settings: Settings | None = None,
    *,
    fabric_id: str | None = None,
) -> dict[str, Any]:
    eff = settings or get_settings()
    default_stand = getattr(eff, "default_fabric_id", "croc-dc1-pod1") or "croc-dc1-pod1"
    if fabric_id and fabric_id != "all":
        devices = [
            d
            for d in devices
            if fabric_id
            in (
                getattr(d, "fabric_id", None) or default_stand,
                getattr(d, "site", None),
            )
        ]

    intents_map: dict[str, Any] = {}
    intent_root = Path(eff.intent_repo_path)
    with contextlib.suppress(Exception):
        snap = IntentRepository(intent_root).load()
        intents_map = dict(snap.intents)
    for d in devices:
        if d.hostname in intents_map:
            continue
        st_slug = getattr(d, "fabric_id", None) or getattr(d, "site", None) or default_stand
        for cand in (
            intent_root / "stands" / st_slug / "devices" / f"{d.hostname}.yaml",
            intent_root / "devices" / f"{d.hostname}.yaml",
        ):
            if cand.is_file():
                with contextlib.suppress(Exception):
                    from netops.intent.models import DeviceIntent

                    intents_map[d.hostname] = DeviceIntent.model_validate(
                        yaml.safe_load(cand.read_text(encoding="utf-8")) or {}
                    )
                    break

    by_id: dict[int, Device] = {d.id: d for d in devices if d.id is not None}
    by_canon: dict[str, Device] = {_canon_host(d.hostname): d for d in devices}
    db_status_map: dict[int, str] = {}
    if session is not None and by_id:
        with contextlib.suppress(Exception):
            for did, st_val in session.execute(
                select(Device.id, Device.status).where(Device.id.in_(by_id.keys()))
            ):
                db_status_map[int(did)] = getattr(st_val, "value", str(st_val))

    ip_to_dev_iface: dict[str, tuple[Device, str, str]] = {}
    subnet_buckets: dict[str, list[tuple[Device, str, str, str | None, str | None, int]]] = (
        collections.defaultdict(list)
    )

    from netops.services.telemetry import resolve_device_ports
    from netops.telemetry import targets as telemetry_targets

    collector_plan = (telemetry_targets.last_collector_plan() or {}).get("devices") or {}
    proto_overrides = telemetry_targets.get_protocol_overrides()
    drained_map = get_drained_nodes(eff)
    nodes_out: list[dict[str, Any]] = []
    for dev in devices:
        stand_slug = (
            getattr(dev, "fabric_id", None) or getattr(dev, "site", None) or default_stand
        )
        intent = intents_map.get(dev.hostname)
        hw = _extract_hw_specs(dev)
        ifaces_info: list[dict[str, Any]] = []
        bgp_info: dict[str, Any] | None = None
        acls_info: list[str] = []

        if intent is not None:
            for iface in getattr(intent, "interfaces", []) or []:
                ip_str = str(iface.ipv4_address) if iface.ipv4_address else None
                ifaces_info.append({
                    "name": iface.name,
                    "ipv4_address": ip_str,
                    "mode": getattr(iface.mode, "value", str(iface.mode)),
                    "mtu": iface.mtu,
                    "enabled": iface.enabled,
                    "ospf_area": str(iface.ospf_area) if iface.ospf_area is not None else None,
                    "acl_in": iface.acl_in,
                    "description": iface.description,
                })
                if ip_str:
                    with contextlib.suppress(ValueError):
                        ip_iface = ipaddress.IPv4Interface(ip_str)
                        hit = (dev, iface.name, str(ip_iface.network))
                        ip_to_dev_iface[str(ip_iface.ip)] = hit
                        ip_to_dev_iface[f"{stand_slug}:{ip_iface.ip}"] = hit
                        if (
                            ip_iface.network.prefixlen in (24, 29, 30, 31)
                            and not iface.name.lower().startswith(("loop", "vbdif", "vlan"))
                        ):
                            subnet_buckets[str(ip_iface.network)].append((
                                dev,
                                iface.name,
                                str(ip_iface.ip),
                                str(iface.ospf_area) if iface.ospf_area is not None else None,
                                iface.acl_in,
                                int(iface.mtu or 1500),
                            ))
            if getattr(intent, "bgp", None) is not None:
                bgp = intent.bgp
                bgp_info = {
                    "asn": bgp.asn,
                    "router_id": str(bgp.router_id),
                    "evpn": bool(bgp.evpn),
                    "vxlan_vni": bgp.vxlan_vni,
                    "neighbors_count": len(bgp.neighbors or []),
                }
            acls_info = [a.name for a in (getattr(intent, "acls", []) or [])]

        drain_entry = drained_map.get(str(dev.id)) or {}
        plat_str = getattr(dev.platform, "value", str(dev.platform))
        dev_plan = collector_plan.get(dev.hostname) or {}
        override_mode = proto_overrides.get(dev.hostname)
        ports = resolve_device_ports(
            str(dev.management_ip), management_port=dev.management_port, platform=plat_str
        )
        active_proto = (
            override_mode
            if (override_mode and override_mode != "auto")
            else (
                "ssh_cli"
                if plat_str == "huawei_vrp"
                else str(dev_plan.get("protocol") or "ssh_cli")
            )
        )
        raw_status = db_status_map.get(dev.id) or getattr(dev.status, "value", str(dev.status))
        nodes_out.append({
            "id": dev.id,
            "hostname": dev.hostname,
            "platform": plat_str,
            "role": getattr(dev.role, "value", str(dev.role)),
            "status": raw_status,
            "oper_status": getattr(dev, "oper_status", "UNKNOWN") or "UNKNOWN",
            "drained": bool(drain_entry.get("drained")),
            "drain_reason": drain_entry.get("reason"),
            "management_ip": str(dev.management_ip),
            "management_port": dev.management_port or 22,
            "fabric_id": stand_slug,
            "site": getattr(dev, "site", None) or stand_slug,
            "rack": getattr(dev, "rack", None),
            "unit": getattr(dev, "unit", None),
            "load_pct": _device_load(dev),
            "telemetry_protocol": active_proto,
            "telemetry_mode": override_mode or str(dev_plan.get("selected_mode") or "auto"),
            "telemetry_port": int(
                (
                    dev.management_port
                    if active_proto == "ssh_cli"
                    else dev_plan.get("port")
                )
                or ports.get("exporter" if active_proto in ("node_exporter", "prometheus_exporter") else active_proto, dev.management_port)
                or 22
            ),
            "bgp": bgp_info,
            "interfaces": ifaces_info,
            "acls": acls_info,
            "lldp_neighbors": hw.get("lldp_neighbors") or [],
        })

    links_map: dict[str, dict[str, Any]] = {}

    # 1. Synthesize L3 subnet links
    synthesize_l3_subnet_links(subnet_buckets, default_stand, links_map)

    # 2. Enrich/add BGP/EVPN sessions
    synthesize_bgp_evpn_links(devices, intents_map, ip_to_dev_iface, default_stand, links_map)

    # 3. Enrich/add L1/L2 links from Containerlab and LLDP
    synthesize_l2_lldp_links(devices, by_canon, eff, default_stand, links_map)

    # 4. Clos role adjacency and DCI links (Dual-Homed ToR pair for servers)
    by_stand: dict[str, list[Device]] = collections.defaultdict(list)
    for d in devices:
        by_stand[
            getattr(d, "fabric_id", None) or getattr(d, "site", None) or default_stand
        ].append(d)
    synthesize_clos_and_dci_links(by_stand, default_stand, links_map)

    # 5. Merge custom links & apply admin state overrides
    merge_custom_links_and_overrides(
        by_id, eff, default_stand, links_map, db_status_map=db_status_map
    )

    # 6. Analyze Core BFS Reachability
    analyze_core_reachability(nodes_out, links_map)

    stands_rows = list(session.scalars(select(Stand).order_by(Stand.id)).all())
    stands_summary = [
        {
            "slug": st.slug,
            "name": st.name,
            "site": st.site or st.slug,
            "jump_host": st.jump_host,
            "jump_port": st.jump_port,
            "driver": st.driver,
            "status": st.status,
            "device_count": len(by_stand.get(st.slug, [])),
        }
        for st in stands_rows
    ]
    seen_stands = {st.slug for st in stands_rows}
    for slug, st_devs in by_stand.items():
        if slug not in seen_stands:
            stands_summary.append({
                "slug": slug,
                "name": slug,
                "site": slug,
                "jump_host": None,
                "jump_port": 22,
                "driver": getattr(eff, "network_driver", "offline"),
                "status": "ONLINE",
                "device_count": len(st_devs),
            })

    return {
        "nodes": nodes_out,
        "links": list(links_map.values()),
        "stands": stands_summary,
    }
