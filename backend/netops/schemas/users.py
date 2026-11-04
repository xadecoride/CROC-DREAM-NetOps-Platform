from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from netops.enums import UserRole
from netops.schemas.common import PartialUpdate

Username = Annotated[
    str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$")
]


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Username = Field(examples=["duty-engineer"])
    role: UserRole = UserRole.VIEWER
    allowed_system: str | None = Field(default=None, max_length=128)
    expires_in_minutes: int | None = Field(default=None, ge=0, le=525600)


class UserBatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefix: Username = Field(default="jury", examples=["jury", "admin", "operator"])
    role: UserRole = UserRole.VIEWER
    count: int = Field(default=3, ge=1, le=20)
    allowed_system: str | None = Field(default=None, max_length=128)
    expires_in_minutes: int | None = Field(default=None, ge=0, le=525600)


class UserUpdate(PartialUpdate):
    _nullable_fields = frozenset({"allowed_system", "expires_in_minutes"})

    role: UserRole | None = None
    is_active: bool | None = None
    allowed_system: str | None = Field(default=None, max_length=128)
    expires_in_minutes: int | None = Field(default=None, ge=0, le=525600)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: UserRole
    is_active: bool
    allowed_system: str | None = None
    bound_client_id: str | None = None
    bound_ip: str | None = None
    bound_user_agent: str | None = None
    bound_at: datetime | None = None
    last_seen_at: datetime | None = None
    expires_at: datetime | None = None
    telegram_chat_id: str | None = None
    telegram_username: str | None = None
    telegram_verified_at: datetime | None = None
    verification_code: str | None = None
    verification_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class UserWithToken(UserRead):
    token: str = Field(description="Токен показывается один раз, сохраните его")


