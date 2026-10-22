"""Публичная часть констант для SPA: порты консолей нужны до входа в систему."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from netops.constants import const

router = APIRouter(prefix="/config", tags=["config"])


@router.get("/console", summary="Порты консолей и роли (публично, без секретов)")
def console_config() -> dict[str, Any]:
    return {
        "owner_port": int(const("console.owner_port")),
        "role_ports": {role: int(port) for role, port in const("console.role_ports").items()},
        "loopback_hosts": list(const("console.loopback_hosts")),
        "local_owner_login_token": str(const("console.local_owner_login_token")),
    }
