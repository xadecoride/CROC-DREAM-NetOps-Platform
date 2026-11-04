"""Browser sessions: opaque HttpOnly cookie -> server-side record in Redis (StateManager).

The cookie carries only a random session id. Role, scope and CSRF secret live server-side,
so revocation is instant: deactivating a user, rotating their token or changing their role
takes effect on the very next request (the record is re-validated against the DB).
"""

from __future__ import annotations

import hmac
import secrets
import time
from typing import Any

from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from netops.enums import UserRole
from netops.models import User
from netops.security import hash_token
from netops.services.state import get_state_manager
from netops.services.users import _is_expired
from netops.settings import ApiPrincipal, Settings

SESSION_COOKIE = "netops_sid"
SECURE_SESSION_COOKIE = "__Host-netops_sid"
CSRF_COOKIE = "netops_csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_KEY = "auth:session:"
_INVITE_KEY = "auth:invite:"


def issue_invite_code(username: str, ttl: int = 3600) -> str:
    """Issue a one-time invite code (inv_*) stored in Redis/StateManager."""
    code = f"inv_{secrets.token_urlsafe(24)}"
    get_state_manager().set_json(_INVITE_KEY + code, {"username": username}, ex=ttl)
    return code


def peek_invite_code(code: str) -> str | None:
    if not code.startswith("inv_"):
        return None
    data = get_state_manager().get_json(_INVITE_KEY + code)
    return str(data["username"]) if isinstance(data, dict) and data.get("username") else None


def consume_invite_code(code: str) -> str | None:
    username = peek_invite_code(code)
    if username:
        get_state_manager().delete(_INVITE_KEY + code)
    return username


def _is_https(request: Request) -> bool:
    # uvicorn rewrites the scheme from X-Forwarded-Proto only for trusted proxies.
    return request.url.scheme == "https"


def read_session_id(request: Request) -> str | None:
    return request.cookies.get(SECURE_SESSION_COOKIE) or request.cookies.get(SESSION_COOKIE)


def create_session(
    response: Response,
    request: Request,
    principal: ApiPrincipal,
    *,
    token: str,
    settings: Settings,
    db_user: bool,
    token_sha256: str | None = None,
) -> str:
    """Persist a session and set the cookies. Returns the CSRF token for the SPA."""
    sid = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    from netops.constants import const

    is_eternal_qr = principal.username == "static_viewer"
    ttl = int(const("session.eternal_ttl_seconds")) if is_eternal_qr else settings.session_ttl_seconds
    get_state_manager().set_json(
        _KEY + sid,
        {
            "username": principal.username,
            "role": principal.role.value,
            "allowed_system": principal.allowed_system,
            "token_sha256": token_sha256 or hash_token(token.strip()),
            "db_user": db_user,
            "csrf": csrf,
            "created_at": int(time.time()),
            "eternal": is_eternal_qr,
        },
        ex=ttl,
    )
    secure = _is_https(request)
    response.set_cookie(
        SECURE_SESSION_COOKIE if secure else SESSION_COOKIE,
        sid,
        max_age=ttl,
        path="/",
        httponly=True,
        secure=secure,
        samesite="strict",
    )
    # Readable by JS on purpose: the SPA echoes it back in X-CSRF-Token.
    response.set_cookie(
        CSRF_COOKIE, csrf, max_age=ttl, path="/", httponly=False, secure=secure, samesite="strict"
    )
    return csrf


def destroy_session(request: Request, response: Response) -> None:
    sid = read_session_id(request)
    if sid:
        get_state_manager().delete(_KEY + sid)
    for name in (SESSION_COOKIE, SECURE_SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/")


def _static_token_alive(settings: Settings, token_sha256: str) -> ApiPrincipal | None:
    for known, principal in settings.api_tokens.items():
        if hmac.compare_digest(hash_token(known), token_sha256):
            return principal
    return None


def principal_from_session(
    request: Request, session: Session, settings: Settings
) -> tuple[ApiPrincipal | None, dict[str, Any] | None]:
    """Resolve the cookie session to a principal, re-validating it against the source of truth."""
    sid = read_session_id(request)
    if not sid:
        return None, None
    record = get_state_manager().get_json(_KEY + sid)
    if not isinstance(record, dict):
        return None, None

    if record.get("db_user"):
        user = session.scalar(select(User).where(User.username == record.get("username")))
        is_static_qr = user is not None and user.username == "static_viewer"
        if (
            user is None
            or not user.is_active
            or (not is_static_qr and _is_expired(user.expires_at))
            or not hmac.compare_digest(user.token_sha256, str(record.get("token_sha256")))
        ):
            get_state_manager().delete(_KEY + sid)
            return None, None
        principal = ApiPrincipal(
            username=user.username, role=user.role, allowed_system=user.allowed_system
        )
    else:
        principal = _static_token_alive(settings, str(record.get("token_sha256")))
        if principal is None:
            get_state_manager().delete(_KEY + sid)
            return None, None
    return principal, record


def csrf_ok(request: Request, record: dict[str, Any]) -> bool:
    if request.method in SAFE_METHODS:
        return True
    sent = request.headers.get(CSRF_HEADER) or ""
    return bool(sent) and hmac.compare_digest(sent, str(record.get("csrf") or ""))


def session_is_owner(request: Request, session: Session, settings: Settings) -> bool:
    auth_header = (request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        tok = auth_header[7:].strip()
        principal = settings.authenticate(tok)
        if principal is not None:
            return principal.role == UserRole.OWNER
        user = session.scalar(select(User).where(User.token_sha256 == hash_token(tok)))
        if (
            user
            and user.is_active
            and not _is_expired(user.expires_at)
            and user.role == UserRole.OWNER
        ):
            return True
    principal, _ = principal_from_session(request, session, settings)
    return principal is not None and principal.role == UserRole.OWNER
