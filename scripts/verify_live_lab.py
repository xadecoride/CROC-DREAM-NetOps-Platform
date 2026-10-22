"""Проверка живого стенда: доступность узлов, сбор конфигов и снимок состояния.

Запуск из корня репозитория, когда стенд поднят:

    cd backend && uv run python ../scripts/verify_live_lab.py

Учётки берутся из backend/.env (NETOPS_AUTH_PROFILES), в коде их нет. Инвентарь —
intent-live/inventory.yaml. Ничего не меняет на устройствах: только чтение.
"""

from __future__ import annotations

import socket
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from netops.intent import IntentRepository, InventoryDevice
from netops.network.base import HealthExpectations
from netops.settings import Settings
from netops.toolchain import build_toolchain

_ROOT = Path(__file__).resolve().parent.parent
INTENT_LIVE = _ROOT / "intent-live" if (_ROOT / "intent-live").is_dir() else _ROOT / "intent"
PORT_TIMEOUT = 3.0


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=PORT_TIMEOUT):
            return True
    except OSError:
        return False


def main() -> int:
    settings = Settings(
        _env_file=str(BACKEND_DIR / ".env"),  # type: ignore[call-arg]
        intent_repo_path=INTENT_LIVE,
        network_driver="scrapli",
    )
    issues = IntentRepository(INTENT_LIVE).lint()
    if issues:
        print("Инвентарь не проходит проверку:")
        for issue in issues:
            print(" ", issue)
        return 1

    snapshot = IntentRepository(INTENT_LIVE).load()
    toolchain = build_toolchain(settings)
    print(f"Узлов в инвентаре: {len(snapshot.inventory.devices)}\n")
    print(f"{'устройство':22} {'платформа':13} {'порт':>5}  {'SSH':4} {'конфиг':>7}  состояние")

    failed = 0
    for device in snapshot.inventory.devices:
        spec = InventoryDevice.model_validate(device)
        reachable = port_open(str(spec.management_ip), spec.management_port)
        line = f"{spec.hostname:22} {spec.platform.value:13} {spec.management_port:>5}  "
        if not reachable:
            failed += 1
            print(line + "нет   —        порт закрыт")
            continue

        try:
            target = toolchain.target_for(spec)
            started = time.monotonic()
            result = toolchain.collector.fetch_running_configs([target])[spec.hostname]
            elapsed = time.monotonic() - started
        except Exception as exc:
            failed += 1
            print(line + f"да    —        ошибка подключения: {exc}")
            continue

        if result.config is None:
            failed += 1
            print(line + f"да    —        конфиг не снят: {result.error}")
            continue

        intent = snapshot.intent_for(spec.hostname)
        expectations = HealthExpectations.from_intent(intent) if intent else None
        try:
            health = toolchain.health_probe.snapshot(target, expectations)
        except Exception as exc:
            failed += 1
            print(line + f"да    {len(result.config.splitlines()):>5} стр  снимок не снят: {exc}")
            continue

        established = sum(1 for session in health.bgp_sessions.values() if session.established)
        up = sum(1 for state in health.interfaces.values() if state.is_up)
        print(
            line + f"да    {len(result.config.splitlines()):>5} стр  "
            f"BGP {established}/{len(health.bgp_sessions)}, "
            f"порты {up}/{len(health.interfaces)}, {elapsed:.1f}с"
        )

    print()
    if failed:
        print(f"Недоступно или с ошибками: {failed} из {len(snapshot.inventory.devices)}")
        return 1
    print("Все узлы отвечают, конфиги снимаются, состояние читается")
    return 0


if __name__ == "__main__":
    sys.exit(main())
