from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from netops.db import build_engine, build_session_factory
from netops.enums import DriftStatus
from netops.models import Device
from netops.settings import Settings, get_settings
from netops.telemetry.bot_actions import (
    _ai_copilot_text,
    _cleanup_duplicates_text,
    _collect_now_text,
    _freeze_text,
    _scan_now_text,
    _set_user_active_text,
    _set_user_role_text,
    _set_user_ttl_text,
    _token_text,
    _unbind_user_text,
)
from netops.telemetry.bot_auth import (
    _answer_recover,
    _answer_verify_or_start,
    _authorize_open_registration_chat,
    _bot_username,
    _menu_keyboard,
    _user_card_markup,
    _users_markup,
    bind_telegram_chat_user,
    ensure_user_token,
)
from netops.telemetry.bot_views import (
    _BOT_HELP_TEXT,
    _drift_text,
    _fabrics_text,
    _failed_jobs,
    _jobs_text,
    _max_drift_id,
    _metrics_text,
    _new_alerts,
    _overview_text,
    _security_text,
    _status_text,
    _users_text,
)

logger = logging.getLogger(__name__)

API_URL = "https://api.telegram.org"
IDLE_SECONDS = 60
_BOT_POLLING_ACTIVE = False


class TelegramApi(Protocol):
    def send(self, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None: ...

    def updates(self, offset: int | None) -> list[dict[str, Any]]: ...


class HttpTelegramApi:
    def __init__(self, token: str, *, poll_timeout: int = 2) -> None:
        self._token = token
        self._poll_timeout = poll_timeout

    def send(self, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self._call("sendMessage", payload)

    def edit(
        self, chat_id: int, message_id: int, text: str, reply_markup: dict[str, Any] | None = None
    ) -> bool:
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self._call("editMessageText", payload) is not None

    def answer_callback(self, callback_query_id: str) -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_query_id})

    def updates(self, offset: int | None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": self._poll_timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload, timeout=self._poll_timeout + 10)
        return list(result) if isinstance(result, list) else []

    def _call(self, method: str, payload: dict[str, Any], *, timeout: float = 15.0) -> Any:
        request = urllib.request.Request(
            f"{API_URL}/bot{self._token}/{method}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            logger.warning("Telegram %s: HTTP %s", method, exc.code)
            return None
        except OSError as exc:
            logger.warning("Telegram %s недоступен: %s", method, exc)
            return None
        if not body.get("ok"):
            logger.warning("Telegram %s отказал: %s", method, body.get("description"))
            return None
        return body.get("result")


@dataclass
class BotState:
    last_update_id: int | None = None
    last_drift_id: int = 0
    reported_jobs: set[str] = field(default_factory=set)
    device_status: dict[str, DriftStatus] = field(default_factory=dict)
    reported_errors: set[str] = field(default_factory=set)
    reported_anomalies: dict[str, str] = field(default_factory=dict)


class MetricsBot:
    def __init__(
        self, session_factory: sessionmaker[Session], api: TelegramApi, chat_ids: Sequence[int],
        state: BotState | None = None, *, admin_chat_ids: Sequence[int] | None = None,
        open_registration: bool = False, settings: Settings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._api = api
        self._chat_ids = list(chat_ids)
        self._admin_chat_ids = set(admin_chat_ids if admin_chat_ids is not None else chat_ids)
        self._state = state or BotState()
        self._open_registration = open_registration
        self._settings = settings

    def catch_up(self) -> None:
        with self._session_factory() as session:
            self._state.last_drift_id = _max_drift_id(session)
            self._state.reported_jobs = {str(job.id) for job in _failed_jobs(session)}
            self._state.device_status = {
                device.hostname: DriftStatus(device.status.value)
                for device in session.scalars(select(Device))
                if device.status.value in DriftStatus.__members__.values()
            }

    def send_startup_audit(self) -> str:
        from netops.models import User

        with self._session_factory() as session:
            users = list(session.scalars(select(User).order_by(User.id)).all())
            bound_users = [u for u in users if u.bound_client_id]
            verified_tg = [u for u in users if u.telegram_chat_id]
            mode = (
                "ONLINE SSH (Scrapli)"
                if (self._settings and self._settings.network_driver == "scrapli")
                else "OFFLINE Эмуляция"
            )
            lines = [
                f"Аудит подключений и сессий ({mode})",
                f"Участников: {len(users)} | Веб-сессий: {len(bound_users)} | Telegram: {len(verified_tg)}",
            ]
            for u in users:
                dev_str = f"В СЕТИ: {u.bound_client_id}" if u.bound_client_id else "Свободна"
                lines.append(f"• ID #{u.id} {u.username} [{u.role.value.upper()}] — {dev_str}")
            report = "\n".join(lines)
            for cid in self._owner_chat_ids(session):
                self._send(cid, report, reply_markup={"inline_keyboard": _menu_keyboard()})
            return report

    def _owner_chat_ids(self, session: Session) -> list[int]:
        from netops.models import User

        owner = session.scalar(select(User).where(User.username == "owner"))
        if owner is not None and owner.telegram_chat_id and str(owner.telegram_chat_id).lstrip("-").isdigit():
            return [int(owner.telegram_chat_id)]
        if self._settings is not None and self._settings.telegram_chat_id and str(self._settings.telegram_chat_id).lstrip("-").isdigit():
            return [int(self._settings.telegram_chat_id)]
        return list(self._chat_ids[:1])

    def poll_once(self) -> None:
        global _BOT_POLLING_ACTIVE
        _BOT_POLLING_ACTIVE = True
        for update in self._api.updates(self._state.last_update_id):
            self._state.last_update_id = int(update["update_id"]) + 1
            if "callback_query" in update:
                self._handle_callback(update["callback_query"] or {})
            elif "my_chat_member" in update:
                cm = dict(update["my_chat_member"] or {})
                new_status = str((cm.get("new_chat_member") or {}).get("status") or "")
                if new_status in ("member", "administrator", "creator"):
                    cm.setdefault("text", "/start")
                    self._handle(cm)
            else:
                self._handle(update.get("message") or {})

    def send_alerts(self) -> None:
        with self._session_factory() as session:
            for text in self._new_alerts(session):
                self._broadcast(text)

    def _send(self, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        try:
            self._api.send(chat_id, text, reply_markup=reply_markup)
        except TypeError:
            self._api.send(chat_id, text)

    def _authorize_chat(
        self, chat_id: Any, *, from_user: dict[str, Any] | None = None, text: str = ""
    ) -> bool:
        if not isinstance(chat_id, int):
            return False
        if chat_id in self._admin_chat_ids:
            if self._settings is not None:
                from netops.models import User

                with self._session_factory() as session:
                    owner_u = session.scalar(select(User).where(User.username == "owner"))
                    if owner_u is None or not owner_u.telegram_chat_id:
                        bind_telegram_chat_user(
                            session, chat_id, tg_username=(from_user or {}).get("username"),
                            first_name=(from_user or {}).get("first_name"), settings=self._settings,
                        )
            return True
        if chat_id in self._chat_ids and not self._open_registration:
            return True
        if not self._open_registration:
            logger.warning("Запрос из чата %s отклонён: его нет в NETOPS_TELEGRAM_CHAT_IDS", chat_id)
            return False
        return _authorize_open_registration_chat(
            self._session_factory, chat_id, self._chat_ids, self._send,
            from_user=from_user, text=text, settings=self._settings,
        )

    def _handle_callback(self, cb: dict[str, Any]) -> None:
        cb_id = cb.get("id")
        if cb_id and hasattr(self._api, "answer_callback"):
            self._api.answer_callback(str(cb_id))
        msg = cb.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        message_id = msg.get("message_id")
        from_user = cb.get("from") or {}
        text = str(cb.get("data") or "").strip()
        if not text.startswith("/") or not self._authorize_chat(chat_id, from_user=from_user, text=text):
            return
        self._dispatch(
            int(chat_id), text, from_user=from_user, edit_message_id=int(message_id) if isinstance(message_id, int) else None
        )

    def _handle(self, message: dict[str, Any]) -> None:
        chat_id = (message.get("chat") or {}).get("id")
        from_user = message.get("from") or message.get("chat") or {}
        text = str(message.get("text") or "").strip()
        if not text:
            return
        if not text.startswith("/"):
            if not self._authorize_chat(chat_id, from_user=from_user, text="/ai"):
                return
            self._dispatch(int(chat_id), f"/ai {text}", from_user=from_user)
            return
        if not self._authorize_chat(chat_id, from_user=from_user, text=text):
            return
        self._dispatch(int(chat_id), text, from_user=from_user)

    def _caller_role(self, session: Session, chat_id: int | None) -> str:
        if chat_id is None:
            return "admin"
        from netops.models import User

        u = session.scalar(select(User).where(User.telegram_chat_id == str(chat_id)))
        if u is not None:
            if not u.is_active:
                return "blocked"
            return "owner" if u.username == "owner" else u.role.value
        return "admin" if chat_id in self._admin_chat_ids else "viewer"

    def _dispatch(
        self, chat_id: int, text: str, *, from_user: dict[str, Any] | None = None, edit_message_id: int | None = None
    ) -> None:
        parts = text.split()
        command, args = parts[0].split("@")[0], parts[1:]
        with self._session_factory() as session:
            is_admin = self._caller_role(session, chat_id) in ("owner", "admin")
            answer = self._answer(session, command, args, chat_id=chat_id, from_user=from_user)
            if is_admin and command in ("/token", "/ttl", "/block", "/unblock", "/unbind", "/role") and args:
                target_user = args[0].strip()
                if edit_message_id is not None and command != "/token":
                    card = _token_text(session, [target_user], settings=self._settings)
                    answer = f"✅ {answer}\n\n{card}"
                markup = _user_card_markup(session, target_user, self._settings)
            elif is_admin and command in ("/users", "/tokens", "/cleanup"):
                markup = _users_markup(session)
            else:
                markup = {"inline_keyboard": _menu_keyboard()}

            if edit_message_id is not None and hasattr(self._api, "edit"):
                if self._api.edit(chat_id, edit_message_id, answer, reply_markup=markup):
                    return
            self._send(chat_id, answer, reply_markup=markup)

    def _answer(
        self, session: Session, command: str, args: Sequence[str] = (), *, chat_id: int | None = None, from_user: dict[str, Any] | None = None
    ) -> str:
        if command in ("/start", "/verify"):
            return _answer_verify_or_start(session, command, args, chat_id, from_user, self._settings)
        if command == "/help":
            return _BOT_HELP_TEXT

        role = self._caller_role(session, chat_id)
        if role == "blocked":
            return "🚫 Ваш профиль заблокирован администратором."
        if command == "/recover":
            return _answer_recover(session, chat_id, self._settings)

        admin_commands = {"/users", "/tokens", "/cleanup", "/ttl", "/security", "/block", "/unblock", "/unbind", "/role"}
        if command in admin_commands and role not in ("owner", "admin"):
            return "⛔ Недостаточно прав: управление реестром и доступом доступно только Admin / Owner."

        if command == "/token" and role not in ("owner", "admin"):
            from netops.models import User

            caller = session.scalar(select(User).where(User.telegram_chat_id == str(chat_id))) if chat_id is not None else None
            if caller is None:
                return "⛔ Недостаточно прав."
            if args and args[0].strip() != caller.username:
                return "⛔ Недостаточно прав: вы можете запросить ссылку только для своего профиля."
            args = [caller.username]

        if command in ("/collect", "/scan") and role not in ("owner", "admin", "operator"):
            return "⛔ Недостаточно прав: запуск опроса и сканирования доступен с ролью Operator и выше."
        if command == "/freeze" and args and role not in ("owner", "admin", "operator"):
            return "⛔ Недостаточно прав: управление Kill Switch доступно с ролью Operator и выше."

        simple_routes = {
            "/overview": lambda: _overview_text(session, self._settings),
            "/status": lambda: _status_text(session),
            "/drift": lambda: _drift_text(session),
            "/jobs": lambda: _jobs_text(session),
            "/metrics": _metrics_text,
            "/fabrics": lambda: _fabrics_text(session, self._settings),
            "/stands": lambda: _fabrics_text(session, self._settings),
            "/users": lambda: _users_text(session, self._settings),
            "/tokens": lambda: _users_text(session, self._settings),
            "/cleanup": lambda: _cleanup_duplicates_text(session, self._settings),
            "/token": lambda: _token_text(session, args, settings=self._settings),
            "/ttl": lambda: _set_user_ttl_text(session, args),
            "/security": lambda: _security_text(self._settings),
            "/block": lambda: _set_user_active_text(session, args, active=False),
            "/unblock": lambda: _set_user_active_text(session, args, active=True),
            "/unbind": lambda: _unbind_user_text(session, args),
            "/role": lambda: _set_user_role_text(session, args),
            "/collect": lambda: _collect_now_text(session, self._settings, args),
            "/scan": lambda: _scan_now_text(session, self._session_factory, self._settings),
            "/freeze": lambda: _freeze_text(args, self._settings),
            "/ai": lambda: _ai_copilot_text(session, args, settings=self._settings),
            "/ask": lambda: _ai_copilot_text(session, args, settings=self._settings),
        }
        handler = simple_routes.get(command)
        if handler is not None:
            return handler()
        return _ai_copilot_text(session, [command.lstrip("/"), *args], settings=self._settings)

    def _new_alerts(self, session: Session) -> list[str]:
        return _new_alerts(session, self._state, self._settings)

    def _broadcast(self, text: str) -> None:
        for chat_id in self._chat_ids:
            self._send(chat_id, text)


def build_bot(settings: Settings, token: str) -> MetricsBot:
    import os

    from netops.services.notifier import load_saved_subscribers

    if ":" in token and "PYTEST_CURRENT_TEST" not in os.environ and not getattr(settings, "telegram_bot_username", ""):
        try:
            with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=4.0) as resp:
                body = json.loads(resp.read())
                if body.get("ok"):
                    uname = str((body.get("result") or {}).get("username") or "").strip().lstrip("@")
                    if uname:
                        settings.telegram_bot_username = uname
                        settings.save_runtime_overlay({"telegram_bot_username": uname})
        except Exception as exc:
            logger.debug("Telegram getMe skipped: %s", exc)

    session_factory = build_session_factory(build_engine(settings.database_url))
    admin_chat_ids = list(settings.telegram_chat_ids)
    chat_ids = list(admin_chat_ids)
    for sub in load_saved_subscribers(settings):
        if sub not in chat_ids:
            chat_ids.append(sub)
    return MetricsBot(
        session_factory, HttpTelegramApi(token), chat_ids, admin_chat_ids=admin_chat_ids, open_registration=True, settings=settings
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    if settings.telegram_bot_token is None:
        logger.warning("Бот простаивает: задайте NETOPS_TELEGRAM_BOT_TOKEN")
        while settings.telegram_bot_token is None:
            time.sleep(IDLE_SECONDS)
            settings.apply_runtime_overlay()

    bot = build_bot(settings, settings.telegram_bot_token.get_secret_value())
    bot.catch_up()
    bot.send_startup_audit()
    alert_interval = max(3600, settings.drift_scan_interval_seconds)
    last_alert_ts = time.monotonic()
    logger.info(
        "Бот @%s запущен (чатов: %d, верификация по коду: %s, интервал алертов: %ds)",
        _bot_username(settings), len(bot._chat_ids), bot._open_registration, alert_interval,
    )
    while True:
        t0 = time.monotonic()
        bot.poll_once()
        now = time.monotonic()
        if now - last_alert_ts >= alert_interval:
            bot.send_alerts()
            last_alert_ts = now
        if now - t0 < 2.0:
            time.sleep(2.0)


if __name__ == "__main__":
    main()

__all__ = ["BotState", "HttpTelegramApi", "MetricsBot", "TelegramApi", "bind_telegram_chat_user", "build_bot", "ensure_user_token"]
