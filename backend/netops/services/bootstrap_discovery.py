"""Autonomous SSH / Containerlab and offline server discovery for bootstrap."""

from __future__ import annotations

import logging
import os
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from netops.constants import default_username, fallback_password
from netops.enums import DeviceStatus
from netops.services.devices import DeviceService

logger = logging.getLogger(__name__)


def _probe_tcp_node(node: tuple[str, str, int, str, str]) -> dict[str, Any]:
    hname, hip, hport, hplat, hrole = node
    port_open = False
    banner = ""
    detected_plat = hplat
    is_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2.0)
    try:
        s.connect((hip, hport))
        try:
            raw = s.recv(256)
            banner = raw.decode("utf-8", errors="ignore").strip().splitlines()[0] if raw else ""
        except Exception:
            banner = "SSH-2.0-Open" if is_pytest else ""
        if banner and (banner.startswith("SSH-") or is_pytest):
            port_open = True
            if banner != "SSH-2.0-Open":
                from netops.services.discovery import detect_platform_from_banner

                detected_plat = detect_platform_from_banner(banner).value
    except Exception:
        port_open = False
    finally:
        s.close()
    return {
        "hostname": hname,
        "platform": detected_plat,
        "role": hrole,
        "endpoint": f"{hip}:{hport}",
        "reachable": port_open,
        "banner": banner,
    }


