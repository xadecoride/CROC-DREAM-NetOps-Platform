from __future__ import annotations

from netops.services.notifier import send_telegram_message
from netops.settings import Settings


def test_telegram_unconfigured_returns_false() -> None:
    settings = Settings(telegram_bot_token=None, telegram_chat_id=None)
    assert send_telegram_message(settings, "Test") is False
