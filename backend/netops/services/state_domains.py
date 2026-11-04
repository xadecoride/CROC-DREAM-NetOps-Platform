"""Domain-specific state methods for StateManager (sync locks, telemetry, Git SoT, templates, freeze)."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


class StateDomainsMixin:
    """Mixin providing distributed sync locks, telemetry series, Git SoT, template, and freeze caches."""

    _lock: threading.RLock
    _local_locks: dict[str, tuple[float, dict[str, Any]]]
    _local_telemetry: dict[str, dict[str, list[float]]]
    _local_git_sot: dict[str, tuple[float, dict[str, Any]]]
    _local_templates: dict[str, tuple[float, str]]
    _local_freeze: dict[str, Any]
    _local_fabric_freeze: dict[str, dict[str, Any]]

    def _get_redis(self) -> Any:
        raise NotImplementedError

    def _handle_redis_error(self, exc: Exception) -> None:
        raise NotImplementedError

    def acquire_sync_lock(
        self, target: str, holder: str, ttl: int = 600, reason: str = ""
    ) -> bool:
        """Acquire a distributed sync lock for target (e.g. 'device:1', 'fabric:pod1')."""
        now_ts = time.time()
        payload = {
            "target": target,
            "holder": holder,
            "reason": reason or "Active deployment or rollback",
            "acquired_at": datetime.now(UTC).isoformat(),
            "expires_at": datetime.fromtimestamp(now_ts + ttl, UTC).isoformat(),
            "ttl": ttl,
        }
        raw = json.dumps(payload, ensure_ascii=False)
        key = f"netops:sync_lock:{target}"
        client = self._get_redis()
        if client:
            try:
                if bool(client.set(key, raw, nx=True, ex=ttl)):
                    with self._lock:
                        self._local_locks[target] = (now_ts + ttl, payload)
                    return True
                return False
            except Exception as exc:
                self._handle_redis_error(exc)
        with self._lock:
            existing = self._local_locks.get(target)
            if existing and existing[0] > now_ts:
                return False
            self._local_locks[target] = (now_ts + ttl, payload)
            return True

    def _release_redis_lock(self, client: Any, key: str, holder: str | None) -> bool | None:
        try:
            raw = client.get(key)
            if not raw:
                return False
            try:
                data = json.loads(raw)
                if holder is not None and data.get("holder") != holder:
                    return False
            except Exception:
                if holder is not None:
                    return False
            return bool(client.delete(key))
        except Exception as exc:
            self._handle_redis_error(exc)
            return None

    def release_sync_lock(self, target: str, holder: str | None = None) -> bool:
        """Release a distributed sync lock for target."""
        key = f"netops:sync_lock:{target}"
        client = self._get_redis()
        if client:
            redis_res = self._release_redis_lock(client, key, holder)
            if redis_res is not None:
                if redis_res:
                    with self._lock:
                        self._local_locks.pop(target, None)
                return redis_res
        with self._lock:
            existing = self._local_locks.get(target)
            if not existing:
                return False
            exp, data = existing
            if time.time() > exp:
                self._local_locks.pop(target, None)
                return False
            if holder is not None and data.get("holder") != holder:
                return False
            self._local_locks.pop(target, None)
            return True

    def get_sync_lock_info(self, target: str) -> dict[str, Any] | None:
        """Return active lock metadata dictionary for target, or None if unlocked or expired."""
        key = f"netops:sync_lock:{target}"
        client = self._get_redis()
        if client:
            try:
                raw = client.get(key)
                return json.loads(raw) if raw else None
            except Exception as exc:
                self._handle_redis_error(exc)
        with self._lock:
            existing = self._local_locks.get(target)
            if not existing:
                return None
            exp, payload = existing
            if time.time() > exp:
                self._local_locks.pop(target, None)
                return None
            return dict(payload)

    def is_sync_locked(self, target: str) -> bool:
        """Check whether target is currently locked."""
        return self.get_sync_lock_info(target) is not None

    def clear_sync_locks(self) -> int:
        """Drop all sync locks (operator unfreeze); returns number of locks removed."""
        removed = 0
        client = self._get_redis()
        if client:
            try:
                keys = list(client.scan_iter("netops:sync_lock:*"))
                if keys:
                    removed = int(client.delete(*keys))
            except Exception as exc:
                self._handle_redis_error(exc)
        with self._lock:
            removed = max(removed, len(self._local_locks))
            self._local_locks.clear()
        return removed

    def acquire_ssh_lock(self, host: str, port: int, holder: str, ttl: int = 45) -> bool:
        """Acquire a short-lived distributed Redis lock for SSH endpoint netops:ssh_lock:{host}:{port}."""
        key = f"netops:ssh_lock:{host}:{port}"
        client = self._get_redis()
        if client:
            try:
                return bool(client.set(key, holder, nx=True, ex=ttl))
            except Exception as exc:
                self._handle_redis_error(exc)
        return True

    def release_ssh_lock(self, host: str, port: int, holder: str | None = None) -> bool:
        """Release a distributed Redis lock for SSH endpoint netops:ssh_lock:{host}:{port}."""
        key = f"netops:ssh_lock:{host}:{port}"
        client = self._get_redis()
        if client:
            try:
                if holder is not None:
                    curr = client.get(key)
                    if curr is not None and curr != holder:
                        return False
                return bool(client.delete(key))
            except Exception as exc:
                self._handle_redis_error(exc)
        return True

    def append_telemetry(
        self, hostname: str, metric: str, value: float, max_points: int = 288
    ) -> None:
        """Append a metric reading to telemetry time-series."""
        is_pct = metric.endswith(("_pct", "percent", "utilization"))
        clamped = (
            round(max(0.0, min(100.0, float(value))), 2)
            if is_pct
            else round(max(0.0, float(value)), 2)
        )
        key = f"netops:telemetry:{hostname}:{metric}"
        client = self._get_redis()
        if client:
            try:
                pipe = client.pipeline()
                pipe.rpush(key, str(clamped))
                pipe.ltrim(key, -max_points, -1)
                pipe.expire(key, 86400 * 7)
                pipe.execute()
            except Exception as exc:
                self._handle_redis_error(exc)
        with self._lock:
            series = self._local_telemetry.setdefault(hostname, {}).setdefault(metric, [])
            series.append(clamped)
            if len(series) > max_points:
                series.pop(0)

    def get_telemetry_series(
        self, hostname: str, metric: str, points: int = 24
    ) -> list[float]:
        """Fetch the most recent readings for given hostname and metric."""
        key = f"netops:telemetry:{hostname}:{metric}"
        client = self._get_redis()
        if client:
            try:
                raw_items = client.lrange(key, -points, -1)
                if raw_items:
                    return [float(x) for x in raw_items]
            except Exception as exc:
                self._handle_redis_error(exc)
        with self._lock:
            return list(self._local_telemetry.get(hostname, {}).get(metric, [])[-points:])

    def set_telemetry_series(
        self, hostname: str, metric: str, series: list[float], ttl: int = 86400 * 7
    ) -> None:
        """Explicitly set time-series data for a metric."""
        is_pct = metric.endswith(("_pct", "percent", "utilization"))
        clamped_series = [
            round(max(0.0, min(100.0, float(x))), 2)
            if is_pct
            else round(max(0.0, float(x)), 2)
            for x in series
        ]
        key = f"netops:telemetry:{hostname}:{metric}"
        client = self._get_redis()
        if client:
            try:
                pipe = client.pipeline()
                pipe.delete(key)
                if clamped_series:
                    pipe.rpush(key, *[str(x) for x in clamped_series])
                pipe.expire(key, ttl)
                pipe.execute()
            except Exception as exc:
                self._handle_redis_error(exc)
        with self._lock:
            self._local_telemetry.setdefault(hostname, {})[metric] = list(clamped_series)

    def clear_telemetry(self, hostname: str | None = None) -> None:
        """Clear telemetry cache for a single host or all hosts."""
        client = self._get_redis()
        if client:
            try:
                patterns = (
                    [f"netops:telemetry:{hostname}:*", f"netops:telemetry:host:{hostname}"]
                    if hostname
                    else ["netops:telemetry:*"]
                )
                keys = [k for pat in patterns for k in client.scan_iter(pat)]
                if keys:
                    client.delete(*keys)
            except Exception as exc:
                self._handle_redis_error(exc)
        with self._lock:
            local_kv = getattr(self, "_local_kv", None)
            if hostname is None:
                self._local_telemetry.clear()
                if isinstance(local_kv, dict):
                    for k in [
                        k
                        for k in local_kv
                        if k.startswith(("netops:telemetry:host:", "netops:telemetry:fabric:"))
                    ]:
                        local_kv.pop(k, None)
            else:
                self._local_telemetry.pop(hostname, None)
                if isinstance(local_kv, dict):
                    local_kv.pop(f"netops:telemetry:host:{hostname}", None)

    def get_git_sot_meta(self, cache_key: str) -> dict[str, Any] | None:
        """Get cached Git SoT repository metadata if still valid."""
        client = self._get_redis()
        if client:
            try:
                raw = client.get(f"netops:git_sot:{cache_key}")
                if raw:
                    return json.loads(raw)
            except Exception as exc:
                self._handle_redis_error(exc)
        with self._lock:
            entry = self._local_git_sot.get(cache_key)
            if not entry:
                return None
            expires_at, val = entry
            if time.time() > expires_at:
                self._local_git_sot.pop(cache_key, None)
                return None
            return dict(val)

    def set_git_sot_meta(self, cache_key: str, data: dict[str, Any], ttl: int = 10) -> None:
        """Cache Git SoT repository metadata with TTL."""
        client = self._get_redis()
        if client:
            try:
                client.set(f"netops:git_sot:{cache_key}", json.dumps(data, ensure_ascii=False), ex=ttl)
            except Exception as exc:
                self._handle_redis_error(exc)
        with self._lock:
            self._local_git_sot[cache_key] = (time.time() + ttl, dict(data))

    def clear_git_sot_meta(self) -> None:
        """Invalidate all Git SoT cache entries."""
        client = self._get_redis()
        if client:
            try:
                keys = list(client.scan_iter("netops:git_sot:*"))
                if keys:
                    client.delete(*keys)
            except Exception as exc:
                self._handle_redis_error(exc)
        with self._lock:
            self._local_git_sot.clear()

    def get_rendered_template(self, key: str) -> str | None:
        """Get cached rendered configuration template if present."""
        client = self._get_redis()
        if client:
            try:
                return client.get(f"netops:template:{key}")
            except Exception as exc:
                self._handle_redis_error(exc)
        with self._lock:
            entry = self._local_templates.get(key)
            if not entry:
                return None
            expires_at, val = entry
            if time.time() > expires_at:
                self._local_templates.pop(key, None)
                return None
            return val

    def set_rendered_template(self, key: str, content: str, ttl: int = 300) -> None:
        """Cache rendered configuration template with TTL."""
        client = self._get_redis()
        if client:
            try:
                client.set(f"netops:template:{key}", content, ex=ttl)
            except Exception as exc:
                self._handle_redis_error(exc)
        with self._lock:
            self._local_templates[key] = (time.time() + ttl, content)

    def _get_global_factory_freeze(self, client: Any) -> dict[str, Any]:
        state = dict(self._local_freeze)
        if client:
            try:
                raw = client.get("netops:factory_freeze")
                if raw:
                    state = json.loads(raw)
            except Exception as exc:
                self._handle_redis_error(exc)
        frozen_fabrics: dict[str, Any] = {}
        if client:
            try:
                prefix = "netops:factory_freeze:fabric:"
                for k in client.scan_iter(f"{prefix}*"):
                    fab_id = k.removeprefix(prefix)
                    fab_raw = client.get(k)
                    if fab_raw:
                        val = json.loads(fab_raw)
                        if val.get("frozen"):
                            frozen_fabrics[fab_id] = val
            except Exception as exc:
                self._handle_redis_error(exc)
        with self._lock:
            for fab_id, val in self._local_fabric_freeze.items():
                if val.get("frozen") and fab_id not in frozen_fabrics:
                    frozen_fabrics[fab_id] = val
        state["fabrics"] = frozen_fabrics
        return state

    def get_factory_freeze(self, fabric_id: str | None = None) -> dict[str, Any]:
        """Fetch Factory Freeze state for a specific stand or global fabric."""
        client = self._get_redis()
        if fabric_id:
            if client:
                try:
                    raw = client.get(f"netops:factory_freeze:fabric:{fabric_id}")
                    if raw:
                        return json.loads(raw)
                except Exception as exc:
                    self._handle_redis_error(exc)
            with self._lock:
                return dict(
                    self._local_fabric_freeze.get(
                        fabric_id,
                        {
                            "frozen": False,
                            "reason": f"Штатная эксплуатация стенда {fabric_id}",
                            "fabric_id": fabric_id,
                            "timestamp": None,
                            "user": None,
                            "updated_by": None,
                        },
                    )
                )
        return self._get_global_factory_freeze(client)

    def set_factory_freeze(
        self, frozen: bool, reason: str = "", user: str = "operator", fabric_id: str | None = None
    ) -> dict[str, Any]:
        """Set Factory Freeze state globally or for a specific stand fabric_id."""
        actor = user or "operator"
        timestamp = datetime.now(UTC).isoformat()
        if fabric_id:
            default_reason = (
                f"Заморозка стенда {fabric_id} активирована оператором"
                if frozen
                else f"Штатная эксплуатация стенда {fabric_id}"
            )
            state = {
                "frozen": frozen,
                "reason": reason or default_reason,
                "fabric_id": fabric_id,
                "timestamp": timestamp,
                "user": actor,
                "updated_by": actor,
            }
            with self._lock:
                self._local_fabric_freeze[fabric_id] = state
            client = self._get_redis()
            if client:
                try:
                    client.set(
                        f"netops:factory_freeze:fabric:{fabric_id}",
                        json.dumps(state, ensure_ascii=False),
                    )
                except Exception as exc:
                    self._handle_redis_error(exc)
            return dict(state)

        default_reason = "Заморозка активирована оператором" if frozen else "Штатная эксплуатация"
        state = {
            "frozen": frozen,
            "reason": reason or default_reason,
            "timestamp": timestamp,
            "user": actor,
            "updated_by": actor,
        }
        with self._lock:
            self._local_freeze.update(state)
        client = self._get_redis()
        if client:
            try:
                client.set("netops:factory_freeze", json.dumps(state, ensure_ascii=False))
            except Exception as exc:
                self._handle_redis_error(exc)
        return dict(state)

    def is_factory_frozen(self, fabric_id: str | None = None) -> bool:
        """Return True if global factory freeze or stand-specific freeze is active."""
        if bool(self.get_factory_freeze().get("frozen", False)):
            return True
        if fabric_id:
            return bool(self.get_factory_freeze(fabric_id=fabric_id).get("frozen", False))
        return False

    def reset_factory_freeze(self) -> None:
        """Reset all factory freeze state (global and stands)."""
        with self._lock:
            self._local_freeze.update({
                "frozen": False,
                "reason": "Штатная эксплуатация (заморозка выключена)",
                "timestamp": None,
                "user": None,
                "updated_by": None,
            })
            self._local_fabric_freeze.clear()
        client = self._get_redis()
        if client:
            try:
                keys = list(client.scan_iter("netops:factory_freeze*"))
                if keys:
                    client.delete(*keys)
            except Exception as exc:
                self._handle_redis_error(exc)