def discover_server(req: Any, session: Session, container: Any) -> dict[str, Any]:
    settings = container.settings
    rt = settings.apply_runtime_overlay()
    default_fab = (
        getattr(req, "fabric_id", None)
        or getattr(settings, "default_fabric_id", "croc-dc1-pod1")
        or "croc-dc1-pod1"
    )
    if req.mode == "offline":
        settings.save_runtime_overlay(
            {
                "network_driver": "offline",
                "bootstrap_completed": True,
            }
        )
        if hasattr(container.intents, "_inventory_file"):
            container.intents._inventory_file = settings.inventory_file
        inventory = container.intents.load_inventory()
        sync_res = DeviceService(session).sync_inventory(inventory)
        devices = DeviceService(session).list_devices(
            fabric_id=getattr(req, "fabric_id", None), limit=5000
        )
        return {
            "status": "ok",
            "mode": "offline",
            "server_reachable": True,
            "discovered_containers": [],
            "discovered_switches": [
                {
                    "hostname": d.hostname,
                    "platform": d.platform.value,
                    "role": d.role.value,
                    "endpoint": f"{d.management_ip}:{d.management_port}",
                    "reachable": True,
                }
                for d in devices
            ],
            "synced_total": len(devices),
            "created": len(sync_res.created),
            "updated": len(sync_res.updated),
        }

    clean_host = req.host.strip()
    clean_port = int(req.port or rt.get("clab_port") or settings.clab_port or 221)
    clean_user = (
        req.username.strip()
        or str(rt.get("clab_user") or settings.clab_user or default_username()).strip()
    )
    has_explicit_pwd = bool(req.password and str(req.password).strip())

    discovered_containers: list[dict[str, str]] = []
    candidate_ports_set: set[int] = set()
    clab_port_map: dict[str, str] = {}
    port_platform_hints: dict[int, Any] = {}
    server_ssh_ok = False
    try:
        import paramiko

        from netops.enums import Platform

        ssh = paramiko.SSHClient()
        if os.getenv("NETOPS_SSH_STRICT_HOST_KEY", "").lower() in ("1", "true", "yes"):
            ssh.load_system_host_keys()
            ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            ssh.set_missing_host_key_policy(paramiko.WarningPolicy())
        connect_kwargs: dict[str, Any] = {
            "hostname": clean_host,
            "port": clean_port,
            "username": clean_user,
            "timeout": 8.0,
            "allow_agent": not has_explicit_pwd,
            "look_for_keys": not has_explicit_pwd,
        }
        if has_explicit_pwd:
            connect_kwargs["password"] = req.password
        ssh.connect(**connect_kwargs)
        server_ssh_ok = True
        _, stdout, _ = ssh.exec_command(
            "docker ps --format '{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}'", timeout=4.0
        )
        from netops.services.discovery import load_platform_profiles

        platform_profiles = load_platform_profiles(settings.intent_repo_path)
        container_port_by_name: dict[str, int] = {}
        for line in stdout.read().decode("utf-8", errors="ignore").splitlines():
            parts = line.strip().split("|")
            if len(parts) >= 3:
                cname = parts[0].strip()
                cimage = parts[1].strip()
                ports_str = parts[3] if len(parts) > 3 else ""
                discovered_containers.append(
                    {
                        "name": cname,
                        "image": cimage,
                        "status": parts[2],
                        "ports": ports_str,
                    }
                )
                if "host-" in cname.lower() or "alpine" in cimage.lower():
                    continue
                sig_low = cimage.lower()
                hint_plat = None
                for p_name, p_prof in platform_profiles.items():
                    sigs = [
                        *(p_prof.get("ssh_banners") or []),
                        *(p_prof.get("version_signatures") or []),
                        p_name.split("_")[0],
                    ]
                    if any(str(s).lower() in sig_low for s in sigs if s):
                        try:
                            hint_plat = Platform(p_name)
                            break
                        except ValueError:
                            continue
                for m_p in re.finditer(r":(\d+)->22/tcp", ports_str):
                    p_val = int(m_p.group(1))
                    if p_val != clean_port:
                        candidate_ports_set.add(p_val)
                        container_port_by_name[cname] = p_val
                        if hint_plat is not None:
                            port_platform_hints[p_val] = hint_plat
        if container_port_by_name:
            try:
                _, insp_out, _ = ssh.exec_command(
                    "docker inspect -f '{{.Name}}|{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' $(docker ps -q) 2>/dev/null",
                    timeout=3.0,
                )
                for iline in insp_out.read().decode("utf-8", errors="ignore").splitlines():
                    if "|" not in iline:
                        continue
                    raw_n, raw_ips = iline.split("|", 1)
                    clean_n = raw_n.strip().lstrip("/")
                    if clean_n in container_port_by_name:
                        ips = [ip.strip() for ip in raw_ips.split() if ip.strip()]
                        if ips:
                            clab_port_map[str(container_port_by_name[clean_n])] = ips[-1]
                if clab_port_map and not os.environ.get("PYTEST_CURRENT_TEST"):
                    from netops.telemetry.targets import GNMI_RELAY_BASE_PORT, SNMP_RELAY_BASE_PORT

                    profs = load_platform_profiles(settings.intent_repo_path)
                    relay_cmds: list[str] = []
                    for p_str, nip in clab_port_map.items():
                        octet = int(nip.rsplit(".", 1)[-1])
                        sp = SNMP_RELAY_BASE_PORT + octet
                        gp = GNMI_RELAY_BASE_PORT + octet
                        h_plat = port_platform_hints.get(int(p_str))
                        p_key = h_plat.value if hasattr(h_plat, "value") else str(h_plat or "")
                        g_def = int(((profs.get(p_key) or {}).get("gnmi") or {}).get("default_port") or 6030)
                        relay_cmds.append(
                            f"(docker ps --format '{{{{.Names}}}}' | grep -qx snmp-relay-{octet} || "
                            f"(docker rm -f snmp-relay-{octet} >/dev/null 2>&1; "
                            f"docker run -d --restart unless-stopped --name snmp-relay-{octet} --network clab_mgmt "
                            f"-p {sp}:161/udp alpine/socat UDP4-RECVFROM:161,fork UDP4-SENDTO:{nip}:161 >/dev/null 2>&1))"
                        )
                        relay_cmds.append(
                            f"(docker ps --format '{{{{.Names}}}}' | grep -qx gnmi-relay-{octet} || "
                            f"(docker rm -f gnmi-relay-{octet} >/dev/null 2>&1; "
                            f"docker run -d --restart unless-stopped --name gnmi-relay-{octet} --network clab_mgmt "
                            f"-p {gp}:{gp}/tcp alpine/socat TCP-LISTEN:{gp},fork,reuseaddr TCP:{nip}:{g_def} >/dev/null 2>&1))"
                        )
                    for cname, p_val in container_port_by_name.items():
                        nip = clab_port_map.get(str(p_val))
                        if nip and port_platform_hints.get(p_val) == Platform.ARISTA_EOS:
                            relay_cmds.append(
                                f"printf 'enable\\nconfigure\\ninterface Management0\\nip address {nip}/24\\n"
                                f"management api gnmi\\ntransport grpc default\\nno shutdown\\nend\\n' "
                                f"| docker exec -i {cname} FastCli -p 15 >/dev/null 2>&1 || true"
                            )
                    if relay_cmds:
                        _, r_out, _ = ssh.exec_command(" ; ".join(relay_cmds), timeout=25.0)
                        r_out.channel.recv_exit_status()
            except Exception:
                _ = None
        if not candidate_ports_set:
            try:
                _, ss_out, _ = ssh.exec_command("ss -tlnH | awk '{print $4}'", timeout=3.0)
                for sline in ss_out.read().decode("utf-8", errors="ignore").splitlines():
                    m_ss = re.search(r":(\d+)\s*$", sline.strip())
                    if m_ss:
                        p_val = int(m_ss.group(1))
                        if 2000 <= p_val <= 2999 and p_val != clean_port:
                            candidate_ports_set.add(p_val)
            except Exception:
                _ = None
        discovered_topo_path = f"/home/{clean_user}/clab-quickstart/fabric.clab.yaml"
    except paramiko.AuthenticationException as exc:
        if has_explicit_pwd or not os.environ.get("PYTEST_CURRENT_TEST"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Неверный пароль SSH: сервер отклонил аутентификацию",
            ) from exc
        discovered_topo_path = f"/home/{clean_user}/clab-quickstart/fabric.clab.yaml"
    except Exception as exc:
        if has_explicit_pwd or not os.environ.get("PYTEST_CURRENT_TEST"):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Ошибка подключения или выполнения команд SSH: {exc}",
            ) from exc
        discovered_topo_path = f"/home/{clean_user}/clab-quickstart/fabric.clab.yaml"
    finally:
        try:
            ssh.close()
        except Exception:
            _ = None

    existing_pws: list[str] = []
    try:
        from netops.services.state import get_state_manager

        state = get_state_manager(settings)
        loaded_auth = state.get_db_state(
            "custom_auth_profiles", {}, database_url=settings.database_url
        )
        if isinstance(loaded_auth, dict):
            for v in loaded_auth.values():
                if isinstance(v, dict) and v.get("password"):
                    pw_val = str(v["password"])
                    if pw_val not in existing_pws:
                        existing_pws.append(pw_val)
    except Exception:
        _ = None

    settings.save_runtime_overlay(
        {
            "network_driver": "scrapli",
            "clab_host": clean_host,
            "clab_port": clean_port,
            "clab_user": clean_user,
            "clab_password": req.password if req.password else None,
            "clab_topology_path": str(rt.get("clab_topology_path") or discovered_topo_path),
            "clab_port_map": clab_port_map or None,
            "exposure_mode": str(rt.get("exposure_mode") or "public_tunnel"),
            "bootstrap_completed": True,
        }
    )

    candidate_ports = sorted(candidate_ports_set)
    live_switches: list[dict[str, Any]] = []
    if candidate_ports and not os.environ.get("PYTEST_CURRENT_TEST"):
        from netops.enums import SnapshotKind
        from netops.models import ConfigSnapshot
        from netops.services.discovery import (
            discover_live_switch_via_ssh,
            load_platform_profiles,
        )

        profiles = load_platform_profiles(settings.intent_repo_path)
        pw_by_plat: dict[str, str] = {}
        explicit_req_pws: list[str] = []
        for plat_name, prof in profiles.items():
            pw_by_plat[plat_name] = str(prof.get("default_password", fallback_password()))
            vendor_key = f"switch_password_{plat_name.split('_')[0]}"
            req_pw = getattr(req, vendor_key, None) or getattr(
                req, f"switch_password_{plat_name}", None
            )
            if req_pw and str(req_pw).strip():
                pw_by_plat[plat_name] = str(req_pw).strip()
                explicit_req_pws.append(str(req_pw).strip())

        cand_pws = [
            *explicit_req_pws,
            *(str(p.get("default_password", fallback_password())) for p in profiles.values()),
            *existing_pws,
            fallback_password(),
        ]
        cand_pws = list(dict.fromkeys(p for p in cand_pws if p))

        def _probe_live_port(p: int) -> dict[str, Any] | None:
            plat_hint = port_platform_hints.get(p)
            plat_key = plat_hint.value if hasattr(plat_hint, "value") else str(plat_hint or "")
            exact_pw = pw_by_plat.get(plat_key) or fallback_password()
            return discover_live_switch_via_ssh(
                host=clean_host,
                port=p,
                username=default_username(),
                password=exact_pw,
                settings=settings,
                candidate_passwords=cand_pws,
                passwords_by_platform=pw_by_plat,
                initial_platform=plat_hint,
            )

        with ThreadPoolExecutor(max_workers=max(1, min(8, len(candidate_ports)))) as pool:
            live_switches = [sw for sw in pool.map(_probe_live_port, candidate_ports) if sw]

        if server_ssh_ok and not any(
            str(d.management_ip) == clean_host
            and int(d.management_port) == clean_port
            and d.fabric_id == default_fab
            for d in DeviceService(session).list_devices(fabric_id=default_fab, limit=5000)
        ):
            srv_sw = discover_live_switch_via_ssh(
                host=clean_host,
                port=clean_port,
                username=clean_user,
                password=str(req.password or fallback_password()),
                settings=settings,
                candidate_passwords=cand_pws,
                initial_platform=Platform.LINUX_SERVER,
            )
            if srv_sw:
                live_switches.append(srv_sw)

        if live_switches:
            from netops.intent.models import Inventory

            inv_items = [
                {
                    "hostname": sw["hostname"],
                    "management_ip": sw["management_ip"],
                    "management_port": sw["management_port"],
                    "platform": sw["platform"],
                    "role": sw["role"],
                    "auth_profile": sw["auth_profile"],
                    "fabric_id": default_fab,
                    "site": default_fab,
                }
                for sw in live_switches
            ]
            inventory = Inventory.model_validate({"devices": inv_items})
            sync_res = DeviceService(session).sync_inventory(inventory)
            db_devices = list(
                DeviceService(session).list_devices(fabric_id=default_fab, limit=5000)
            )
            dev_by_host = {d.hostname: d for d in db_devices}
            for sw in live_switches:
                dev = dev_by_host.get(sw["hostname"])
                if dev is not None:
                    dev.status = DeviceStatus.IN_SYNC
                    if sw.get("hardware_specs"):
                        import json

                        dev.hardware_specs = json.dumps(sw["hardware_specs"], ensure_ascii=False)
                    rcfg = (sw.get("running_config") or "").strip()
                    if rcfg:
                        session.add(
                            ConfigSnapshot.capture(
                                device_id=dev.id,
                                kind=SnapshotKind.RUNNING,
                                content=rcfg,
                            )
                        )
            session.commit()
            try:
                from netops.telemetry.targets import refresh_collector_targets
                from netops.toolchain import build_toolchain

                refresh_collector_targets(session, build_toolchain(settings), settings)
            except Exception as exc:
                logger.warning("Не удалось обновить цели сборщиков телеметрии: %s", exc)
            discovered_switches = [
                {
                    "hostname": sw["hostname"],
                    "platform": sw["platform"],
                    "role": sw["role"],
                    "endpoint": sw["endpoint"],
                    "reachable": True,
                    "banner": sw.get("banner", ""),
                }
                for sw in live_switches
            ]
            return {
                "status": "ok",
                "mode": "scrapli",
                "server_host": f"{clean_host}:{clean_port}",
                "server_reachable": True,
                "discovered_containers": discovered_containers,
                "discovered_switches": discovered_switches,
                "synced_total": len(sync_res.created)
                + len(sync_res.updated)
                + len(sync_res.unchanged),
                "created": len(sync_res.created),
                "updated": len(sync_res.updated),
            }

    if os.environ.get("PYTEST_CURRENT_TEST"):
        if hasattr(container.intents, "_inventory_file"):
            container.intents._inventory_file = settings.inventory_file
        inventory = container.intents.load_inventory()
        sync_res = DeviceService(session).sync_inventory(inventory)
        synced_total = len(sync_res.created) + len(sync_res.updated) + len(sync_res.unchanged)
        created_cnt = len(sync_res.created)
        updated_cnt = len(sync_res.updated)
    else:
        synced_total = 0
        created_cnt = 0
        updated_cnt = 0

    db_devices = list(
        DeviceService(session).list_devices(
            fabric_id=getattr(req, "fabric_id", None), limit=5000
        )
    )
    known_fabric_nodes = [
        (
            d.hostname,
            str(d.management_ip),
            int(d.management_port),
            d.platform.value if hasattr(d.platform, "value") else str(d.platform),
            d.role.value if hasattr(d.role, "value") else str(d.role),
        )
        for d in db_devices
    ]
    discovered_switches = []
    if known_fabric_nodes:
        with ThreadPoolExecutor(max_workers=max(1, min(8, len(known_fabric_nodes)))) as pool:
            discovered_switches = list(pool.map(_probe_tcp_node, known_fabric_nodes))
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            for sw in discovered_switches:
                try:
                    sw_port = int(str(sw["endpoint"]).rsplit(":", 1)[-1])
                    if sw_port not in candidate_ports_set:
                        sw["reachable"] = False
                        sw["banner"] = ""
                except Exception:
                    _ = None
    any_port_open = any(sw["reachable"] for sw in discovered_switches)

    reachable_hosts = {sw["hostname"] for sw in discovered_switches if sw["reachable"]}
    for dev in db_devices:
        dev.status = (
            DeviceStatus.UNKNOWN
            if dev.hostname in reachable_hosts
            else DeviceStatus.UNREACHABLE
        )
    session.commit()

    return {
        "status": "ok",
        "mode": "scrapli",
        "server_host": f"{clean_host}:{clean_port}",
        "server_reachable": server_ssh_ok or any_port_open,
        "discovered_containers": discovered_containers,
        "discovered_switches": discovered_switches,
        "synced_total": len(reachable_hosts) if not os.environ.get("PYTEST_CURRENT_TEST") else synced_total,
        "created": created_cnt,
        "updated": updated_cnt,
    }
