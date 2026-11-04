"""Смена Telegram-аккаунта Владельца.

Владелец привязывается к первому chat_id, написавшему боту; другой аккаунт принять нельзя,
пока привязка не снята. Эндпоинт снимает привязку (только Owner) — после этого /start
у бота от нового аккаунта закрепит за ним роль Владельца.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from netops.api.deps import ContainerDep, Owner, SessionDep
from netops.models import User

router = APIRouter(tags=["system"])


@router.post("/system/owner/telegram/unlink", summary="Снять Telegram-привязку Владельца (Owner)")
def unlink_owner_telegram(
    session: SessionDep, container: ContainerDep, _principal: Owner
) -> dict[str, object]:
    owner = session.scalar(select(User).where(User.username == "owner"))
    if owner is not None:
        owner.telegram_chat_id = None
        owner.telegram_username = None
        owner.telegram_verified_at = None
        session.commit()
    container.settings.save_runtime_overlay(
        {
            "telegram_owner_linked": False,
            "telegram_chat_id": "",
            "telegram_verified_username": "",
        }
    )
    return {"unlinked": True}
