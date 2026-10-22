#!/usr/bin/env python3
"""CROC DREAM NetOps Platform — Master Industrial Automation CLI.

Implements the Enterprise NetOps pipeline inspired by Yandex Cloud 'Annushka'
and RFC 7938 / 7432 / 8365 data center fabric best practices:
1. Intent Schema Validation (SSoT)
2. Multi-Vendor Jinja2 Configuration Rendering (6 vendors)
3. Semantic Hierarchical Configuration Diffing
4. AI Guard / Safety Pre-flight Verification
5. Health Checks & Telemetry Verification (including Telephony MOS/QoS)
6. Continuous Drift Detection & Audit Reporting
7. 1-Click Lab Reset & Full End-to-End Pipeline Execution
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("automate_netops")


def action_validate() -> bool:
    """Validate all Intent models against Pydantic schemas."""
    from netops.intent.repository import IntentRepository

    intent_dir = PROJECT_ROOT / "intent"
    logger.info("--> [1/7] Валидация декларативного Intent (Single Source of Truth) в %s...", intent_dir)
    try:
        repo = IntentRepository(intent_dir, inventory_file="inventory.yaml")
        snapshot = repo.load()
        devices = snapshot.inventory.devices
        logger.info("✓ Успешно валидировано %d устройств в инвентаре:", len(devices))
        for d in devices:
            device_intent = snapshot.intents.get(d.hostname)
            if device_intent:
                logger.info("   * %s [%s, %s]: %d интерфейсов, BGP AS %s, %d ACL",
                            d.hostname, d.platform, d.role,
                            len(device_intent.interfaces),
                            device_intent.bgp.asn if device_intent.bgp else "N/A",
                            len(device_intent.acls))
            else:
                logger.warning("   ! %s: файл спецификации не найден!", d.hostname)
        return True
    except Exception as exc:
        logger.error("✗ Ошибка валидации Intent: %s", exc)
        return False


def action_render() -> dict[str, str]:
    """Render candidate configuration for all devices using Jinja2 templates."""
    from netops.intent.repository import IntentRepository
    from netops.network.rendering import JinjaConfigRenderer

    intent_dir = PROJECT_ROOT / "intent"
    templates_dir = PROJECT_ROOT / "templates"
    logger.info("--> [2/7] Рендеринг мультивендорных конфигураций из Jinja2 шаблонов (%s)...", templates_dir)
    repo = IntentRepository(intent_dir, inventory_file="inventory.yaml")
    snapshot = repo.load()
    renderer = JinjaConfigRenderer(templates_dir)

    rendered_configs: dict[str, str] = {}
    failed: list[str] = []
    for d in snapshot.inventory.devices:
        dev_intent = snapshot.intents.get(d.hostname)
        if not dev_intent:
            logger.warning("! %s: нет intent-спецификации, устройство пропущено при рендеринге", d.hostname)
            continue
        try:
            cfg = renderer.render(device=d, intent=dev_intent)
            rendered_configs[d.hostname] = cfg
            logger.info("✓ %s (%s): сгенерировано %d байт конфигурации", d.hostname, d.platform.value, len(cfg))
        except Exception as exc:
            logger.error("✗ %s (%s): ошибка рендеринга: %s", d.hostname, d.platform.value, exc)
            failed.append(d.hostname)
    if failed:
        # Не возвращаем частичный результат: иначе diff/drift/комплаенс считаются только по успешным устройствам.
        raise RuntimeError(f"Ошибка рендеринга конфигурации для устройств: {', '.join(failed)}")
    return rendered_configs


def _config_lines(cfg: str) -> list[str]:
    """Flatten config into ordered lines qualified by their parent path (by indentation)."""
    out: list[str] = []
    stack: list[tuple[int, str]] = []
    for raw in cfg.splitlines():
        s = raw.strip()
        if not s or s.startswith(('!', '#')):
            continue
        indent = len(raw) - len(raw.lstrip())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        out.append(" > ".join([p for _, p in stack] + [s]))
        stack.append((indent, s))
    return out


def action_diff() -> dict[str, Any]:
    """Compute semantic hierarchical diffs between current running config and Intent."""

    rendered = action_render()
    lab_running = PROJECT_ROOT / "lab" / "running"
    logger.info("--> [3/7] Вычисление семантического диффа (Hierarchical AST Diff) против %s...", lab_running)

    diff_report: dict[str, Any] = {}
    for hostname, target_cfg in rendered.items():
        running_path = lab_running / f"{hostname}.cfg"
        running_cfg = running_path.read_text(encoding="utf-8") if running_path.exists() else ""
        
        target_lines = _config_lines(target_cfg)
        running_lines = _config_lines(running_cfg)

        from collections import Counter
        added = sorted((Counter(target_lines) - Counter(running_lines)).elements())
        removed = sorted((Counter(running_lines) - Counter(target_lines)).elements())
        t_set, r_set = set(target_lines), set(running_lines)
        reordered = [l for l in target_lines if l in r_set] != [l for l in running_lines if l in t_set]
        diff_report[hostname] = {
            "has_diff": len(added) > 0 or len(removed) > 0 or reordered,
            "added_count": len(added),
            "removed_count": len(removed),
            "reordered": reordered,
            "added": added[:5],
            "removed": removed[:5],
            # Полные списки нужны AI Guard: превью [:5] не должно скрывать опасные изменения.
            "added_all": added,
            "removed_all": removed
        }
        status = "DRIFT DETECTED" if diff_report[hostname]["has_diff"] else "IN SYNC"
        logger.info("   * %s: %s (+%d / -%d строк)", hostname, status, len(added), len(removed))

    return diff_report


def action_ai_guard(diff_report: dict[str, Any] | None = None) -> bool:
    """Analyze changes with AI Guard safety rules (preventing management lockouts and routing wipe)."""
    if diff_report is None:
        diff_report = action_diff()

    logger.info("--> [4/7] AI Guard & Safety Pre-flight Verification...")
    safe = True
    import re
    # Опасные строки среди ДОБАВЛЯЕМЫХ (команды отключения/отмены) — полные совпадения по иерархическому пути.
    dangerous_added = [re.compile(p) for p in (
        r"^(?:no router bgp|undo bgp)\b",
        r"^(?:no ip route 0\.0\.0\.0|undo ip route-static 0\.0\.0\.0)\b",
        r"^interface (?:GigabitEthernet1|MEth0/0/0|Management0) > shutdown$",
    )]
    # Опасные строки среди УДАЛЯЕМЫХ (пропадает критическая конфигурация).
    dangerous_removed = [re.compile(p) for p in (
        r"^router bgp \d+$",
        r"^bgp \d+$",
        r"^ip route 0\.0\.0\.0 ",
        r"^ip route-static 0\.0\.0\.0 ",
    )]

    for hostname, info in diff_report.items():
        checks = (
            ("добавления", info.get("added_all", info.get("added", [])), dangerous_added),
            ("удаления", info.get("removed_all", info.get("removed", [])), dangerous_removed),
        )
        for kind, lines, patterns in checks:
            for line in lines:
                if any(p.search(line) for p in patterns):
                    logger.error("⛔ [CRITICAL SAFETY ALERT] %s: Опасное изменение (%s) критической функции: '%s'!", hostname, kind, line)
                    safe = False

    if safe:
        logger.info("✓ AI Guard: Все конфигурационные диффы безопасны для применения (нет риска изоляции устройства).")
    return safe


def action_telemetry_and_health() -> dict[str, Any]:
    """Verify infrastructure health and telephony VoIP/SIP metrics."""
    logger.info("--> [5/7] Сбор телеметрии здоровья оборудования и сервисов телефонии...")
    
    # 1. Device metrics
    health_data = {
        "source": "simulated",  # статические значения, реальный опрос оборудования не выполняется
        "nodes": [
            {"hostname": "spine-1.croc.lab", "role": "spine", "platform": "cisco_iosxe", "cpu_percent": 27.4, "ram_used_mb": 3986, "ram_total_mb": 4096, "bgp_established": 2, "temp_c": 38},
            {"hostname": "spine-2.croc.lab", "role": "spine", "platform": "arista_eos", "cpu_percent": 1.2, "ram_used_mb": 899, "ram_total_mb": 2048, "bgp_established": 2, "temp_c": 36},
            {"hostname": "leaf-1.croc.lab", "role": "leaf", "platform": "huawei_vrp", "cpu_percent": 12.8, "ram_used_mb": 1513, "ram_total_mb": 2048, "bgp_established": 2, "temp_c": 41},
            {"hostname": "leaf-2.croc.lab", "role": "leaf", "platform": "huawei_vrp", "cpu_percent": 11.5, "ram_used_mb": 1523, "ram_total_mb": 2048, "bgp_established": 2, "temp_c": 40},
        ],
        "telephony_service": {
            "service_name": "Enterprise VoIP Trunk / PBX Gateway",
            "active_sip_channels": 48,
            "mean_opinion_score_mos": 4.38,  # > 4.0 is Good/Excellent
            "jitter_ms": 2.4,
            "packet_loss_percent": 0.01,
            "rtt_latency_ms": 14.2,
            "status": "UNKNOWN",  # значения выше — симуляция, статус сервиса не измерялся
        }
    }
    logger.warning("⚠ Телеметрия узлов — СИМУЛЯЦИЯ (статические значения): реальная доступность устройств и состояние BGP не проверялись.")
    logger.warning("⚠ Телефония (СИМУЛЯЦИЯ, не измерено): MOS=%.2f/5.00, Jitter=%.1f ms, Packet Loss=%.2f%%, Активных SIP-каналов=%d",
                health_data["telephony_service"]["mean_opinion_score_mos"],
                health_data["telephony_service"]["jitter_ms"],
                health_data["telephony_service"]["packet_loss_percent"],
                health_data["telephony_service"]["active_sip_channels"])
    return health_data


def action_drift_audit() -> None:
    """Run compliance drift audit and print summary."""
    logger.info("--> [6/7] Непрерывный аудит дрейфа конфигурации (Continuous Compliance Audit)...")
    diffs = action_diff()
    total = len(diffs)
    synced = sum(1 for v in diffs.values() if not v["has_diff"])
    compliance = (synced / total) * 100 if total > 0 else 100.0
    logger.info("=================================================================")
    logger.info("  Итоговый аудит комплаенса: %.1f%% устройств в эталонном состоянии", compliance)
    logger.info("=================================================================")


def action_reset() -> None:
    """Trigger 1-click lab reset script to restore Git SoT."""
    logger.info("--> [RESET] Вызов автоматического сброса стенда к эталону Git...")
    from scripts.reset_lab import reset_environment
    reset_environment(seed=True)
    logger.info("✓ Локальная среда успешно восстановлена к эталону.")


def action_full_pipeline() -> None:
    """Execute the complete end-to-end industrial NetOps pipeline."""
    print("=================================================================")
    print("   CROC DREAM NetOps Platform — Industrial Pipeline (Annushka)   ")
    print("=================================================================")
    t0 = time.time()
    
    # 1. Validation
    if not action_validate():
        sys.exit(1)
        
    # 2. Rendering
    action_render()
    
    # 3. Diff
    diffs = action_diff()
    
    # 4. Safety Guard
    if not action_ai_guard(diffs):
        logger.error("Остановка пайплайна по соображениям безопасности!")
        sys.exit(1)
        
    # 5. Telemetry & Telephony
    action_telemetry_and_health()
    
    # 6. Drift
    action_drift_audit()
    
    elapsed = time.time() - t0
    logger.info("✓ Сквозной пайплайн NetOps успешно выполнен за %.2f сек.", elapsed)


def main() -> int:
    parser = argparse.ArgumentParser(description="CROC DREAM Master Industrial NetOps Automation CLI")
    parser.add_argument(
        "--action",
        choices=["validate", "render", "diff", "ai-guard", "health", "drift", "reset", "full-pipeline"],
        default="full-pipeline",
        help="Действие для выполнения"
    )
    args = parser.parse_args()

    if args.action == "validate":
        ok = action_validate()
        return 0 if ok else 1
    elif args.action == "render":
        action_render()
        return 0
    elif args.action == "diff":
        action_diff()
        return 0
    elif args.action == "ai-guard":
        ok = action_ai_guard()
        return 0 if ok else 1
    elif args.action == "health":
        action_telemetry_and_health()
        return 0
    elif args.action == "drift":
        action_drift_audit()
        return 0
    elif args.action == "reset":
        action_reset()
        return 0
    elif args.action == "full-pipeline":
        action_full_pipeline()
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
