from __future__ import annotations

from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from netops.db import Base, utcnow


class Stand(Base):
    __tablename__ = "stands"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    site: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    driver: Mapped[str] = mapped_column(String(32), default="scrapli", server_default="scrapli")
    jump_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    jump_port: Mapped[int] = mapped_column(default=22, server_default="22")
    jump_user: Mapped[str | None] = mapped_column(String(64), nullable=True)
    encrypted_password: Mapped[str | None] = mapped_column(String(512), nullable=True)
    port_map_json: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    topology_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    intent_subpath: Mapped[str | None] = mapped_column(String(255), nullable=True)
    queue_name: Mapped[str] = mapped_column(String(64), default="default", server_default="default")
    status: Mapped[str] = mapped_column(String(32), default="ONLINE", server_default="ONLINE")
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    def __repr__(self) -> str:
        return f"<Stand id={self.id} slug={self.slug!r} status={self.status!r}>"
