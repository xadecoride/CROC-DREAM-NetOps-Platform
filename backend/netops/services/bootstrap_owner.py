"""Owner bootstrap claim, recovery, and role-locked port guard."""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from netops.constants import const
from netops.db import utcnow
from netops.enums import UserRole
from netops.models import User
from netops.security import generate_token, hash_token
from netops.services.notifier import (
    load_user_tokens,
    resolve_telegram_targets,
    save_user_token,
    send_telegram_message,
    sync_access_registry,
)
from netops.services.users import _is_expired


def _reject_non_owner_bearer(request: Request, settings: Any) -> None:
    auth_header = (request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        tok = auth_header[7:].strip()
        principal = settings.authenticate(tok)
        if principal is not None and principal.role != UserRole.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Эта консоль закреплена за не-Owner ролью. Привязка Владельца доступна только на консоли Owner "
                f"({const('console.owner_port')}).",
            )


def _resolve_bot_username(request: Request, settings: Any) -> str:
    _reject_non_owner_bearer(request, settings)
    rt = settings.apply_runtime_overlay()
    return str(
        rt.get("telegram_bot_username")
        or getattr(settings, "telegram_bot_username", "")
        or ""
    ).lstrip("@")


def claim_owner(req: Any, request: Request, session: Session, container: Any) -> dict[str, Any]:
    from netops.services.sessions import session_is_owner

    settings = container.settings
    bot_uname = _resolve_bot_username(request, settings)
    client_id = (request.headers.get("X-Client-Id") or "").strip() or "owner-primary-pc"
    client_ip = request.client.host if request.client else "127.0.0.1"
    user_agent = (request.headers.get("User-Agent") or "Browser")[:250]

    owner = session.scalar(select(User).where(User.username == "owner"))
    user_tokens = load_user_tokens(settings)
    saved_tok = user_tokens.get("owner")

    if owner is not None and not session_is_owner(request, session, settings):
        provided_key = (req.recovery_key or "").strip()
        key_valid = bool(
            provided_key
            and (
                (saved_tok and provided_key == saved_tok)
                or hash_token(provided_key) == owner.token_sha256
            )
        )
        if not key_valid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Права Владельца уже привязаны к другому ПК. "
                    "Чтобы перенести права на этот компьютер, введите ваш Резервный Ключ (Recovery Key) "
                    f"или отправьте /recover в Telegram-боте @{bot_uname}."
                ),
            )

    for stale in session.scalars(
        select(User).where(User.bound_client_id == client_id, User.username != "owner")
    ).all():
        stale.bound_client_id = None
    session.flush()

    token = saved_tok or f"crk_owner_{generate_token()[:24]}"
    if owner is None:
        owner = User(
            username="owner",
            role=UserRole.OWNER,
            token_sha256=hash_token(token),
            is_active=True,
            bound_client_id=client_id,
            bound_ip=client_ip,
            bound_user_agent=user_agent,
            bound_at=utcnow(),
            last_seen_at=utcnow(),
        )
        session.add(owner)
    else:
        owner.is_active = True
        owner.role = UserRole.OWNER
        owner.token_sha256 = hash_token(token)
        owner.bound_client_id = client_id
        owner.bound_ip = client_ip
        owner.bound_user_agent = user_agent
        owner.bound_at = utcnow()
        owner.last_seen_at = utcnow()
    session.commit()

    save_user_token("owner", token, settings)
    sync_access_registry(session, settings)
    rt = settings.apply_runtime_overlay()
    effective_cluster = req.cluster_name.strip() or str(rt.get("cluster_name") or "")
    settings.save_runtime_overlay({"cluster_name": effective_cluster})

    return {
        "status": "claimed",
        "username": "owner",
        "role": "owner",
        "token": token,
        "recovery_key": token,
        "bound_client_id": client_id,
        "bound_ip": client_ip,
        "cluster_name": effective_cluster,
    }


def recover_owner(req: Any, request: Request, session: Session, container: Any) -> dict[str, Any]:
    settings = container.settings
    bot_uname = _resolve_bot_username(request, settings)
    owner = session.scalar(select(User).where(User.username == "owner"))
    if owner is None:
        raise HTTPException(status_code=404, detail="Профиль owner ещё не инициализирован")

    if req.action == "request_telegram_code":
        if not owner.telegram_chat_id:
            discovered = resolve_telegram_targets(settings, discover=True)
            if discovered:
                owner.telegram_chat_id = str(discovered[0])
                session.commit()
        code = f"{secrets.randbelow(900000) + 100000}"
        owner.verification_code = code
        owner.verification_expires_at = utcnow() + timedelta(minutes=15)
        session.commit()
        owner_targets = [owner.telegram_chat_id] if owner.telegram_chat_id else None
        sent = send_telegram_message(
            settings,
            f"🔐 <b>Код переноса прав Владельца (Owner Recovery)</b>\n"
            f"Ваш одноразовый код для привязки нового ПК: <code>{code}</code>\n"
            f"Код действителен 15 минут.",
            owner_only=True,
            chat_ids=owner_targets,
        )
        return {
            "status": "code_sent" if sent else "telegram_not_configured",
            "sent": sent,
            "message": (
                f"6-значный код отправлен владельцу в Telegram (@{bot_uname})!"
                if sent
                else "Telegram ещё не привязан. Используйте ваш Резервный Ключ (Recovery Key)."
            ),
        }

    user_tokens = load_user_tokens(settings)
    saved_tok = user_tokens.get("owner")
    provided_key = (req.recovery_key or "").strip()
    provided_code = (req.telegram_code or "").strip()
    candidates = [x for x in (provided_key, provided_code) if x]

    key_ok = any(
        (saved_tok and c == saved_tok)
        or hash_token(c) == owner.token_sha256
        for c in candidates
    )
    code_ok = any(
        owner.verification_code
        and owner.verification_code == c
        and not _is_expired(owner.verification_expires_at)
        for c in candidates
    )
    if not key_ok and not code_ok:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Неверный резервный ключ или 6-значный Telegram-код",
        )

    client_id = (request.headers.get("X-Client-Id") or "").strip() or "owner-recovered-pc"
    client_ip = request.client.host if request.client else "127.0.0.1"
    user_agent = (request.headers.get("User-Agent") or "Browser")[:250]

    for stale in session.scalars(
        select(User).where(User.bound_client_id == client_id, User.username != "owner")
    ).all():
        stale.bound_client_id = None
    session.flush()

    matched_key = next(
        (c for c in candidates if (saved_tok and c == saved_tok) or hash_token(c) == owner.token_sha256),
        None,
    )
    token = saved_tok or matched_key or f"crk_owner_{generate_token()[:24]}"
    owner.is_active = True
    owner.role = UserRole.OWNER
    owner.token_sha256 = hash_token(token)
    owner.bound_client_id = client_id
    owner.bound_ip = client_ip
    owner.bound_user_agent = user_agent
    owner.bound_at = utcnow()
    owner.last_seen_at = utcnow()
    owner.verification_code = None
    owner.verification_expires_at = None
    session.commit()

    save_user_token("owner", token, settings)
    sync_access_registry(session, settings)

    return {
        "status": "recovered",
        "username": "owner",
        "role": "owner",
        "token": token,
        "recovery_key": token,
        "bound_client_id": client_id,
        "bound_ip": client_ip,
    }
