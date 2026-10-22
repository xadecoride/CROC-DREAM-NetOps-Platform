"""Подтверждение деградации протокола телеметрии и журнал его смен (видимый фоллбэк).

Одиночная проба по UDP/NAT-релею теряет ответы, поэтому понижение рабочего протокола до
ssh_cli подтверждается повторными пробами; каждая смена протокола пишется в журнал,
который отдаётся в /telemetry/collectors и показывается в UI.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

from netops.db import utcnow
from netops.enums import TelemetryProtocol
from netops.services.state import get_state_manager
from netops.telemetry.metrics import Sample

EVENTS_KEY = "netops:telemetry:events"
MAX_EVENTS = 100
CONFIRM_ATTEMPTS = 2
CONFIRM_DELAY_S = 5.0
_SSH = TelemetryProtocol.SSH_CLI.value


def confirm_downgrades(
    prev: dict[str, Any],
    plan: dict[str, dict[str, Any]],
    overrides: dict[str, str],
    reprobe: Callable[[str], dict[str, Any]],
) -> None:
    """Перепроверяет устройства, у которых рабочий протокол «упал» в ssh_cli (in-place)."""
    for host, item in plan.items():
        old = (prev.get(host) or {}).get("protocol")
        if host in overrides or old in (None, _SSH) or item.get("protocol") != _SSH:
            continue
        for _ in range(CONFIRM_ATTEMPTS):
            if "PYTEST_CURRENT_TEST" not in os.environ:
                time.sleep(CONFIRM_DELAY_S)
            retry = reprobe(host)
            if retry.get("protocol") != _SSH:
                plan[host] = retry
                break


def record_changes(prev: dict[str, Any], plan: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Дописывает в журнал смены протокола между прошлым и новым планом."""
    now = utcnow().isoformat()
    new_events = []
    for host, item in plan.items():
        old, new = (prev.get(host) or {}).get("protocol"), item.get("protocol")
        if old and old != new:
            reason = (item.get("error") or "probe_failed") if new == _SSH else "probe_ok"
            new_events.append({"ts": now, "hostname": host, "from": old, "to": new, "reason": str(reason)})
    if new_events:
        sm = get_state_manager()
        stored = sm.get_db_state(EVENTS_KEY, [])
        sm.set_db_state(EVENTS_KEY, ((stored if isinstance(stored, list) else []) + new_events)[-MAX_EVENTS:])
    return new_events


def get_events(hostnames: set[str] | None = None, limit: int = 20) -> list[dict[str, Any]]:
    stored = get_state_manager().get_db_state(EVENTS_KEY, [])
    events = [e for e in (stored if isinstance(stored, list) else []) if hostnames is None or e.get("hostname") in hostnames]
    return events[-limit:][::-1]


def metric_samples(plan_devices: dict[str, Any]) -> list[Sample]:
    """Активный протокол по устройствам и число зафиксированных смен протокола."""
    samples = [
        Sample("netops_telemetry_active_protocol", 1.0, {"hostname": h, "protocol": str(item.get("protocol"))})
        for h, item in sorted(plan_devices.items())
        if isinstance(item, dict)
    ]
    counts: dict[tuple[str, str, str], int] = {}
    for e in get_events(limit=MAX_EVENTS):
        key = (str(e.get("hostname")), str(e.get("from")), str(e.get("to")))
        counts[key] = counts.get(key, 0) + 1
    samples.extend(
        Sample("netops_telemetry_fallback_events_total", float(n), {"hostname": h, "from": a, "to": b})
        for (h, a, b), n in sorted(counts.items())
    )
    return samples
