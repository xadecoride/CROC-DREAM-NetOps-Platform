"""Telegram and Webhook Notification Dispatcher for NetOps Platform.

Provides incident alerting (Drift Detected, Deploy Rollback, Chaos Events)
and AI-generated executive network summaries via Telegram Bot API.
Standard library only (urllib.request).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import UTC
from typing import Any

from netops.settings import Settings

logger = logging.getLogger("netops.notifier")

KNOWN_TELEGRAM_PROFILES: dict[int, dict[str, str]] = {}


def load_user_tokens(settings: Settings | None = None) -> dict[str, str]:
    from netops.services.state import get_state_manager

    sm = get_state_manager(settings)
    db_url = getattr(settings, "database_url", None) if settings is not None else None
    merged: dict[str, str] = {}
    db_data = sm.get_db_state("user_tokens", {}, database_url=db_url)
    if isinstance(db_data, dict):
        for k, v in db_data.items():
            if isinstance(k, str) and isinstance(v, str) and v:
                merged[k] = v
    data = sm.get_json("auth:user_tokens")
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(k, str) and isinstance(v, str) and v:
                merged[k] = v
    return merged


def save_user_token(username: str, token: str, settings: Settings | None = None) -> None:
    from netops.services.state import get_state_manager

    sm = get_state_manager(settings)
    current = load_user_tokens(settings)
    current[username] = token
    sm.set_json("auth:user_tokens", current, ex=86400)
    db_url = getattr(settings, "database_url", None) if settings is not None else None
    sm.set_db_state("user_tokens", current, database_url=db_url)


def load_telegram_bindings(settings: Settings | None = None) -> dict[str, dict[str, Any]]:
    from netops.services.state import get_state_manager

    merged: dict[str, dict[str, Any]] = {}
    db_url = getattr(settings, "database_url", None) if settings is not None else None
    data = get_state_manager(settings).get_db_state(
        "telegram_bindings", {}, database_url=db_url
    )
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict):
                clean = {kk: vv for kk, vv in v.items() if kk != "token"}
                merged[str(k)] = {**merged.get(str(k), {}), **clean}
    return merged


def save_telegram_binding(
    chat_id: int,
    info: dict[str, Any],
    settings: Settings | None = None,
) -> None:
    from netops.services.state import get_state_manager

    current = load_telegram_bindings(settings)
    key = str(chat_id)
    clean_info = {k: v for k, v in info.items() if k != "token"}
    current[key] = {**current.get(key, {}), "chat_id": chat_id, **clean_info}
    db_url = getattr(settings, "database_url", None) if settings is not None else None
    get_state_manager(settings).set_db_state(
        "telegram_bindings", current, database_url=db_url
    )


def load_access_registry(settings: Settings | None = None) -> dict[str, Any]:
    from netops.services.state import get_state_manager

    db_url = getattr(settings, "database_url", None) if settings is not None else None
    sm = get_state_manager(settings)
    events = sm.get_db_state("auth:security_events", None, database_url=db_url)
    if not isinstance(events, list):
        events = sm.get_json("auth:security_events")
    return {
        "updated_at": None,
        "system_info": {},
        "participants": [],
        "telegram_bindings": load_telegram_bindings(settings),
        "systems": [{"id": "*", "label": "Вся фабрика CLOS (все узлы)"}],
        "security_events": events if isinstance(events, list) else [],
    }


def record_security_event(
    event_type: str,
    message: str,
    *,
    username: str | None = None,
    client_id: str | None = None,
    client_ip: str | None = None,
    telegram_chat_id: str | int | None = None,
    severity: str = "warning",
    settings: Settings | None = None,
) -> dict[str, Any]:
    from datetime import datetime

    from netops.services.state import get_state_manager

    db_url = getattr(settings, "database_url", None) if settings is not None else None
    state = get_state_manager(settings)
    events = state.get_db_state("auth:security_events", None, database_url=db_url)
    if not isinstance(events, list):
        events = state.get_json("auth:security_events")
    ev_list = list(events) if isinstance(events, list) else []
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "type": event_type,
        "severity": severity,
        "message": message,
        "username": username,
        "client_id": client_id,
        "client_ip": client_ip,
        "telegram_chat_id": str(telegram_chat_id) if telegram_chat_id is not None else None,
    }
    ev_list.insert(0, entry)
    trimmed = ev_list[:50]
    state.set_json("auth:security_events", trimmed, ex=86400)
    state.set_db_state("auth:security_events", trimmed, database_url=db_url)
    return entry


def sync_access_registry(session: Any, settings: Settings | None = None) -> dict[str, Any]:
    return load_access_registry(settings)


def load_saved_subscribers(settings: Settings) -> list[int]:
    from netops.services.state import get_state_manager

    result: list[int] = []
    db_url = getattr(settings, "database_url", None) if settings is not None else None
    data = get_state_manager(settings).get_db_state(
        "telegram_subscribers", [], database_url=db_url
    )
    if isinstance(data, list):
        for x in data:
            if isinstance(x, (int, str)) and str(x).lstrip("-").isdigit():
                cid = int(x)
                if cid not in result:
                    result.append(cid)
    return result


def save_subscriber(settings: Settings, chat_id: int) -> None:
    from netops.services.state import get_state_manager

    current = load_saved_subscribers(settings)
    if chat_id not in current:
        current.append(chat_id)
    db_url = getattr(settings, "database_url", None) if settings is not None else None
    get_state_manager(settings).set_db_state(
        "telegram_subscribers", current, database_url=db_url
    )


def resolve_telegram_targets(settings: Settings, *, discover: bool = True) -> list[int | str]:
    _ = discover
    targets: list[int | str] = list(settings.telegram_chat_ids)
    if settings.telegram_chat_id and settings.telegram_chat_id not in [str(t) for t in targets]:
        targets.append(settings.telegram_chat_id)
    for sub in load_saved_subscribers(settings):
        if sub not in targets and str(sub) not in [str(t) for t in targets]:
            targets.append(sub)
    return targets


def resolve_owner_telegram_targets(settings: Settings) -> list[int | str]:
    if settings.telegram_chat_id:
        cid = str(settings.telegram_chat_id).strip()
        if cid:
            return [int(cid) if cid.lstrip("-").isdigit() else cid]
    for cid_str, info in load_telegram_bindings(settings).items():
        if isinstance(info, dict) and (
            info.get("username") == "owner" or info.get("role") == "owner"
        ):
            return [int(cid_str) if cid_str.lstrip("-").isdigit() else cid_str]
    if settings.telegram_chat_ids:
        return [
            int(c) if str(c).strip().lstrip("-").isdigit() else str(c).strip()
            for c in settings.telegram_chat_ids
            if str(c).strip()
        ]
    return []


def send_telegram_message(
    settings: Settings,
    text: str,
    parse_mode: str = "HTML",
    reply_markup: dict[str, Any] | None = None,
    *,
    owner_only: bool = True,
    chat_ids: list[int | str] | None = None,
) -> bool:
    """Send text message to configured Telegram chat(s) (defaults to bound Owner)."""
    if not settings.telegram_bot_token:
        logger.debug("Telegram notifications unconfigured (missing token)")
        return False
    targets = (
        list(chat_ids)
        if chat_ids
        else (
            resolve_owner_telegram_targets(settings)
            if owner_only
            else resolve_telegram_targets(settings)
        )
    )
    if not targets:
        logger.debug("Telegram notifications unconfigured (no chat_id discovered yet)")
        return False

    token = settings.telegram_bot_token.get_secret_value()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    delivered = False
    for chat_id in targets:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "CROC-NetOps/1.0"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                if resp.status == 200:
                    delivered = True
        except Exception as exc:
            logger.warning("Failed to deliver Telegram notification to %s: %s", chat_id, exc)
    return delivered


def format_executive_summary(
    total_devices: int,
    in_sync: int,
    drifted: list[str],
    unreachable: list[str],
    active_sessions: int,
    git_branch: str = "main",
    git_sha: str = "HEAD",
) -> str:
    """Format full executive fabric summary for Telegram."""
    health_state = "OK" if not drifted and not unreachable else "WARN" if not unreachable else "CRIT"
    lines = [
        f"<b>[CROC DREAM NetOps | {health_state}] Сводка фабрики CLOS</b>",
        f"<b>Git SoT:</b> <code>{git_branch}@{git_sha}</code>",
        f"<b>Устройств в фабрике:</b> {total_devices} (в синхронизации: {in_sync})",
    ]
    if drifted:
        lines.append(f"<b>Дрейф ({len(drifted)}):</b> <code>{', '.join(drifted)}</code>")
    else:
        lines.append("<b>Дрейф конфигурации:</b> отсутствует")
    if unreachable:
        lines.append(f"<b>Недоступны ({len(unreachable)}):</b> <code>{', '.join(unreachable)}</code>")
    lines.append(f"<b>Привязанных сессий по ссылкам:</b> {active_sessions}")
    lines.append("\n<i>Команды бота: /status, /drift, /jobs, /metrics, /users</i>")
    return "\n".join(lines)
