"""Static Multi-Viewer service for CROC DREAM NetOps Platform.

Provides a permanent, reusable read-only viewer access mechanism (token/link + QR code)
that allows unlimited concurrent external observers (jury, auditors, students) to explore
all stands without triggering 1-Device=1-Code binding conflicts, while enabling instant
O(1) session invalidation on token rotation or access disabling.
"""

from __future__ import annotations

import threading
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from netops.constants import const
from netops.db import utcnow
from netops.enums import UserRole
from netops.models import SystemState, User
from netops.security import generate_token, hash_token
from netops.services.state import get_state_manager
from netops.settings import Settings, get_settings

STATIC_VIEWER_USERNAME = "static_viewer"
STATIC_VIEWER_STATE_KEY = "static_viewer"


class StaticViewerService:
    """Manages the static viewer account in PostgreSQL and its plaintext token in SystemState."""

    _lock = threading.RLock()

    @staticmethod
    def _build_url(token: str, base_url: str | None = None, settings: Settings | None = None) -> str:
        s = settings or get_settings()
        if base_url and base_url.strip():
            clean_base = base_url.strip().rstrip("/")
            return f"{clean_base}/?token={token}"
        pub_override = str(getattr(s, "public_web_url", "") or "").strip().rstrip("/")
        if pub_override:
            return f"{pub_override}/?token={token}"
        owner_port = int(const("console.owner_port"))
        local_port = int(getattr(s, "frontend_port", None) or owner_port)
        return f"http://127.0.0.1:{local_port}/?token={token}"

    @staticmethod
    def _load_state_data(
        session: Session, state_mgr: Any, settings: Settings | None = None
    ) -> dict[str, Any] | None:
        state_row = session.get(SystemState, STATIC_VIEWER_STATE_KEY)
        if state_row is not None and isinstance(state_row.value, dict):
            return state_row.value
        db_url = getattr(settings, "database_url", None) if settings is not None else None
        cached = state_mgr.get_db_state(STATIC_VIEWER_STATE_KEY, database_url=db_url)
        return cached if isinstance(cached, dict) else None

    @classmethod
    def _format_viewer_response(
        cls,
        user: User,
        token: str,
        base_url: str | None,
        settings: Settings | None,
        fallback_ts: str,
    ) -> dict[str, Any]:
        role_val = user.role.value if hasattr(user.role, "value") else str(user.role)
        updated_ts = user.updated_at.isoformat() if user.updated_at else fallback_ts
        return {
            "username": user.username,
            "role": role_val,
            "token": token,
            "enabled": user.is_active,
            "url": cls._build_url(token, base_url, settings),
            "allowed_system": user.allowed_system,
            "updated_at": updated_ts,
        }

    @classmethod
    def get_or_create(
        cls,
        session: Session,
        base_url: str | None = None,
        settings: Settings | None = None,
    ) -> dict[str, Any]:
        """Fetch existing static viewer credentials, or generate a fresh pair if absent."""
        with cls._lock:
            user = session.scalar(select(User).where(User.username == STATIC_VIEWER_USERNAME))
            state_mgr = get_state_manager(settings)
            state_data = cls._load_state_data(session, state_mgr, settings)

            token = state_data.get("token") if state_data else None
            enabled = bool(state_data.get("enabled", True)) if state_data else True

            # If user exists and matches stored token hash, reuse it (always eternal: expires_at=None)
            if user is not None and token and user.token_sha256 == hash_token(token):
                if user.expires_at is not None:
                    user.expires_at = None
                    session.commit()
                return cls._format_viewer_response(
                    user, token, base_url, settings, utcnow().isoformat()
                )

            # Use the deterministic eternal token from constants.yaml so QR codes never break across DB resets
            eternal_tok = str(const("console.static_viewer_token")).strip()
            token = eternal_tok if eternal_tok.startswith("crk_viewer_") else f"crk_viewer_{eternal_tok}"
            token_hash = hash_token(token)
            now = utcnow()

            if user is None:
                user = User(
                    username=STATIC_VIEWER_USERNAME,
                    role=UserRole.VIEWER,
                    token_sha256=token_hash,
                    is_active=True,
                    allowed_system=None,
                    bound_client_id=None,
                    bound_ip=None,
                    bound_user_agent=None,
                    bound_at=None,
                    last_seen_at=None,
                    expires_at=None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(user)
            else:
                user.role = UserRole.VIEWER
                user.token_sha256 = token_hash
                user.is_active = enabled
                user.allowed_system = None
                user.bound_client_id = None
                user.expires_at = None
                user.updated_at = now

            payload = {
                "enabled": user.is_active,
                "token": token,
                "updated_at": now.isoformat(),
            }

            try:
                session.merge(
                    SystemState(key=STATIC_VIEWER_STATE_KEY, value=payload, updated_at=now)
                )
                session.commit()
                db_url = getattr(settings, "database_url", None) if settings is not None else None
                state_mgr.set_db_state(STATIC_VIEWER_STATE_KEY, payload, database_url=db_url)
            except IntegrityError:
                session.rollback()
                user = session.scalar(select(User).where(User.username == STATIC_VIEWER_USERNAME))
                if user is None:
                    raise
                state_data = cls._load_state_data(session, state_mgr, settings)
                token = (state_data or {}).get("token") or token

            return cls._format_viewer_response(user, token, base_url, settings, now.isoformat())

    @classmethod
    def toggle(
        cls,
        session: Session,
        enabled: bool,
        base_url: str | None = None,
        settings: Settings | None = None,
    ) -> dict[str, Any]:
        """Enable or disable static QR viewer access.

        Disabling immediately causes active sessions to fail with 401 on their next request.
        """
        with cls._lock:
            # Ensure account exists
            info = cls.get_or_create(session, base_url, settings)
            token = info["token"]

            user = session.scalar(select(User).where(User.username == STATIC_VIEWER_USERNAME))
            if user is None:
                raise RuntimeError("Static viewer account missing after get_or_create")

            now = utcnow()
            user.is_active = enabled
            user.updated_at = now
            return cls._persist_viewer_state(session, user, token, now, base_url, settings)

    @classmethod
    def _persist_viewer_state(
        cls,
        session: Session,
        user: User,
        token: str,
        now: Any,
        base_url: str | None,
        settings: Settings | None,
    ) -> dict[str, Any]:
        payload = {
            "enabled": user.is_active,
            "token": token,
            "updated_at": now.isoformat(),
        }
        session.merge(SystemState(key=STATIC_VIEWER_STATE_KEY, value=payload, updated_at=now))
        session.commit()
        db_url = getattr(settings, "database_url", None) if settings is not None else None
        get_state_manager(settings).set_db_state(
            STATIC_VIEWER_STATE_KEY, payload, database_url=db_url
        )
        return cls._format_viewer_response(
            user, token, base_url, settings, now.isoformat()
        )

    @classmethod
    def rotate(
        cls,
        session: Session,
        base_url: str | None = None,
        settings: Settings | None = None,
    ) -> dict[str, Any]:
        """Rotate static viewer token.

        Immediately terminates all existing sessions holding the prior token hash (401 Unauthorized).
        """
        with cls._lock:
            user = session.scalar(select(User).where(User.username == STATIC_VIEWER_USERNAME))
            now = utcnow()
            raw_tok = generate_token()
            new_token = f"crk_viewer_{raw_tok.removeprefix('crk_')}"
            new_hash = hash_token(new_token)

            if user is None:
                user = User(
                    username=STATIC_VIEWER_USERNAME,
                    role=UserRole.VIEWER,
                    token_sha256=new_hash,
                    is_active=True,
                    allowed_system=None,
                    bound_client_id=None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(user)
            else:
                user.token_sha256 = new_hash
                user.bound_client_id = None
                user.updated_at = now

            return cls._persist_viewer_state(session, user, new_token, now, base_url, settings)
