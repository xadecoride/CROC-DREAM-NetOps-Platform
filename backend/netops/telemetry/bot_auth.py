"""Telegram bot authentication, chat-to-user binding, inline keyboards, and URL helpers."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from netops.constants import const
from netops.db import utcnow
from netops.settings import Settings


def _menu_keyboard() -> list[list[dict[str, str]]]:
    return [
        [
            {"text": "Обзор стенда", "callback_data": "/overview"},
            {"text": "Статус", "callback_data": "/status"},
            {"text": "Метрики", "callback_data": "/metrics"},
        ],
        [
            {"text": "Дрейф", "callback_data": "/drift"},
            {"text": "Задачи", "callback_data": "/jobs"},
            {"text": "🤖 AI-Копилот", "callback_data": "/ai"},
        ],
        [
            {"text": "🔄 Собрать метрики", "callback_data": "/collect"},
            {"text": "🔍 Скан дрейфа", "callback_data": "/scan"},
            {"text": "🔑 Реестр и токены", "callback_data": "/users"},
        ],
        [
            {"text": "🏗 Кластеры/Стенды", "callback_data": "/fabrics"},
            {"text": "🛡 Безопасность", "callback_data": "/security"},
            {"text": "❄️ Kill Switch", "callback_data": "/freeze"},
        ],
    ]


def ensure_user_token(session: Session, user: Any, settings: Settings | None = None) -> str:
    from netops.security import generate_token, hash_token
    from netops.services.notifier import load_user_tokens, save_user_token
    from netops.services.sessions import issue_invite_code

    tokens = load_user_tokens(settings)
    existing = tokens.get(user.username)
    if existing and user.token_sha256 == hash_token(existing):
        return existing
    if not getattr(user, "token_sha256", None):
        token = generate_token()
        user.token_sha256 = hash_token(token)
        session.commit()
        save_user_token(user.username, token, settings)
        return token
    return issue_invite_code(user.username)


def bind_telegram_chat_user(
    session: Session,
    chat_id: int,
    *,
    tg_username: str | None = None,
    first_name: str | None = None,
    explicit_token: str | None = None,
    settings: Settings | None = None,
) -> tuple[Any, str, str]:
    from netops.enums import UserRole
    from netops.models import User
    from netops.security import generate_token, hash_token
    from netops.services.notifier import (
        KNOWN_TELEGRAM_PROFILES,
        load_telegram_bindings,
        save_telegram_binding,
        save_user_token,
        sync_access_registry,
    )
    from netops.services.sessions import consume_invite_code

    known = KNOWN_TELEGRAM_PROFILES.get(chat_id, {})
    bindings = load_telegram_bindings(settings).get(str(chat_id), {})
    tg_handle = (
        tg_username or bindings.get("tg_username") or known.get("tg_username") or ""
    ).lstrip("@")
    disp_name = (
        first_name or bindings.get("first_name") or known.get("first_name") or tg_handle or str(chat_id)
    )
    user: User | None = None

    if explicit_token and explicit_token not in ("claim_owner", "recover_owner"):
        tok = explicit_token.strip()
        if tok.startswith("inv_"):
            inv_username = consume_invite_code(tok)
            if inv_username:
                user = session.scalar(select(User).where(User.username == inv_username))
        if user is None:
            now = utcnow()
            user = session.scalar(
                select(User).where(
                    User.is_active.is_(True),
                    (
                        (User.verification_code == tok)
                        & (
                            User.verification_expires_at.is_(None)
                            | (User.verification_expires_at >= now)
                        )
                    )
                    | (User.token_sha256 == hash_token(tok)),
                )
            )
        if user is not None:
            if user.token_sha256 == hash_token(tok):
                save_user_token(user.username, tok, settings)
            user.verification_code = None
            user.verification_expires_at = None

    if user is None:
        user = session.scalar(select(User).where(User.telegram_chat_id == str(chat_id)))

    owner_user = session.scalar(select(User).where(User.username == "owner"))
    if (
        user is None
        and (not explicit_token or explicit_token in ("claim_owner", "recover_owner"))
        and (
            owner_user is None
            or not owner_user.telegram_chat_id
            or owner_user.telegram_chat_id == str(chat_id)
        )
    ):
        if owner_user is None:
            owner_tok = generate_token()
            owner_user = User(
                username="owner",
                role=UserRole.OWNER,
                token_sha256=hash_token(owner_tok),
                is_active=True,
                telegram_chat_id=str(chat_id),
                telegram_username=tg_handle or None,
                telegram_verified_at=utcnow(),
            )
            session.add(owner_user)
            session.commit()
            save_user_token("owner", owner_tok, settings)
        user = owner_user

    if user is None:
        target_username = (
            known.get("username")
            or bindings.get("username")
            or (tg_handle.lower() if tg_handle else f"tg-{chat_id}")
        )
        if session.scalar(select(User).where(User.username == target_username)) is not None:
            target_username = f"tg-{chat_id}"
        role_enum = (
            UserRole.ADMIN
            if known.get("role") in ("owner", "admin")
            else UserRole.OPERATOR
            if known.get("role") == "operator"
            else UserRole.VIEWER
        )
        default_tok = generate_token()
        user = User(
            username=target_username,
            role=role_enum,
            token_sha256=hash_token(default_tok),
            is_active=True,
            telegram_chat_id=str(chat_id),
            telegram_username=tg_handle or None,
            telegram_verified_at=utcnow(),
        )
        session.add(user)
        session.commit()
        save_user_token(user.username, default_tok, settings)

    user.telegram_chat_id = str(chat_id)
    if tg_handle:
        user.telegram_username = tg_handle
    if user.telegram_verified_at is None:
        user.telegram_verified_at = utcnow()
    session.commit()

    token = ensure_user_token(session, user, settings)
    role_badge = "owner" if user.username == "owner" else user.role.value
    if settings is not None:
        if user.username == "owner":
            settings.save_runtime_overlay({
                "telegram_owner_linked": True,
                "telegram_chat_id": str(chat_id),
                "telegram_verified_username": tg_handle or disp_name,
            })
        save_telegram_binding(
            chat_id,
            {
                "user_id": user.id,
                "username": user.username,
                "tg_username": tg_handle,
                "first_name": disp_name,
                "role": role_badge,
                "allowed_system": user.allowed_system or "*",
                "verified": True,
            },
            settings,
        )
        sync_access_registry(session, settings)
    return user, token, role_badge


def _authorize_open_registration_chat(
    session_factory: sessionmaker[Session],
    chat_id: int,
    chat_ids: list[int],
    send_fn: Callable[[int, str], None],
    *,
    from_user: dict[str, Any] | None = None,
    text: str = "",
    settings: Settings | None = None,
) -> bool:
    from netops.models import User
    from netops.security import hash_token
    from netops.services.notifier import KNOWN_TELEGRAM_PROFILES, save_subscriber
    from netops.services.sessions import peek_invite_code
    from netops.services.users import _is_expired

    tg_user = (from_user or {}).get("username")
    first_name = (from_user or {}).get("first_name")
    parts = text.split()
    cmd = parts[0].split("@")[0] if parts else ""
    code_arg = parts[1].strip() if len(parts) > 1 and cmd in ("/verify", "/start", "/auth") else None

    with session_factory() as session:
        matched_user = session.scalar(select(User).where(User.telegram_chat_id == str(chat_id)))
        owner_u = session.scalar(select(User).where(User.username == "owner"))
        if matched_user is None and (
            owner_u is None or not owner_u.telegram_chat_id or chat_id in KNOWN_TELEGRAM_PROFILES
        ):
            matched_user, _, _ = bind_telegram_chat_user(
                session, chat_id, tg_username=tg_user, first_name=first_name, settings=settings
            )
        if matched_user is None and code_arg:
            inv_uname = peek_invite_code(code_arg)
            candidate = (
                session.scalar(select(User).where(User.username == inv_uname))
                if inv_uname
                else session.scalar(
                    select(User).where(
                        (User.verification_code == code_arg)
                        | (User.token_sha256 == hash_token(code_arg))
                    )
                )
            )
            if candidate is not None:
                if (
                    candidate.verification_code == code_arg
                    and candidate.verification_expires_at is not None
                    and _is_expired(candidate.verification_expires_at)
                ):
                    send_fn(chat_id, "[NetOps Security] Срок действия 6-значного кода верификации истёк.")
                    return False
                matched_user = candidate

        if matched_user is None:
            send_fn(
                chat_id,
                f"[NetOps Security] Ваш Telegram (ID: {chat_id}) не привязан и ожидает подтверждения Владельцем. "
                "Отправьте /verify <6-значный КОД>, выданный Владельцем.",
            )
            return False

        if not matched_user.is_active:
            send_fn(
                chat_id,
                f"[NetOps Security] Доступ для {matched_user.username} ожидает подтверждения Владельцем (или заблокирован).",
            )
            return False

        if chat_id not in chat_ids:
            chat_ids.append(chat_id)
        if settings is not None:
            save_subscriber(settings, chat_id)
        bind_telegram_chat_user(
            session,
            chat_id,
            tg_username=tg_user,
            first_name=first_name,
            explicit_token=code_arg,
            settings=settings,
        )
    return True


def _users_markup(session: Session) -> dict[str, Any]:
    from netops.models import User

    rows: list[list[dict[str, str]]] = []
    users = session.scalars(select(User).order_by(User.username)).all()
    current_row: list[dict[str, str]] = []
    for u in users:
        icon = "🚫" if not u.is_active else ("🟢" if (u.telegram_chat_id or u.bound_client_id) else "🔑")
        current_row.append({
            "text": f"{icon} {u.username} [{u.role.value.upper()[:3]}]",
            "callback_data": f"/token {u.username}",
        })
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)
    rows.append([{"text": "🧹 Очистить дубли сессий", "callback_data": "/cleanup"}])
    rows.extend(_menu_keyboard())
    return {"inline_keyboard": rows}


def _public_host(settings: Settings | None = None) -> str:
    return (
        (settings.clab_host if settings and settings.clab_host else None)
        or os.getenv("NETOPS_CLAB_HOST")
        or "localhost"
    )


def _bot_username(settings: Settings | None = None) -> str:
    raw = (getattr(settings, "telegram_bot_username", None) if settings is not None else None) or ""
    return str(raw).lstrip("@") or ""


def _web_urls(settings: Settings | None = None) -> tuple[str, str]:
    host = _public_host(settings)
    owner_port = int(const("console.owner_port"))
    local_port = int(getattr(settings, "frontend_port", None) or owner_port) if settings is not None else owner_port
    pub_override = (
        str(getattr(settings, "public_web_url", "") or "").strip().rstrip("/")
        if settings is not None
        else ""
    )
    if pub_override:
        pub_url = pub_override
    elif host not in ("localhost", "127.0.0.1", "::1"):
        pub_port = int(const("console.role_ports.admin"))
        pub_url = f"http://{host}:{pub_port}"
    else:
        pub_url = f"http://{host}:{local_port}"
    return pub_url, f"http://127.0.0.1:{local_port}"


def _user_card_markup(
    session: Session, username: str, settings: Settings | None = None
) -> dict[str, Any]:
    from netops.models import User

    u = session.scalar(select(User).where(User.username == username))
    if u is None:
        return _users_markup(session)
    tok = ensure_user_token(session, u, settings)
    pub_base, _ = _web_urls(settings)
    bot_name = _bot_username(settings)
    rows: list[list[dict[str, str]]] = [
        [
            {"text": "🌐 Открыть Web-панель", "url": f"{pub_base}/?token={tok}"},
            {"text": "🔗 Привязать TG", "url": f"https://t.me/{bot_name}?start={tok}"},
        ],
        [
            {"text": "👑 Admin", "callback_data": f"/role {u.username} admin"},
            {"text": "🛠 Operator", "callback_data": f"/role {u.username} operator"},
            {"text": "👁 Viewer", "callback_data": f"/role {u.username} viewer"},
        ],
        [
            {"text": "♾ Навсегда", "callback_data": f"/ttl {u.username} forever"},
            {"text": "⏳ 24 часа", "callback_data": f"/ttl {u.username} 1440"},
            {"text": "⏱ 1 час", "callback_data": f"/ttl {u.username} 60"},
        ],
    ]
    sec_row: list[dict[str, str]] = [
        {
            "text": "🚫 Заблокировать" if u.is_active else "✅ Разблокировать",
            "callback_data": f"/{'block' if u.is_active else 'unblock'} {u.username}",
        }
    ]
    if u.bound_client_id:
        sec_row.append({"text": "🔌 Отвязать браузер", "callback_data": f"/unbind {u.username}"})
    rows.append(sec_row)
    rows.append([
        {"text": "⬅️ К списку участников", "callback_data": "/users"},
        {"text": "🏠 Главное меню", "callback_data": "/overview"},
    ])
    return {"inline_keyboard": rows}


def _clear_bound_client_session(session: Session, user: Any) -> None:
    from netops.services.users import UserService

    UserService._clear_device_binding(user)
    session.commit()


def _answer_verify_or_start(
    session: Session,
    command: str,
    args: Sequence[str],
    chat_id: int | None,
    from_user: dict[str, Any] | None,
    settings: Any,
) -> str:
    from netops.telemetry.bot_views import _overview_text

    explicit_tok = args[0] if args else None
    if command == "/verify" and not explicit_tok:
        return "Укажите 6-значный код верификации из веб-панели:\n/verify <КОД>"
    is_recover = explicit_tok == "recover_owner"
    if explicit_tok in ("claim_owner", "recover_owner"):
        explicit_tok = None
    user, token, role_badge = bind_telegram_chat_user(
        session,
        chat_id or 0,
        tg_username=(from_user or {}).get("username"),
        first_name=(from_user or {}).get("first_name"),
        explicit_token=explicit_tok,
        settings=settings,
    )
    if is_recover:
        _clear_bound_client_session(session, user)
    pub_base, loc_base = _web_urls(settings)
    bot_name = _bot_username(settings)
    return (
        f"CROC DREAM NetOps Bot (@{bot_name}) — Верификация подтверждена\n"
        f"Участник: ID #{user.id} {user.username} [{role_badge.upper()}]\n"
        f"Веб-панель (публичная): {pub_base}/?token={token}\n"
        f"Веб-панель (локальная): {loc_base}/?token={token}\n\n"
        + _overview_text(session, settings)
    )


def _answer_recover(session: Session, chat_id: int | None, settings: Any) -> str:
    from netops.models import User
    from netops.services.sessions import issue_invite_code

    user = (
        session.scalar(select(User).where(User.telegram_chat_id == str(chat_id)))
        if chat_id is not None
        else None
    )
    if user is None:
        return "Ваш Telegram ещё не привязан к профилю. Сначала выполните /verify <КОД>."
    _clear_bound_client_session(session, user)
    role_badge = "owner" if user.username == "owner" else user.role.value
    inv_code = issue_invite_code(user.username)
    pub_base, loc_base = _web_urls(settings)
    return (
        f"🔓 Привязка старого ПК для {user.username} [{role_badge.upper()}] успешно сброшена!\n"
        f"• Публичная: {pub_base}/?token={inv_code}\n"
        f"• Локальная: {loc_base}/?token={inv_code}"
    )
