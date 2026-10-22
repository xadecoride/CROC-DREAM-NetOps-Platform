"""Unified Redis state manager with seamless thread-safe in-memory fallback.

Provides shared hot state (live telemetry time-series, Git SoT cache, rendered template cache,
and Factory Freeze) as well as distributed sync locks (two-phase rollback protection and
emergency patch safety) across API replicas and background workers.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from netops.services.state_domains import StateDomainsMixin
from netops.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class _ManagerHolder:
    instance: StateManager | None = None
    lock = threading.Lock()


class StateManager(StateDomainsMixin):
    """Singleton state manager providing Redis connectivity with circuit breaker.

    When Redis is offline or unreachable, operations fall back to an internal
    thread-safe in-memory store with zero latency overhead.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        redis_url: str | None = None,
        enable_redis: bool | None = None,
        probe_cooldown_seconds: float = 30.0,
        socket_timeout: float = 0.2,
    ) -> None:
        import os

        self._settings = settings or get_settings()
        self._redis_url = redis_url if redis_url is not None else self._settings.redis_url
        if enable_redis is None:
            self._enable_redis = not ("PYTEST_CURRENT_TEST" in os.environ and redis_url is None)
        else:
            self._enable_redis = enable_redis
        self._probe_cooldown_seconds = probe_cooldown_seconds
        self._socket_timeout = socket_timeout

        self._client: Any = None
        self._is_redis_online: bool | None = None
        self._last_probe_time: float = 0.0

        # Thread-safe in-memory stores
        self._lock = threading.RLock()
        self._local_kv: dict[str, tuple[float | None, str]] = {}
        self._local_locks: dict[str, tuple[float, dict[str, Any]]] = {}
        self._local_telemetry: dict[str, dict[str, list[float]]] = {}
        self._local_git_sot: dict[str, tuple[float, dict[str, Any]]] = {}
        self._local_templates: dict[str, tuple[float, str]] = {}
        self._local_freeze: dict[str, Any] = {
            "frozen": False,
            "reason": "Штатная эксплуатация (заморозка выключена)",
            "timestamp": None,
            "user": None,
            "updated_by": None,
        }
        self._local_fabric_freeze: dict[str, dict[str, Any]] = {}

    def _get_redis(self) -> Any:
        """Return connected Redis client, or None if disabled, unreachable, or in cooldown."""
        if not self._enable_redis or not self._redis_url:
            return None

        now = time.monotonic()
        # Fast-fail circuit breaker: skip network attempt if in probe cooldown window
        cooldown_active = (now - self._last_probe_time) < self._probe_cooldown_seconds
        if self._is_redis_online is False and cooldown_active:
            return None

        if self._client is not None and self._is_redis_online:
            return self._client

        try:
            import redis

            client = redis.Redis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_timeout=self._socket_timeout,
                socket_connect_timeout=self._socket_timeout,
            )
            client.ping()
            self._client = client
            self._is_redis_online = True
            logger.info("Connected to Redis shared state store at %s", self._redis_url)
            return self._client
        except Exception as exc:
            self._is_redis_online = False
            self._last_probe_time = now
            self._client = None
            logger.debug(
                "Redis unavailable (%s); using thread-safe in-memory state store (cooldown %ss)",
                exc,
                self._probe_cooldown_seconds,
            )
            return None

    def _handle_redis_error(self, exc: Exception) -> None:
        self._is_redis_online = False
        self._last_probe_time = time.monotonic()
        self._client = None
        logger.debug("Redis operation failed (%s); failing over to in-memory state", exc)

    @property
    def is_redis_connected(self) -> bool:
        """Check if currently connected to a live Redis instance."""
        return bool(self._get_redis() is not None)

    # -------------------------------------------------------------------------
    # Primitive Key-Value Operations with TTL
    # -------------------------------------------------------------------------

    def get(self, key: str) -> str | None:
        client = self._get_redis()
        if client:
            try:
                return client.get(key)
            except Exception as exc:
                self._handle_redis_error(exc)

        with self._lock:
            entry = self._local_kv.get(key)
            if entry is None:
                return None
            expires_at, val = entry
            if expires_at is not None and time.time() > expires_at:
                self._local_kv.pop(key, None)
                return None
            return val

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        client = self._get_redis()
        if client:
            try:
                return bool(client.set(key, value, ex=ex))
            except Exception as exc:
                self._handle_redis_error(exc)

        with self._lock:
            expires_at = (time.time() + ex) if ex else None
            self._local_kv[key] = (expires_at, value)
            return True

    def delete(self, key: str) -> bool:
        client = self._get_redis()
        if client:
            try:
                return bool(client.delete(key))
            except Exception as exc:
                self._handle_redis_error(exc)

        with self._lock:
            return bool(self._local_kv.pop(key, None))

    def get_json(self, key: str) -> Any | None:
        raw = self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def set_json(self, key: str, value: Any, ex: int | None = None) -> bool:
        return self.set(key, json.dumps(value, ensure_ascii=False), ex=ex)

    # Hot ephemeral keys stored exclusively in Redis (never written to heavy PostgreSQL).
    # SD-цели (prometheus_*_targets) сюда не входят: пишутся и в БД, при Redis-miss читаются из БД.
    _HOT_REDIS_KEYS: frozenset[str] = frozenset(
        {
            "chaos_events",
            "grafana_noc_dashboard",
        }
    )
    _NULL_SENTINEL = "__netops_db_null__"

    def _get_db_engine(self, database_url: str | None = None) -> Any | None:
        import os

        db_url = database_url or getattr(self._settings, "database_url", "") or ""
        if not db_url:
            return None
        if (
            "PYTEST_CURRENT_TEST" in os.environ
            and db_url.startswith("postgresql")
            and "localhost" in db_url
        ):
            return None
        eng_cache: dict[str, Any] = getattr(self, "_db_engines", {})
        if db_url not in eng_cache:
            try:
                import contextlib

                from netops.db import build_engine
                from netops.models.system_state import SystemState

                eng = build_engine(db_url, pool_size=2, max_overflow=3)
                with contextlib.suppress(Exception):
                    SystemState.__table__.create(bind=eng, checkfirst=True)
                eng_cache[db_url] = eng
                self._db_engines = eng_cache
            except Exception:
                return None
        return eng_cache.get(db_url)

    def get_db_state(
        self, key: str, default: Any = None, *, database_url: str | None = None
    ) -> Any:
        cache_key = f"netops:db_state:{key}"
        cached = self.get_json(cache_key)
        if cached is not None:
            if isinstance(cached, dict) and cached.get(self._NULL_SENTINEL) is True:
                return default
            return cached
        engine = self._get_db_engine(database_url)
        if engine is not None:
            try:
                from sqlalchemy.orm import Session

                from netops.models.system_state import SystemState

                with Session(engine) as sess:
                    row = sess.get(SystemState, key)
                    if row is not None and row.value is not None:
                        self.set_json(cache_key, row.value, ex=600)
                        return row.value
                    if key != "runtime_settings":
                        self.set_json(cache_key, {self._NULL_SENTINEL: True}, ex=60)
            except Exception:
                _ = None
        return default

    def set_db_state(
        self, key: str, value: Any, *, database_url: str | None = None
    ) -> bool:
        cache_key = f"netops:db_state:{key}"
        ttl = 86400 if key in self._HOT_REDIS_KEYS else 3600
        self.set_json(cache_key, value, ex=ttl)
        if key in self._HOT_REDIS_KEYS and self._get_redis() is not None:
            return True
        engine = self._get_db_engine(database_url)
        if engine is not None:
            try:
                from sqlalchemy.orm import Session

                from netops.db import utcnow
                from netops.models.system_state import SystemState

                with Session(engine) as sess:
                    sess.merge(SystemState(key=key, value=value, updated_at=utcnow()))
                    sess.commit()
            except Exception as exc:
                logger.debug("Failed to persist SystemState[%s] to DB: %s", key, exc)
        return True

    def reset(self) -> None:
        """Reset all in-memory local state (useful for test isolation)."""
        with self._lock:
            self._local_kv.clear()
            self._local_locks.clear()
            self._local_telemetry.clear()
            self._local_git_sot.clear()
            self._local_templates.clear()
            self._local_freeze.update({
                "frozen": False,
                "reason": "Штатная эксплуатация (заморозка выключена)",
                "timestamp": None,
                "user": None,
                "updated_by": None,
            })
            self._local_fabric_freeze.clear()
            for eng in getattr(self, "_db_engines", {}).values():
                try:
                    eng.dispose()
                except Exception:
                    _ = None
            self._db_engines = {}


def get_state_manager(
    settings: Settings | None = None,
    force_refresh: bool = False,
    **kwargs: Any,
) -> StateManager:
    """Return the global StateManager singleton."""
    with _ManagerHolder.lock:
        if _ManagerHolder.instance is None or force_refresh:
            _ManagerHolder.instance = StateManager(settings=settings, **kwargs)
        return _ManagerHolder.instance


def reset_state_manager() -> None:
    """Reset the global singleton and its internal state."""
    with _ManagerHolder.lock:
        if _ManagerHolder.instance is not None:
            _ManagerHolder.instance.reset()
        _ManagerHolder.instance = None
