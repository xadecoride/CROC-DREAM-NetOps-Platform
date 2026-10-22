"""Cookie-session auth: login/logout, CSRF, instant revocation, and Telegram bot takeover guards."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from netops.enums import UserRole
from netops.models import User
from netops.settings import Settings
from netops.telemetry.bot import bind_telegram_chat_user
from tests.conftest import TOKENS, auth

ADMIN = auth(UserRole.ADMIN)


def _create_user(client: TestClient, username: str, role: str = "operator") -> dict:
    res = client.post("/api/v1/users", json={"username": username, "role": role}, headers=ADMIN)
    assert res.status_code == 201, res.text
    return res.json()


def _login(client: TestClient, token: str) -> str:
    res = client.post("/api/v1/auth/login", json={"token": token}, headers={"X-Client-ID": "pc-1"})
    assert res.status_code == 200, res.text
    set_cookie = res.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie
    return res.json()["csrf_token"]


def test_login_sets_cookie_session_and_no_bearer_needed(client: TestClient) -> None:
    user = _create_user(client, "cookie-op")
    _login(client, user["token"])
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "cookie-op"
    assert me.json()["role"] == "operator"


def test_invalid_token_login_rejected(client: TestClient) -> None:
    res = client.post("/api/v1/auth/login", json={"token": "definitely-not-a-token"})
    assert res.status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401


def test_mutations_require_csrf_header(client: TestClient) -> None:
    user = _create_user(client, "csrf-admin", role="admin")
    csrf = _login(client, user["token"])
    payload = {"username": "made-by-cookie", "role": "viewer"}
    assert client.post("/api/v1/users", json=payload).status_code == 403
    assert (
        client.post("/api/v1/users", json=payload, headers={"X-CSRF-Token": "wrong"}).status_code
        == 403
    )
    assert (
        client.post("/api/v1/users", json=payload, headers={"X-CSRF-Token": csrf}).status_code
        == 201
    )


def test_logout_destroys_session(client: TestClient) -> None:
    user = _create_user(client, "logout-op")
    csrf = _login(client, user["token"])
    sid = client.cookies.get("netops_sid")
    assert client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    # Even replaying the old cookie value does not work: the server-side record is gone.
    client.cookies.set("netops_sid", sid)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_deactivation_role_change_and_rotation_apply_instantly(
    client: TestClient, session: Session
) -> None:
    user = _create_user(client, "revoke-op")
    _login(client, user["token"])
    assert client.get("/api/v1/auth/me").json()["role"] == "operator"

    db_user = session.scalar(select(User).where(User.username == "revoke-op"))
    assert db_user is not None
    db_user.role = UserRole.VIEWER
    session.commit()
    assert client.get("/api/v1/auth/me").json()["role"] == "viewer"

    rotated = client.post(f"/api/v1/users/{user['id']}/rotate", headers=ADMIN)
    assert rotated.status_code == 200, rotated.text
    assert client.get("/api/v1/auth/me").status_code == 401

    _login(client, rotated.json()["token"])
    db_user = session.scalar(select(User).where(User.username == "revoke-op"))
    assert db_user is not None
    db_user.is_active = False
    session.commit()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_static_machine_key_still_works_via_bearer(client: TestClient) -> None:
    res = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {TOKENS[UserRole.VIEWER]}"}
    )
    assert res.status_code == 200
    assert res.json()["role"] == "viewer"


def test_bot_recover_owner_from_foreign_chat_does_not_grant_owner(
    session: Session, settings: Settings
) -> None:
    owner_chat, stranger_chat = 1001, 6666
    owner, _, badge = bind_telegram_chat_user(
        session, owner_chat, tg_username="boss", settings=settings
    )
    assert owner.username == "owner"
    assert badge == "owner"

    for payload in ("recover_owner", "claim_owner", None):
        user, token, badge = bind_telegram_chat_user(
            session, stranger_chat, tg_username="owner", explicit_token=payload, settings=settings
        )
        assert user.username != "owner"
        assert badge == "viewer"
        assert user.role == UserRole.VIEWER
        assert len(token) >= 32  # random, not a guessable croc-<user>-<role> pattern


def test_one_time_invite_code_login_and_replay_rejected(client: TestClient) -> None:
    from netops.services.sessions import issue_invite_code

    _create_user(client, "invite-op", role="operator")
    inv = issue_invite_code("invite-op")
    assert inv.startswith("inv_")

    _login(client, inv)
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "invite-op"

    # Replaying the consumed one-time invite code must fail with 401
    replay = client.post("/api/v1/auth/login", json={"token": inv})
    assert replay.status_code == 401


def test_fabric_scoped_rbac_filters_devices(
    client: TestClient, devices: dict
) -> None:
    res = client.post(
        "/api/v1/users",
        json={"username": "pod1-viewer", "role": "viewer", "allowed_system": "fabric:croc-dc1-pod1"},
        headers=ADMIN,
    )
    assert res.status_code == 201
    _login(client, res.json()["token"])

    listed = client.get("/api/v1/devices")
    assert listed.status_code == 200
    assert len(listed.json()) == 4


def test_telegram_bot_enforces_viewer_rbac(
    session_factory, session: Session, devices: dict
) -> None:
    from netops.security import hash_token
    from netops.telemetry.bot import MetricsBot

    viewer = User(
        username="tg-viewer",
        role=UserRole.VIEWER,
        token_sha256=hash_token("viewer-secret-tok"),
        is_active=True,
        telegram_chat_id="555",
    )
    other = User(
        username="jury-admin",
        role=UserRole.ADMIN,
        token_sha256=hash_token("admin-secret-tok"),
        is_active=True,
    )
    session.add_all([viewer, other])
    session.commit()

    sent: list[tuple[int, str]] = []

    class _Api:
        def send(self, chat_id: int, text: str, reply_markup=None) -> None:
            sent.append((chat_id, text))

        def updates(self, offset):
            return []

    bot = MetricsBot(session_factory, _Api(), [555])
    assert "Недостаточно прав" in bot._answer(session, "/users", chat_id=555)
    assert "Недостаточно прав" in bot._answer(session, "/block", ["jury-admin"], chat_id=555)
    assert "Недостаточно прав" in bot._answer(session, "/role", ["tg-viewer", "admin"], chat_id=555)
    assert "Недостаточно прав" in bot._answer(session, "/collect", chat_id=555)
    assert "Недостаточно прав" in bot._answer(session, "/scan", chat_id=555)
    assert "Недостаточно прав" in bot._answer(session, "/freeze", ["on"], chat_id=555)
    assert "Недостаточно прав" in bot._answer(session, "/token", ["jury-admin"], chat_id=555)
    # Viewer can request their own invite link
    own_card = bot._answer(session, "/token", chat_id=555)
    assert "tg-viewer" in own_card
