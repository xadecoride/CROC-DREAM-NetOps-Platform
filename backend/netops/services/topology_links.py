"""Topology Link Synthesis and Normalization Engine."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

import yaml

from netops.models import Device
from netops.services.topology_commutation import (
    get_custom_links,
    get_link_overrides,
)
from netops.settings import Settings


def canon_host(name: str) -> str:
    return (name or "").strip().lower().split(".")[0].replace("-", "")


def load_clab_endpoints(settings: Settings) -> list[tuple[str, str, str, str]]:
    """Parse endpoints from configured topology_path or lab/fabric.clab.yaml."""
    candidates = [
        Path(getattr(settings, "clab_topology_path", "") or ""),
        Path(__file__).resolve().parents[3] / "lab" / "fabric.clab.yaml",
        Path("/app/lab/fabric.clab.yaml"),
    ]
    out: list[tuple[str, str, str, str]] = []
    for path in candidates:
        if path and path.is_file():
            with contextlib.suppress(Exception):
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                for item in ((raw.get("topology") or {}).get("links")) or []:
                    eps = item.get("endpoints") if isinstance(item, dict) else None
                    if isinstance(eps, list) and len(eps) == 2:
                        a_host, _, a_port = str(eps[0]).partition(":")
                        b_host, _, b_port = str(eps[1]).partition(":")
                        out.append((
                            canon_host(a_host),
                            a_port or "eth1",
                            canon_host(b_host),
                            b_port or "eth1",
                        ))
            break
    return out


def device_load(dev: Device) -> float:
    spark = getattr(dev, "sparkline", None) or []
    return round(float(spark[-1]), 1) if spark else 0.0


def extract_hw_specs(dev: Device) -> dict[str, Any]:
    raw = getattr(dev, "hardware_specs", None)
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    with contextlib.suppress(Exception):
        parsed = json.loads(str(raw))
        return parsed if isinstance(parsed, dict) else {}
    return {}


def pair_key(a_id: int, b_id: int) -> str:
    return f"link-{min(a_id, b_id)}-{max(a_id, b_id)}"


def device_stand(dev: Device, default_stand: str) -> str:
    return getattr(dev, "fabric_id", None) or getattr(dev, "site", None) or default_stand


def make_base_link(
    da: Device,
    db: Device,
    src_port: str,
    dst_port: str,
    *,
    layer: str,
    layers: list[str],
    protocol: str,
    default_stand: str,
    src_ip: str | None = None,
    dst_ip: str | None = None,
    subnet: str | None = None,
    bgp_asn: str | None = None,
    vxlan_vni: int | None = None,
    mtu: int = 1500,
    acl_in: str | None = None,
) -> dict[str, Any]:
    lid = pair_key(da.id, db.id)
    st1 = device_stand(da, default_stand)
    st2 = device_stand(db, default_stand)
    is_inter = st1 != st2 or layer == "INTER_STAND"
    return {
        "id": lid,
        "source_id": da.id,
        "target_id": db.id,
        "source_hostname": da.hostname,
        "target_hostname": db.hostname,
        "source_port": src_port,
        "target_port": dst_port,
        "source_ip": src_ip,
        "target_ip": dst_ip,
        "subnet": subnet,
        "layer": layer,
        "layers": layers,
        "protocol": protocol,
        "bgp_asn": bgp_asn,
        "vxlan_vni": vxlan_vni,
        "mtu": mtu,
        "acl_in": acl_in,
        "fabric_id": st1,
        "is_inter_stand": is_inter,
        "source_stand": st1,
        "target_stand": st2,
        "load_pct": max(device_load(da), device_load(db)),
        "admin_state": "UP",
    }


def _add_l3_pair(
    endpoints: list[tuple[Device, str, str, str | None, str | None, int]],
    net_str: str,
    default_stand: str,
    links_map: dict[str, dict[str, Any]],
) -> None:
    if len(endpoints) == 2 and endpoints[0][0].id != endpoints[1][0].id:
        (d1, p1, ip1, ospf1, acl1, mtu1), (d2, p2, ip2, ospf2, acl2, mtu2) = endpoints
        st1 = device_stand(d1, default_stand)
        st2 = device_stand(d2, default_stand)
        is_inter = st1 != st2
        lnk = make_base_link(
            d1,
            d2,
            p1,
            p2,
            layer="INTER_STAND" if is_inter else "L3_SUBNET",
            layers=["L2_LLDP", "L3_SUBNET", *(["INTER_STAND"] if is_inter else [])],
            protocol=f"OSPF Area {ospf1 or ospf2}"
            if (ospf1 or ospf2) is not None
            else "IPv4 P2P",
            default_stand=default_stand,
            src_ip=ip1,
            dst_ip=ip2,
            subnet=net_str,
            mtu=min(mtu1, mtu2),
            acl_in=acl1 or acl2,
        )
        links_map[lnk["id"]] = lnk


def synthesize_l3_subnet_links(
    subnet_buckets: dict[str, list[tuple[Device, str, str, str | None, str | None, int]]],
    default_stand: str,
    links_map: dict[str, dict[str, Any]],
) -> None:
    for net_str, endpoints in subnet_buckets.items():
        if len(endpoints) == 2:
            _add_l3_pair(endpoints, net_str, default_stand, links_map)
        elif len(endpoints) > 2:
            by_st: dict[str, list[tuple[Device, str, str, str | None, str | None, int]]] = {}
            for ep in endpoints:
                by_st.setdefault(device_stand(ep[0], default_stand), []).append(ep)
            for st_eps in by_st.values():
                _add_l3_pair(st_eps, net_str, default_stand, links_map)


def synthesize_bgp_evpn_links(
    devices: list[Device],
    intents_map: dict[str, Any],
    ip_to_dev_iface: dict[str, tuple[Device, str, str]],
    default_stand: str,
    links_map: dict[str, dict[str, Any]],
) -> None:
    for dev in devices:
        intent = intents_map.get(dev.hostname)
        bgp = getattr(intent, "bgp", None) if intent else None
        if not bgp:
            continue
        st1 = device_stand(dev, default_stand)
        for nbr in bgp.neighbors or []:
            peer_ip_str = str(nbr.peer_ip)
            peer_hit = ip_to_dev_iface.get(f"{st1}:{peer_ip_str}") or ip_to_dev_iface.get(
                peer_ip_str
            )
            if not peer_hit:
                continue
            peer_dev, peer_port, net_str = peer_hit
            if peer_dev.id == dev.id:
                continue
            lo, hi = (dev.id, peer_dev.id) if dev.id <= peer_dev.id else (peer_dev.id, dev.id)
            lid = f"link-{lo}-{hi}"
            asn_pair = f"AS{bgp.asn} ↔ AS{nbr.remote_asn}"
            vni = bgp.vxlan_vni
            if lid in links_map:
                existing = links_map[lid]
                existing["bgp_asn"] = asn_pair
                eff_vni = vni or existing.get("vxlan_vni")
                if eff_vni:
                    existing["vxlan_vni"] = eff_vni
                if "BGP_EVPN" not in existing["layers"]:
                    existing["layers"].append("BGP_EVPN")
                existing["protocol"] = f"eBGP ({asn_pair})" + (
                    f" · EVPN VNI {eff_vni}" if eff_vni else (" · EVPN" if bgp.evpn else "")
                )
            else:
                st2 = device_stand(peer_dev, default_stand)
                is_inter = st1 != st2
                links_map[lid] = make_base_link(
                    dev,
                    peer_dev,
                    "BGP-Peer",
                    peer_port,
                    layer="INTER_STAND" if is_inter else "BGP_EVPN",
                    layers=["BGP_EVPN", *(["INTER_STAND"] if is_inter else [])],
                    protocol=f"eBGP ({asn_pair})",
                    default_stand=default_stand,
                    src_ip=str(bgp.router_id),
                    dst_ip=peer_ip_str,
                    subnet=net_str,
                    bgp_asn=asn_pair,
                    vxlan_vni=vni,
                )


def synthesize_l2_lldp_links(
    devices: list[Device],
    by_canon: dict[str, Device],
    eff: Settings,
    default_stand: str,
    links_map: dict[str, dict[str, Any]],
) -> None:
    for a_canon, a_port, b_canon, b_port in load_clab_endpoints(eff):
        da, db = by_canon.get(a_canon), by_canon.get(b_canon)
        if not da or not db or da.id == db.id:
            continue
        lnk = make_base_link(
            da,
            db,
            a_port,
            b_port,
            layer="L2_LLDP",
            layers=["L2_LLDP"],
            protocol="Containerlab L1 / LLDP",
            default_stand=default_stand,
        )
        links_map.setdefault(lnk["id"], lnk)

    for dev in devices:
        hw = extract_hw_specs(dev)
        for lldp in hw.get("lldp_neighbors") or []:
            if not isinstance(lldp, dict):
                continue
            nbr_dev = by_canon.get(canon_host(str(lldp.get("neighbor") or "")))
            if not nbr_dev or nbr_dev.id == dev.id:
                continue
            lnk = make_base_link(
                dev,
                nbr_dev,
                str(lldp.get("local_port") or "eth1"),
                str(lldp.get("remote_port") or "eth1"),
                layer="L2_LLDP",
                layers=["L2_LLDP"],
                protocol="IEEE 802.1AB LLDP",
                default_stand=default_stand,
            )
            links_map.setdefault(lnk["id"], lnk)


def synthesize_clos_and_dci_links(
    by_stand: dict[str, list[Device]],
    default_stand: str,
    links_map: dict[str, dict[str, Any]],
) -> None:
    connected_ids: set[int] = {
        int(lnk[k]) for lnk in links_map.values() for k in ("source_id", "target_id")
    }

    for _st_slug, st_devs in by_stand.items():
        spines = [d for d in st_devs if getattr(d.role, "value", str(d.role)) == "spine"]
        leaves = [d for d in st_devs if getattr(d.role, "value", str(d.role)) == "leaf"]
        borders = [d for d in st_devs if "border" in getattr(d.role, "value", str(d.role))]
        servers = [
            d
            for d in st_devs
            if getattr(d.role, "value", str(d.role)) == "server"
            or getattr(d.platform, "value", str(d.platform)) == "linux_server"
        ]
        for idx_s, sp in enumerate(spines, 1):
            for idx_l, lf in enumerate(leaves, 1):
                lnk = make_base_link(
                    sp,
                    lf,
                    f"eth{idx_l}",
                    f"eth{idx_s}",
                    layer="L3_SUBNET",
                    layers=["L2_LLDP", "L3_SUBNET", "BGP_EVPN"],
                    protocol="Clos Fabric Adjacency",
                    default_stand=default_stand,
                )
                links_map.setdefault(lnk["id"], lnk)
        for bd in borders:
            if bd.id in connected_ids:
                continue
            for idx_s, sp in enumerate(spines, 1):
                lnk = make_base_link(
                    bd,
                    sp,
                    f"eth{idx_s}",
                    f"eth{idx_s + 8}",
                    layer="BGP_EVPN",
                    layers=["L2_LLDP", "L3_SUBNET", "BGP_EVPN"],
                    protocol="Border Uplink",
                    default_stand=default_stand,
                )
                links_map.setdefault(lnk["id"], lnk)
        tor_leaves = sorted(leaves, key=lambda x: (x.hostname or "", x.id or 0))[:2]
        for srv in servers:
            if srv.id in connected_ids:
                continue
            for idx_l, lf in enumerate(tor_leaves, 1):
                lnk = make_base_link(
                    lf,
                    srv,
                    "GE1/0/10",
                    f"eth{idx_l}",
                    layer="L2_LLDP",
                    layers=["L2_LLDP", "L3_SUBNET"],
                    protocol="ToR LACP / VLAN 10 Access (10.20.1.0/24)",
                    default_stand=default_stand,
                    subnet="10.20.1.0/24",
                )
                links_map.setdefault(lnk["id"], lnk)

    # Inter-Stand DCI Backbone Links
    stand_gateways: list[Device] = []
    for _st_slug, st_devs in sorted(by_stand.items()):
        gw = next(
            (d for d in st_devs if "border" in getattr(d.role, "value", str(d.role))),
            next((d for d in st_devs if getattr(d.role, "value", str(d.role)) == "spine"), None),
        )
        if gw is not None:
            stand_gateways.append(gw)
    for i in range(len(stand_gateways) - 1):
        ga, gb = stand_gateways[i], stand_gateways[i + 1]
        st_a = device_stand(ga, default_stand)
        st_b = device_stand(gb, default_stand)
        lnk = make_base_link(
            ga,
            gb,
            "DCI-0",
            "DCI-0",
            layer="INTER_STAND",
            layers=["L3_SUBNET", "BGP_EVPN", "INTER_STAND"],
            protocol=f"DCI EVPN Interconnect ({st_a} ↔ {st_b})",
            default_stand=default_stand,
            mtu=9000,
        )
        links_map.setdefault(lnk["id"], lnk)


def merge_custom_links_and_overrides(
    by_id: dict[int, Device],
    eff: Settings,
    default_stand: str,
    links_map: dict[str, dict[str, Any]],
    *,
    db_status_map: dict[int, str] | None = None,
) -> None:
    for custom in get_custom_links(eff):
        da = by_id.get(int(custom.get("source_id") or 0))
        db = by_id.get(int(custom.get("target_id") or 0))
        if not da or not db:
            continue
        st1 = device_stand(da, default_stand)
        st2 = device_stand(db, default_stand)
        layer = str(custom.get("layer") or ("INTER_STAND" if st1 != st2 else "L3_SUBNET"))
        lnk = make_base_link(
            da,
            db,
            str(custom.get("source_port") or "eth1"),
            str(custom.get("target_port") or "eth1"),
            layer=layer,
            layers=list(dict.fromkeys([layer, "L2_LLDP", *(["INTER_STAND"] if st1 != st2 else [])])),
            protocol=str(custom.get("protocol") or "Custom Commutation Link"),
            default_stand=default_stand,
            subnet=custom.get("subnet"),
            bgp_asn=custom.get("bgp_asn"),
            vxlan_vni=custom.get("vxlan_vni"),
        )
        lid = str(custom.get("id") or lnk["id"])
        lnk["id"] = lid
        lnk["admin_state"] = str(custom.get("admin_state") or "UP").upper()
        lnk["is_custom"] = True
        if custom.get("description"):
            lnk["description"] = custom.get("description")
        links_map[lid] = lnk

    overrides = get_link_overrides(eff)
    for lid, link in links_map.items():
        ovr = overrides.get(lid)
        if ovr and ovr.get("admin_state") in ("UP", "SHUTDOWN"):
            link["admin_state"] = ovr["admin_state"]
            link["status_reason"] = ovr.get("reason")
        da = by_id.get(link["source_id"])
        db = by_id.get(link["target_id"])
        st_a = (
            (db_status_map or {}).get(da.id) or str(getattr(da.status, "value", da.status))
            if da
            else "UNKNOWN"
        )
        st_b = (
            (db_status_map or {}).get(db.id) or str(getattr(db.status, "value", db.status))
            if db
            else "UNKNOWN"
        )
        if link["admin_state"] == "SHUTDOWN":
            link["oper_status"] = "SHUTDOWN"
        elif st_a == "UNREACHABLE" or st_b == "UNREACHABLE":
            link["oper_status"] = "DOWN"
        elif st_a == "DRIFT_DETECTED" or st_b == "DRIFT_DETECTED":
            link["oper_status"] = "DEGRADED"
        else:
            link["oper_status"] = "UP"

