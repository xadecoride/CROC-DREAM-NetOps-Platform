from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import select

from netops.api.deps import Admin, ContainerDep, SessionDep, UserServiceDep, Viewer
from netops.api.routes.auth import (
    StaticViewerResponse,
    StaticViewerToggleRequest,
    get_static_viewer,
    rotate_static_viewer,
    toggle_static_viewer,
)
from netops.models import User
from netops.schemas.users import (
    UserBatchCreate,
    UserCreate,
    UserRead,
    UserUpdate,
    UserWithToken,
)

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", summary="Текущий пользователь и статус привязки устройства")
def get_current_user_profile(user: Viewer, session: SessionDep) -> dict[str, Any]:
    db_user = session.scalar(select(User).where(User.username == user.username))
    if db_user is not None:
        return UserRead.model_validate(db_user).model_dump(mode="json")
    return {
        "id": None,
        "username": user.username,
        "role": user.role.value,
        "is_active": True,
        "allowed_system": user.allowed_system,
        "bound_client_id": None,
        "bound_ip": None,
        "bound_user_agent": None,
        "bound_at": None,
        "last_seen_at": None,
        "created_at": None,
        "updated_at": None,
    }


@router.get("", response_model=list[UserRead], summary="Список пользователей")
def list_users(service: UserServiceDep, _: Admin) -> list[User]:
    return list(service.list_users())


@router.post(
    "",
    response_model=UserWithToken,
    status_code=status.HTTP_201_CREATED,
    summary="Создать пользователя и выдать токен",
)
def create_user(data: UserCreate, service: UserServiceDep, _: Admin) -> UserWithToken:
    return _with_token(*service.create(data))


@router.post(
    "/batch",
    response_model=list[UserWithToken],
    status_code=status.HTTP_201_CREATED,
    summary="Выпустить пакет индивидуальных ссылок-токенов (для жюри, операторов или админов)",
)
def create_users_batch(
    data: UserBatchCreate, service: UserServiceDep, _: Admin
) -> list[UserWithToken]:
    return [_with_token(u, t) for u, t in service.create_batch(data)]


@router.get("/static-viewer", response_model=StaticViewerResponse, include_in_schema=False)
def get_users_static_viewer(
    request: Request, session: SessionDep, container: ContainerDep, admin: Admin
) -> dict[str, Any]:
    return get_static_viewer(request, session, container, admin)


@router.patch("/static-viewer", response_model=StaticViewerResponse, include_in_schema=False)
@router.post("/static-viewer/toggle", response_model=StaticViewerResponse, include_in_schema=False)
def toggle_users_static_viewer(
    req: StaticViewerToggleRequest,
    request: Request,
    session: SessionDep,
    container: ContainerDep,
    admin: Admin,
) -> dict[str, Any]:
    return toggle_static_viewer(req, request, session, container, admin)


@router.post("/static-viewer/rotate", response_model=StaticViewerResponse, include_in_schema=False)
def rotate_users_static_viewer(
    request: Request, session: SessionDep, container: ContainerDep, admin: Admin
) -> dict[str, Any]:
    return rotate_static_viewer(request, session, container, admin)


@router.get("/{user_id}", response_model=UserRead, summary="Пользователь")
def get_user(user_id: int, service: UserServiceDep, _: Admin) -> User:
    return service.get(user_id)


@router.patch(
    "/{user_id}", response_model=UserRead, summary="Сменить роль, систему или заблокировать пользователя"
)
def update_user(user_id: int, data: UserUpdate, service: UserServiceDep, admin: Admin) -> User:
    return service.update(user_id, data, acting_user=admin.username)


@router.post(
    "/{user_id}/token",
    response_model=UserWithToken,
    summary="Выпустить новый токен, старый перестаёт работать и привязка устройства сбрасывается",
)
@router.post(
    "/{user_id}/rotate",
    response_model=UserWithToken,
    include_in_schema=False,
)
def rotate_token(user_id: int, service: UserServiceDep, _: Admin) -> UserWithToken:
    return _with_token(*service.rotate_token(user_id))


@router.post(
    "/{user_id}/unbind",
    response_model=UserRead,
    summary="Сбросить привязку токена к устройству (разрешить вход с нового устройства)",
)
def unbind_user_device(user_id: int, service: UserServiceDep, _: Admin) -> User:
    return service.unbind_device(user_id)


@router.post(
    "/{user_id}/verify-code",
    response_model=UserRead,
    summary="Сгенерировать 6-значный код верификации для привязки Telegram-бота к участнику и его системе",
)
def generate_user_verify_code(user_id: int, service: UserServiceDep, _: Admin) -> User:
    return service.generate_verification_code(user_id)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Удалить пользователя")
def delete_user(user_id: int, service: UserServiceDep, admin: Admin) -> Response:
    service.delete(user_id, acting_user=admin.username)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _with_token(user: User, token: str) -> UserWithToken:
    return UserWithToken(**UserRead.model_validate(user).model_dump(), token=token)

