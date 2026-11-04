from __future__ import annotations

import logging
import os
import time
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)


def build_engine(database_url: str, **kwargs: Any) -> Engine:
    if database_url.startswith("sqlite"):
        kwargs.setdefault("connect_args", {"check_same_thread": False})
        engine = create_engine(database_url, **kwargs)
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
        return engine
    # Пул на процесс; общий лимит = процессы * (pool_size + max_overflow) < max_connections PG.
    kwargs.setdefault("pool_size", int(os.environ.get("NETOPS_DB_POOL_SIZE", "10")))
    kwargs.setdefault("max_overflow", int(os.environ.get("NETOPS_DB_MAX_OVERFLOW", "10")))
    kwargs.setdefault("pool_recycle", 1800)
    kwargs.setdefault("pool_timeout", 30)
    return create_engine(database_url, pool_pre_ping=True, **kwargs)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    # Пайплайн коммитит после каждого шага, объекты должны оставаться доступными.
    return sessionmaker(bind=engine, expire_on_commit=False)


def wait_for_database(engine: Engine, *, attempts: int = 30, delay: float = 2.0) -> None:
    for attempt in range(1, attempts + 1):
        try:
            with engine.connect():
                return
        except OperationalError:
            if attempt == attempts:
                raise
            logger.warning(
                "База недоступна, попытка %d/%d, повтор через %gс", attempt, attempts, delay
            )
            time.sleep(delay)


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
