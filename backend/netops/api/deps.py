from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session, sessionmaker

from netops.constants import role_locked_ports
from netops.enums import UserRole
from netops.intent import IntentRepository
from netops.services import DeviceService, JobDispatcher, JobService, UserService
from netops.settings import ApiPrincipal, Settings


@dataclass(frozen=True)
class AppContainer:
    settings: Settings
    session_factory: sessionmaker[Session]
    dispatcher: JobDispatcher
    intents: IntentRepository


def get_container(request: Request) -> AppContainer:
    container: AppContainer = request.app.state.container
    return container


ContainerDep = Annotated[AppContainer, Depends(get_container)]


def get_session(container: ContainerDep) -> Iterator[Session]:
    with container.session_factory() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]

_bearer = HTTPBearer(auto_error=False, description="Токен API, выданный администратором")


def get_user_service(session: SessionDep, container: ContainerDep) -> UserService:
    return UserService(
        session,
        reserved_usernames={p.username for p in container.settings.api_tokens.values()},
    )


UserServiceDep = Annotated[UserService, Depends(get_user_service)]


def get_principal(
    request: Request,
    container: ContainerDep,
    users: UserServiceDep,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)],
) -> ApiPrincipal:
    from netops.services.sessions import csrf_ok, principal_from_session
    from netops.services.users import DeviceBoundConflictError

    # 1) Browser: opaque HttpOnly session cookie (+ CSRF header on mutating requests).
    #    An explicit Authorization header wins: it is CSRF-immune (never auto-attached).
    if credentials is None:
        principal, record = principal_from_session(request, session, container.settings)
        if principal is not None and record is not None:
            console_port = (request.headers.get("x-console-port") or "").strip()
            if principal.role == UserRole.OWNER and console_port in role_locked_ports():
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Owner session is not valid on a role-locked console port",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            if not csrf_ok(request, record):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="Missing or invalid CSRF token"
                )
            return principal

    # 2) Machine clients (MCP, bot, CI, smoke): Authorization: Bearer <api key>.
    principal = None
    if credentials is not None:
        token = credentials.credentials
        principal = container.settings.authenticate(token)
        if principal is None:
            try:
                principal = users.authenticate(
                    token,
                    client_id=request.headers.get("X-Client-ID"),
                    user_agent=request.headers.get("User-Agent"),
                    # uvicorn already resolves X-Forwarded-For for trusted proxies only.
                    client_ip=request.client.host if request.client else None,
                    settings=container.settings,
                )
            except DeviceBoundConflictError as exc:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=str(exc),
                ) from exc
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def require_role(role: UserRole) -> Callable[[ApiPrincipal], ApiPrincipal]:
    def dependency(principal: Annotated[ApiPrincipal, Depends(get_principal)]) -> ApiPrincipal:
        if not principal.role.grants(role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"The {role} role is required",
            )
        return principal

    return dependency


Viewer = Annotated[ApiPrincipal, Depends(require_role(UserRole.VIEWER))]
Operator = Annotated[ApiPrincipal, Depends(require_role(UserRole.OPERATOR))]
Admin = Annotated[ApiPrincipal, Depends(require_role(UserRole.ADMIN))]
Owner = Annotated[ApiPrincipal, Depends(require_role(UserRole.OWNER))]


def get_device_service(session: SessionDep) -> DeviceService:
    return DeviceService(session)


def get_job_service(session: SessionDep, container: ContainerDep) -> JobService:
    return JobService(session, container.dispatcher)


DeviceServiceDep = Annotated[DeviceService, Depends(get_device_service)]
JobServiceDep = Annotated[JobService, Depends(get_job_service)]
