from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from netops.enums import UserRole
from netops.models import User
from netops.security import hash_token
from tests.conftest import auth

ADMIN = auth(UserRole.ADMIN)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create(client: TestClient, username: str = "duty", role: str = "operator") -> Any:
    response = client.post(
        "/api/v1/users", json={"username": username, "role": role}, headers=ADMIN
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_created_user_can_authenticate(client: TestClient) -> None:
    user = _create(client)
    assert user["role"] == "operator"
    assert user["is_active"] is True
    assert len(user["token"]) >= 40

    me = client.get("/api/v1/auth/me", headers=_bearer(user["token"]))
    assert me.json() == {"username": "duty", "role": "operator"}
    # An operator may start jobs but not manage users.
    assert client.get("/api/v1/users", headers=_bearer(user["token"])).status_code == 403


def test_only_a_hash_of_the_token_is_stored(client: TestClient, session: Session) -> None:
    user = _create(client)
    stored = session.get_one(User, user["id"])
    assert stored.token_sha256 == hash_token(user["token"])
    assert user["token"] not in stored.token_sha256
    listed = client.get("/api/v1/users", headers=ADMIN).json()
    assert "token" not in listed[0]


def test_list_and_get(client: TestClient) -> None:
    _create(client, "zoe", "viewer")
    _create(client, "amir", "admin")
    assert [u["username"] for u in client.get("/api/v1/users", headers=ADMIN).json()] == [
        "amir",
        "zoe",
    ]
    user_id = _create(client, "kim")["id"]
    assert client.get(f"/api/v1/users/{user_id}", headers=ADMIN).json()["username"] == "kim"
    assert client.get("/api/v1/users/999", headers=ADMIN).status_code == 404


def test_role_change_takes_effect_immediately(client: TestClient) -> None:
    user = _create(client, role="viewer")
    response = client.patch(f"/api/v1/users/{user['id']}", json={"role": "admin"}, headers=ADMIN)
    assert response.json()["role"] == "admin"
    assert client.get("/api/v1/users", headers=_bearer(user["token"])).status_code == 200


def test_disabled_user_is_rejected(client: TestClient) -> None:
    user = _create(client)
    client.patch(f"/api/v1/users/{user['id']}", json={"is_active": False}, headers=ADMIN)
    assert client.get("/api/v1/auth/me", headers=_bearer(user["token"])).status_code == 401

    client.patch(f"/api/v1/users/{user['id']}", json={"is_active": True}, headers=ADMIN)
    assert client.get("/api/v1/auth/me", headers=_bearer(user["token"])).status_code == 200


def test_token_rotation(client: TestClient) -> None:
    user = _create(client)
    rotated = client.post(f"/api/v1/users/{user['id']}/token", headers=ADMIN).json()
    assert rotated["token"] != user["token"]
    assert client.get("/api/v1/auth/me", headers=_bearer(user["token"])).status_code == 401
    assert client.get("/api/v1/auth/me", headers=_bearer(rotated["token"])).status_code == 200


def test_delete(client: TestClient) -> None:
    user = _create(client)
    assert client.delete(f"/api/v1/users/{user['id']}", headers=ADMIN).status_code == 204
    assert client.get("/api/v1/auth/me", headers=_bearer(user["token"])).status_code == 401
    assert client.get(f"/api/v1/users/{user['id']}", headers=ADMIN).status_code == 404


def test_admin_cannot_lock_themselves_out(client: TestClient) -> None:
    admin = _create(client, "boss", "admin")
    own = _bearer(admin["token"])
    url = f"/api/v1/users/{admin['id']}"

    assert client.patch(url, json={"role": "viewer"}, headers=own).status_code == 409
    assert client.patch(url, json={"is_active": False}, headers=own).status_code == 409
    assert client.delete(url, headers=own).status_code == 409
    # Rotating one's own token is allowed.
    assert client.post(f"{url}/token", headers=own).status_code == 200


def test_duplicate_and_reserved_usernames(client: TestClient) -> None:
    _create(client, "duty")
    duplicate = client.post("/api/v1/users", json={"username": "duty"}, headers=ADMIN)
    assert duplicate.status_code == 409
    # "admin-user" belongs to a bootstrap token from the settings.
    reserved = client.post("/api/v1/users", json={"username": "admin-user"}, headers=ADMIN)
    assert reserved.status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"username": "has space"},
        {"username": ""},
        {"username": "x", "role": "root"},
        {"username": "x", "password": "nope"},
    ],
)
def test_create_validation(client: TestClient, body: dict[str, object]) -> None:
    assert client.post("/api/v1/users", json=body, headers=ADMIN).status_code == 422


def test_update_validation(client: TestClient) -> None:
    user = _create(client)
    url = f"/api/v1/users/{user['id']}"
    assert client.patch(url, json={"role": None}, headers=ADMIN).status_code == 422
    assert client.patch(url, json={"username": "renamed"}, headers=ADMIN).status_code == 422
