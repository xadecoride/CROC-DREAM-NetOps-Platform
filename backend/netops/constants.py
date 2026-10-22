"""Загрузчик единого файла констант config/constants.yaml.

Значений по умолчанию в коде нет: если файл или ключ отсутствуют, платформа падает сразу
с понятной ошибкой, а не работает на зашитых числах.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_ENV = "NETOPS_CONSTANTS_PATH"
_CANDIDATES = (
    Path("/data/config/constants.yaml"),
    Path(__file__).resolve().parents[2] / "config" / "constants.yaml",
    Path(__file__).resolve().parents[1] / "config" / "constants.yaml",
)


class ConstantsError(RuntimeError):
    pass


def constants_path() -> Path:
    explicit = os.environ.get(_ENV)
    paths = [Path(explicit)] if explicit else list(_CANDIDATES)
    for path in paths:
        if path.is_file():
            return path
    raise ConstantsError(
        f"constants.yaml не найден (искали: {', '.join(map(str, paths))}). "
        f"Задайте {_ENV} или смонтируйте config/ в /data/config."
    )


@lru_cache
def load_constants() -> dict[str, Any]:
    data = yaml.safe_load(constants_path().read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConstantsError("constants.yaml должен быть YAML-словарём")
    return data


def const(dotted: str) -> Any:
    """Значение по пути вида `network.commit_confirm_timeout_seconds`."""
    node: Any = load_constants()
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ConstantsError(f"В constants.yaml нет ключа '{dotted}'")
        node = node[part]
    return node


def console_ports() -> list[int]:
    """Все порты консолей: owner + ролевые."""
    return [int(const("console.owner_port")), *map(int, const("console.role_ports").values())]


def role_locked_ports() -> frozenset[str]:
    """Порты (внешние и внутренние nginx), на которых вход Владельца запрещён."""
    return frozenset(
        str(p) for p in (*const("console.role_ports").values(), *const("console.nginx_role_ports"))
    )


def default_username() -> str:
    return str(const("discovery.default_username"))


def fallback_password() -> str:
    return str(const("discovery.fallback_password"))


def unset_password_marker() -> str:
    return str(const("discovery.unset_password_marker"))
