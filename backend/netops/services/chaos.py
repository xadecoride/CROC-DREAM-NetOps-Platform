"""Chaos Engineering simulation service for CROC DREAM NetOps Platform.

Extracted from `api/routes/system.py` to keep HTTP controllers thin.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from netops.network import JinjaConfigRenderer


def _push_live_configs(container: Any, hostname: str, lines: list[str]) -> str:
    """Push CLI lines over SSH when running in live Scrapli mode."""
    if container.settings.network_driver != "scrapli":
        return ""
    try:
        from netops.network.scrapli_driver import _ParamikoConnAdapter
        from netops.toolchain import build_toolchain

        toolchain = build_toolchain(container.settings)
        snapshot = container.intents.load()
        dev = next((d for d in snapshot.inventory.devices if d.hostname == hostname), None)
        if dev is None:
            return f" (не найден в инвентаре: {hostname})"
        target = toolchain.target_for(dev)
        with _ParamikoConnAdapter(target, timeout=15) as conn:
            conn.send_configs(lines)
        return " [применено по SSH]"
    except Exception as exc:
        return f" [ошибка SSH: {exc}]"


def _inventory_devices(container: Any) -> list[Any]:
    try:
        return list(container.intents.load().inventory.devices)
    except Exception:
        return []


def _device_intent(container: Any, hostname: str) -> Any | None:
    try:
        return container.intents.load().intent_for(hostname)
    except Exception:
        return None


def _device_platform(container: Any, hostname: str, lab_dir: Path) -> str:
    for d in _inventory_devices(container):
        if str(d.hostname) == hostname:
            return d.platform.value if hasattr(d.platform, "value") else str(d.platform)
    cfg_path = lab_dir / f"{hostname}.cfg"
    if cfg_path.exists():
        text = cfg_path.read_text(encoding="utf-8", errors="ignore")
        if "sysname " in text or "undo portswitch" in text:
            return "huawei_vrp"
    return "cisco_iosxe"


def _pick_target(
    container: Any,
    lab_dir: Path,
    *,
    explicit_target: str | None = None,
    preferred_platform: str | None = None,
    index: int = 0,
    fallback: str | None = None,
) -> str:
    if explicit_target and explicit_target.strip():
        return explicit_target.strip()
    devs = _inventory_devices(container)
    default_domain = getattr(container.settings, "default_domain", "croc.lab") or "croc.lab"
    resolved_fallback = fallback or (
        str(devs[index % len(devs)].hostname) if devs else f"leaf-{index + 1}.{default_domain}"
    )
    if preferred_platform:
        for d in devs:
            plat = d.platform.value if hasattr(d.platform, "value") else str(d.platform)
            if plat == preferred_platform and (
                container.settings.network_driver == "scrapli"
                or (lab_dir / f"{d.hostname}.cfg").exists()
            ):
                return str(d.hostname)
        for d in devs:
            plat = d.platform.value if hasattr(d.platform, "value") else str(d.platform)
            if plat == preferred_platform:
                return str(d.hostname)
    if (lab_dir / f"{resolved_fallback}.cfg").exists():
        return resolved_fallback
    if devs:
        return str(devs[index % len(devs)].hostname)
    return resolved_fallback


def _resolve_acl_name(container: Any, hostname: str, override: str | None = None) -> str:
    if override and override.strip():
        return override.strip()
    intent = _device_intent(container, hostname)
    if intent is not None and getattr(intent, "acls", None):
        return str(intent.acls[0].name)
    return "FABRIC-IN"


def _resolve_uplink_interface(
    container: Any, hostname: str, plat: str, override: str | None = None
) -> str:
    if override and override.strip():
        return override.strip()
    intent = _device_intent(container, hostname)
    if intent is not None and getattr(intent, "interfaces", None):
        uplinks = [
            iface.name
            for iface in intent.interfaces
            if getattr(iface, "enabled", True)
            and not getattr(iface, "shutdown", False)
            and getattr(iface, "mode", "l3") == "l3"
            and not iface.name.lower().startswith(("loopback", "vbdif", "vlan"))
        ]
        if uplinks:
            return str(uplinks[-1])
    if plat == "huawei_vrp":
        return "GE1/0/1"
    if plat == "arista_eos":
        return "Ethernet2"
    return "GigabitEthernet3"


def _shutdown_interface_in_cfg(content: str, iface_name: str) -> str:
    pattern = re.compile(
        rf"(^interface {re.escape(iface_name)}\n(?: [^\n]*\n)*)",
        re.MULTILINE,
    )
    match = pattern.search(content)
    if not match:
        return content
    block = match.group(1)
    if "\n no shutdown\n" in block:
        new_block = block.replace("\n no shutdown\n", "\n shutdown\n")
    elif "\n undo shutdown\n" in block:
        new_block = block.replace("\n undo shutdown\n", "\n shutdown\n")
    elif "\n shutdown\n" not in block:
        new_block = block.rstrip("\n") + "\n shutdown\n"
    else:
        new_block = block
    return content[: match.start()] + new_block + content[match.end() :]


def _apply_cfg_replacements(
    target_cfg: Path,
    replacements: list[tuple[str, str, str]],
    fallback_lines: list[str] | None = None,
) -> None:
    if not target_cfg.exists():
        return
    content = target_cfg.read_text(encoding="utf-8")
    for anchor, guard_sub, new_text in replacements:
        if anchor in content:
            if guard_sub not in content:
                target_cfg.write_text(content.replace(anchor, new_text), encoding="utf-8")
            return
    if fallback_lines and fallback_lines[-1] not in content:
        suffix = "\n" + "\n ".join(fallback_lines) + "\n"
        target_cfg.write_text(content.rstrip("\n") + suffix, encoding="utf-8")


_ACL_DRIFT_REPLACEMENTS = [
    ("20 deny ip any any", "15 permit ip any any", "15 permit ip any any\n 20 deny ip any any"),
    (
        "20 permit tcp any any eq bgp",
        "17 permit ip 10.99.0.0 0.0.255.255 any",
        "17 permit ip 10.99.0.0 0.0.255.255 any\n 20 permit tcp any any eq bgp",
    ),
    (
        "rule 20 deny ip source any destination any",
        "rule 15 permit ip",
        "rule 15 permit ip source any destination any\n rule 20 deny ip source any destination any",
    ),
    (
        "rule 20 permit tcp destination-port eq bgp",
        "rule 17 permit ip source 10.99.0.0",
        "rule 17 permit ip source 10.99.0.0 0.0.255.255\n rule 20 permit tcp destination-port eq bgp",
    ),
]

_HUAWEI_DRIFT_REPLACEMENTS = [
    (
        "rule 20 deny ip source any destination any",
        "rule 15 permit ip source 10.99.0.0",
        "rule 15 permit ip source 10.99.0.0 0.0.255.255 destination any\n rule 20 deny ip source any destination any",
    ),
    (
        "rule 20 permit tcp destination-port eq bgp",
        "rule 17 permit ip source 10.99.0.0",
        "rule 17 permit ip source 10.99.0.0 0.0.255.255\n rule 20 permit tcp destination-port eq bgp",
    ),
    ("20 deny ip any any", "15 permit ip any any", "15 permit ip any any\n 20 deny ip any any"),
    (
        "20 permit tcp any any eq bgp",
        "17 permit ip 10.99.0.0 0.0.255.255 any",
        "17 permit ip 10.99.0.0 0.0.255.255 any\n 20 permit tcp any any eq bgp",
    ),
]


def _execute_acl_drift_chaos(
    container: Any,
    lab_dir: Path,
    *,
    target_hostname: str | None,
    acl_name: str | None,
    preferred_platform: str,
    index: int,
    replacements: list[tuple[str, str, str]],
    prefer_leaf3: bool = False,
    force_huawei_cli: bool = False,
) -> dict[str, str]:
    target_host = _pick_target(
        container,
        lab_dir,
        explicit_target=target_hostname,
        preferred_platform=preferred_platform,
        index=index,
        fallback="leaf-1.croc.lab",
    )
    if prefer_leaf3 and not target_hostname and (lab_dir / "leaf-3.croc.lab.cfg").exists():
        target_host = "leaf-3.croc.lab"
    resolved_acl = _resolve_acl_name(container, target_host, acl_name)
    plat = "huawei_vrp" if force_huawei_cli else _device_platform(container, target_host, lab_dir)
    if plat == "huawei_vrp":
        cli_lines = [
            f"acl name {resolved_acl} advance",
            "rule 17 permit ip source 10.99.0.0 0.0.255.255",
        ]
    else:
        cli_lines = [
            f"ip access-list extended {resolved_acl}",
            "17 permit ip 10.99.0.0 0.0.255.255 any",
        ]
    _apply_cfg_replacements(
        lab_dir / f"{target_host}.cfg", replacements, fallback_lines=cli_lines
    )
    ssh_note = _push_live_configs(container, target_host, cli_lines)
    msg = f"Внедрен несанкционированный ACL ({resolved_acl}) в {target_host}{ssh_note}"
    return {"status": "ok", "message": msg}


def _scenario_port_down(
    container: Any,
    lab_dir: Path,
    *,
    target_hostname: str | None = None,
    interface_name: str | None = None,
) -> dict[str, str]:
    target_host = _pick_target(
        container,
        lab_dir,
        explicit_target=target_hostname,
        index=1,
        fallback="leaf-2.croc.lab",
    )
    plat = _device_platform(container, target_host, lab_dir)
    resolved_iface = _resolve_uplink_interface(container, target_host, plat, interface_name)
    target_cfg = lab_dir / f"{target_host}.cfg"
    if target_cfg.exists():
        content = target_cfg.read_text(encoding="utf-8")
        updated = _shutdown_interface_in_cfg(content, resolved_iface)
        if updated == content and not interface_name and resolved_iface != "GE1/0/1":
            updated = _shutdown_interface_in_cfg(content, "GE1/0/1")
        if updated == content and f"interface {resolved_iface}" not in content:
            updated = content.rstrip("\n") + f"\ninterface {resolved_iface}\n shutdown\n"
        target_cfg.write_text(updated, encoding="utf-8")
    cli_lines = [f"interface {resolved_iface}", "shutdown"]
    ssh_note = _push_live_configs(container, target_host, cli_lines)
    return {
        "status": "ok",
        "message": f"Интерфейс {resolved_iface} на {target_host} переведен в shutdown{ssh_note}",
    }


def _make_acl_drift_scenario(**spec: Any) -> Any:
    def _scenario(
        container: Any,
        lab_dir: Path,
        *,
        target_hostname: str | None = None,
        acl_name: str | None = None,
    ) -> dict[str, str]:
        return _execute_acl_drift_chaos(
            container, lab_dir, target_hostname=target_hostname, acl_name=acl_name, **spec
        )

    return _scenario


_scenario_acl_drift = _make_acl_drift_scenario(
    preferred_platform="cisco_iosxe", index=0, replacements=_ACL_DRIFT_REPLACEMENTS
)
_scenario_huawei_drift = _make_acl_drift_scenario(
    preferred_platform="huawei_vrp",
    index=1,
    replacements=_HUAWEI_DRIFT_REPLACEMENTS,
    prefer_leaf3=True,
    force_huawei_cli=True,
)


def _inject_ai_chaos(
    container: Any,
    lab_dir: Path,
    *,
    target_hostname: str | None,
    index: int,
    fallback: str,
    anomaly_kind: str,
    message_tpl: str,
) -> dict[str, str]:
    from netops.services.ai_guard import AiTelemetryGuard

    target_host = _pick_target(
        container,
        lab_dir,
        explicit_target=target_hostname,
        index=index,
        fallback=fallback,
    )
    AiTelemetryGuard.inject_anomaly(target_host, anomaly_kind)  # type: ignore[arg-type]
    return {
        "status": "ok",
        "message": message_tpl.format(host=target_host),
    }


def _scenario_ai_blackhole(
    container: Any, lab_dir: Path, *, target_hostname: str | None = None
) -> dict[str, str]:
    return _inject_ai_chaos(
        container,
        lab_dir,
        target_hostname=target_hostname,
        index=1,
        fallback="leaf-2.croc.lab",
        anomaly_kind="blackhole",
        message_tpl="Смоделирована 'тихая авария' на {host} (мониторинг телеметрии обнаружит дроп)",
    )


def _scenario_ai_storm(
    container: Any, lab_dir: Path, *, target_hostname: str | None = None
) -> dict[str, str]:
    return _inject_ai_chaos(
        container,
        lab_dir,
        target_hostname=target_hostname,
        index=0,
        fallback="leaf-1.croc.lab",
        anomaly_kind="storm",
        message_tpl="Смоделирован шторм трафика на {host} (мониторинг телеметрии обнаружит аномалию)",
    )


def _scenario_reset_lab(container: Any, lab_dir: Path) -> dict[str, str]:
    from netops.services.ai_guard import AiTelemetryGuard
    from netops.services.state import get_state_manager

    state = get_state_manager()
    state.clear_sync_locks()
    state.delete("netops:topology:link_overrides")
    state.delete("netops:topology:drained_nodes")
    AiTelemetryGuard.clear_all_injections()
    lab_dir.mkdir(parents=True, exist_ok=True)

    renderer = JinjaConfigRenderer(container.settings.templates_path)
    snapshot = container.intents.load()
    for dev in snapshot.inventory.devices:
        AiTelemetryGuard.inject_anomaly(dev.hostname, "clear")
        intent = snapshot.intent_for(dev.hostname)
        if intent is None:
            continue
        cfg = renderer.render(dev, intent)
        (lab_dir / f"{dev.hostname}.cfg").write_text(cfg, encoding="utf-8")
        if container.settings.network_driver == "scrapli":
            plat = dev.platform.value if hasattr(dev.platform, "value") else str(dev.platform)
            acl_name = _resolve_acl_name(container, dev.hostname)
            uplink = _resolve_uplink_interface(container, dev.hostname, plat)
            if plat == "huawei_vrp":
                _push_live_configs(
                    container,
                    dev.hostname,
                    [
                        f"acl name {acl_name} advance",
                        "undo rule 15",
                        "undo rule 17",
                        "quit",
                        f"interface {uplink}",
                        "undo shutdown",
                    ],
                )
            elif plat == "cisco_iosxe":
                _push_live_configs(
                    container,
                    dev.hostname,
                    [
                        f"ip access-list extended {acl_name}",
                        "no 15",
                        "no 17",
                        "exit",
                        f"interface {uplink}",
                        "no shutdown",
                    ],
                )
            elif plat == "arista_eos":
                _push_live_configs(
                    container,
                    dev.hostname,
                    [f"interface {uplink}", "no shutdown"],
                )
    return {
        "status": "ok",
        "message": f"Все устройства ({len(snapshot.inventory.devices)}) успешно сброшены к чистому эталону Git SoT",
    }


def execute_chaos_scenario(
    scenario: str,
    container: Any,
    *,
    target_hostname: str | None = None,
    interface_name: str | None = None,
    acl_name: str | None = None,
) -> dict[str, str]:
    lab_dir = container.settings.offline_lab_path
    if scenario == "acl_drift":
        return _scenario_acl_drift(
            container, lab_dir, target_hostname=target_hostname, acl_name=acl_name
        )
    if scenario in ("port_down", "link_flap", "link_down"):
        return _scenario_port_down(
            container, lab_dir, target_hostname=target_hostname, interface_name=interface_name
        )
    if scenario == "huawei_drift":
        return _scenario_huawei_drift(
            container, lab_dir, target_hostname=target_hostname, acl_name=acl_name
        )
    if scenario in ("ai_blackhole", "bgp_drop", "blackhole"):
        return _scenario_ai_blackhole(container, lab_dir, target_hostname=target_hostname)
    if scenario in ("ai_storm", "storm"):
        return _scenario_ai_storm(container, lab_dir, target_hostname=target_hostname)
    if scenario in ("reset_lab", "reset"):
        return _scenario_reset_lab(container, lab_dir)

    return {"status": "ok", "message": "Неизвестный сценарий"}
