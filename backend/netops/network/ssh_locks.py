"""SSH locking primitives: per-jump-host locks and per-device local+Redis distributed lock."""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
from collections.abc import Iterator

from netops.constants import const

logger = logging.getLogger(__name__)

_LOCKS_GUARD = threading.Lock()
_JUMP_LOCKS: dict[tuple[str, int, str], threading.Lock] = {}
_DEVICE_LOCKS: dict[tuple[str, int], _DistributedDeviceLock] = {}
_WATCHED: dict[int, _DistributedDeviceLock] = {}
_WATCHDOG_STATE: dict[str, threading.Thread | None] = {"thread": None}


class SSHLockUnavailableError(RuntimeError):
    """Distributed SSH lock was not obtained before the deadline for a mutating operation."""


def get_jump_lock(jump_key: tuple[str, int, str]) -> threading.Lock:
    """Per-jump-host lock; the global guard is held only for the dict lookup."""
    with _LOCKS_GUARD:
        lock = _JUMP_LOCKS.get(jump_key)
        if lock is None:
            lock = _JUMP_LOCKS[jump_key] = threading.Lock()
        return lock


def _redis_client_and_key(host: str, port: int) -> tuple[object | None, str]:
    from netops.services.state import get_state_manager

    mgr = get_state_manager()
    client = mgr._get_redis() if mgr.is_redis_connected else None
    return client, f"netops:ssh_lock:{host}:{port}"


def _watchdog_loop() -> None:
    while True:
        time.sleep(max(1.0, float(const("network.ssh_lock_ttl_seconds")) / 3.0))
        with _LOCKS_GUARD:
            held = list(_WATCHED.values())
        for lock in held:
            lock.refresh()


def _watch(lock: _DistributedDeviceLock, add: bool) -> None:
    with _LOCKS_GUARD:
        if add:
            _WATCHED[id(lock)] = lock
            if _WATCHDOG_STATE["thread"] is None:
                t = threading.Thread(target=_watchdog_loop, name="ssh-lock-watchdog", daemon=True)
                _WATCHDOG_STATE["thread"] = t
                t.start()
        else:
            _WATCHED.pop(id(lock), None)


class _DistributedDeviceLock:
    """Thread-local RLock combined with cross-process Redis SSH lock (netops:ssh_lock:{host}:{port}).

    The Redis TTL is refreshed by a shared watchdog while held. Redis unavailable -> local RLock only.
    If the Redis lock is not obtained before the deadline: warn; mutating callers fail closed.
    """

    def __init__(self, host: str, port: int, ttl: int | None = None) -> None:
        self.host, self.port = host, port
        self.ttl = int(ttl if ttl is not None else const("network.ssh_lock_ttl_seconds"))
        self._rlock = threading.RLock()
        self._depth = 0
        self._holder: str | None = None

    def acquire(
        self, blocking: bool = True, timeout: float = -1, fail_closed: bool = False
    ) -> bool:
        if not self._rlock.acquire(blocking=blocking, timeout=timeout):
            return False
        self._depth += 1
        if self._depth == 1 and "PYTEST_CURRENT_TEST" not in os.environ:
            try:
                self._acquire_distributed(fail_closed)
            except BaseException:
                self._depth -= 1
                self._rlock.release()
                raise
        return True

    def _acquire_distributed(self, fail_closed: bool) -> None:
        try:
            from netops.services.state import get_state_manager

            mgr = get_state_manager()
            if not mgr.is_redis_connected:
                return
            holder = f"{os.getpid()}:{threading.get_ident()}"
            deadline = time.monotonic() + float(const("network.ssh_lock_acquire_deadline_seconds"))
            while time.monotonic() < deadline:
                if mgr.acquire_ssh_lock(self.host, self.port, holder, ttl=self.ttl):
                    self._holder = holder
                    _watch(self, True)
                    return
                time.sleep(0.05)
        except Exception as exc:
            logger.warning("SSH lock backend error for %s:%s: %s", self.host, self.port, exc)
            return
        logger.warning(
            "SSH distributed lock %s:%s not acquired before deadline (fail_%s)",
            self.host,
            self.port,
            "closed" if fail_closed else "open",
        )
        if fail_closed:
            raise SSHLockUnavailableError(f"SSH lock busy for {self.host}:{self.port}")

    def refresh(self) -> None:
        holder = self._holder
        if holder is None:
            return
        try:
            client, key = _redis_client_and_key(self.host, self.port)
            if client is None:
                return
            if client.get(key) == holder:  # type: ignore[attr-defined]
                client.expire(key, self.ttl)  # type: ignore[attr-defined]
            else:
                logger.warning("SSH lock %s lost while held (expired or stolen)", key)
                _watch(self, False)
        except Exception as exc:
            logger.warning("SSH lock refresh failed for %s:%s: %s", self.host, self.port, exc)

    @contextlib.contextmanager
    def try_exclusive(self) -> Iterator[bool]:
        """Non-blocking local ownership (used by the pool reaper to skip busy devices)."""
        got = self._rlock.acquire(blocking=False)
        try:
            yield got
        finally:
            if got:
                self._rlock.release()

    def release(self) -> None:
        if self._depth == 1 and self._holder is not None:
            holder, self._holder = self._holder, None
            _watch(self, False)
            with contextlib.suppress(Exception):
                from netops.services.state import get_state_manager

                get_state_manager().release_ssh_lock(self.host, self.port, holder)
        self._depth = max(0, self._depth - 1)
        self._rlock.release()

    def __enter__(self) -> _DistributedDeviceLock:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def _get_device_lock(host: str, port: int) -> _DistributedDeviceLock:
    key = (host, port)
    with _LOCKS_GUARD:
        lock = _DEVICE_LOCKS.get(key)
        if lock is None:
            lock = _DEVICE_LOCKS[key] = _DistributedDeviceLock(host, port)
        return lock
