from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, true
from sqlalchemy.orm import Mapped, mapped_column

from netops.db import Base, enum_type, utcnow
from netops.enums import UserRole


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    role: Mapped[UserRole] = mapped_column(enum_type(UserRole))
    token_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
    allowed_system: Mapped[str | None] = mapped_column(String(128), nullable=True)
    bound_client_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    bound_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bound_user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bound_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    telegram_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    telegram_verified_at: Mapped[datetime | None] = mapped_column(nullable=True)
    verification_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    verification_expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r} role={self.role}>"


