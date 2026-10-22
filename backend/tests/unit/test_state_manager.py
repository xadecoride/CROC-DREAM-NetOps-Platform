"""Unit tests for StateManager (Redis + thread-safe in-memory fallback, distributed sync locks)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
import redis

from netops.services.state import StateManager, get_state_manager, reset_state_manager


@pytest.fixture(autouse=True)
def clean_state():
    reset_state_manager()
    yield
    reset_state_manager()


def test_singleton_get_and_reset():
    mgr1 = get_state_manager()
    mgr2 = get_state_manager()
    assert mgr1 is mgr2

    reset_state_manager()
    mgr3 = get_state_manager()
    assert mgr3 is not mgr1


def test_circuit_breaker_fast_fail_probe():
    """Verify that unreachable Redis triggers circuit breaker cooldown without lag."""
    mgr = StateManager(
        redis_url="redis://127.0.0.1:65432/0",
        probe_cooldown_seconds=10.0,
        socket_timeout=0.05,
    )
    start = time.monotonic()
    client1 = mgr._get_redis()
    elapsed1 = time.monotonic() - start
    assert client1 is None
    assert mgr._is_redis_online is False
    assert elapsed1 < 0.2

    # Second call within cooldown must immediately return None without network attempt (<0.01s)
    start2 = time.monotonic()
    client2 = mgr._get_redis()
    elapsed2 = time.monotonic() - start2
    assert client2 is None
    assert elapsed2 < 0.01


def test_kv_primitives_and_ttl():
    mgr = StateManager(enable_redis=False)

    assert mgr.get("unknown_key") is None
    assert mgr.set("my_key", "hello_world", ex=10) is True
    assert mgr.get("my_key") == "hello_world"

    # JSON get/set
    mgr.set_json("json_key", {"status": "ok", "count": 42})
    assert mgr.get_json("json_key") == {"status": "ok", "count": 42}

    # Delete
    assert mgr.delete("my_key") is True
    assert mgr.get("my_key") is None
    assert mgr.delete("my_key") is False

    # In-memory TTL expiration
    mgr._local_kv["exp_key"] = (time.time() - 1.0, "expired")
    assert mgr.get("exp_key") is None


def test_distributed_sync_lock_acquire_and_release():
    mgr = StateManager(enable_redis=False)
    target = "device:leaf-1"

    assert mgr.is_sync_locked(target) is False

    # 1. Acquire lock
    reason = "Deploying leaf-1"
    assert mgr.acquire_sync_lock(target, holder="deploy:job-1", ttl=60, reason=reason) is True
    assert mgr.is_sync_locked(target) is True

    info = mgr.get_sync_lock_info(target)
    assert info is not None
    assert info["target"] == target
    assert info["holder"] == "deploy:job-1"
    assert info["reason"] == "Deploying leaf-1"
    assert info["ttl"] == 60

    # 2. Competing acquire must be rejected
    assert mgr.acquire_sync_lock(target, holder="deploy:job-2", ttl=60) is False

    # 3. Release with WRONG holder must fail (holder verification)
    assert mgr.release_sync_lock(target, holder="deploy:job-wrong") is False
    assert mgr.is_sync_locked(target) is True

    # 4. Release with CORRECT holder succeeds
    assert mgr.release_sync_lock(target, holder="deploy:job-1") is True
    assert mgr.is_sync_locked(target) is False
    assert mgr.get_sync_lock_info(target) is None

    # Re-releasing unlocked target returns False
    assert mgr.release_sync_lock(target, holder="deploy:job-1") is False


def test_sync_lock_expiration():
    mgr = StateManager(enable_redis=False)
    target = "fabric:croc-dc1-pod1"

    # Acquire lock with expired timestamp
    mgr.acquire_sync_lock(target, holder="deploy:job-old", ttl=1)
    mgr._local_locks[target] = (time.time() - 5.0, mgr._local_locks[target][1])

    assert mgr.is_sync_locked(target) is False
    assert mgr.get_sync_lock_info(target) is None

    # New holder can acquire immediately
    assert mgr.acquire_sync_lock(target, holder="deploy:job-new", ttl=60) is True
    assert mgr.is_sync_locked(target) is True


def test_telemetry_cache():
    mgr = StateManager(enable_redis=False)

    mgr.append_telemetry("leaf-1", "cpu_pct", 15.5, max_points=5)
    mgr.append_telemetry("leaf-1", "cpu_pct", 20.0, max_points=5)
    mgr.append_telemetry("leaf-1", "cpu_pct", 25.5, max_points=5)

    series = mgr.get_telemetry_series("leaf-1", "cpu_pct", points=2)
    assert series == [20.0, 25.5]

    # Max points pruning
    for val in [30.0, 35.0, 40.0]:
        mgr.append_telemetry("leaf-1", "cpu_pct", val, max_points=5)
    all_series = mgr.get_telemetry_series("leaf-1", "cpu_pct", points=10)
    assert len(all_series) == 5
    assert all_series == [20.0, 25.5, 30.0, 35.0, 40.0]

    mgr.clear_telemetry()
    assert mgr.get_telemetry_series("leaf-1", "cpu_pct", points=10) == []


def test_git_sot_meta_cache():
    mgr = StateManager(enable_redis=False)
    data = {"commit_sha": "abc1234", "branch": "main"}

    assert mgr.get_git_sot_meta("repo_1") is None
    mgr.set_git_sot_meta("repo_1", data, ttl=10)
    assert mgr.get_git_sot_meta("repo_1") == data

    # Clear
    mgr.clear_git_sot_meta()
    assert mgr.get_git_sot_meta("repo_1") is None


def test_rendered_template_cache():
    mgr = StateManager(enable_redis=False)

    assert mgr.get_rendered_template("key1") is None
    mgr.set_rendered_template("key1", "hostname leaf-1\n", ttl=300)
    assert mgr.get_rendered_template("key1") == "hostname leaf-1\n"


def test_factory_freeze_state():
    mgr = StateManager(enable_redis=False)

    assert mgr.is_factory_frozen() is False
    assert mgr.is_factory_frozen("croc-dc1-pod1") is False

    # Freeze stand-1 only
    mgr.set_factory_freeze(True, reason="Issue in pod1", fabric_id="croc-dc1-pod1")
    assert mgr.is_factory_frozen("croc-dc1-pod1") is True
    assert mgr.is_factory_frozen("croc-dc2-pod1") is False
    assert mgr.is_factory_frozen() is False

    # Stand freeze state
    state = mgr.get_factory_freeze("croc-dc1-pod1")
    assert state["frozen"] is True
    assert state["reason"] == "Issue in pod1"

    # Global freeze affects all stands
    mgr.set_factory_freeze(True, reason="Total Freeze")
    assert mgr.is_factory_frozen() is True
    assert mgr.is_factory_frozen("croc-dc1-pod1") is True
    assert mgr.is_factory_frozen("croc-dc2-pod1") is True

    # Reset
    mgr.reset_factory_freeze()
    assert mgr.is_factory_frozen() is False
    assert mgr.is_factory_frozen("croc-dc1-pod1") is False


def test_transparent_redis_switching_and_failover():
    """Verify that when Redis is online, operations use Redis; on failure, memory failover works."""
    mgr = StateManager(enable_redis=True)
    mock_redis = MagicMock()
    mock_redis.ping.return_value = True
    mock_redis.set.return_value = True
    mock_redis.get.return_value = '{"target": "device:1", "holder": "h1", "reason": "r", "ttl": 60}'
    mock_redis.delete.return_value = 1

    mgr._client = mock_redis
    mgr._is_redis_online = True

    # Lock acquire delegates to Redis
    acquired = mgr.acquire_sync_lock("device:1", holder="h1", ttl=60)
    assert acquired is True
    mock_redis.set.assert_called_once()

    # Lock check delegates to Redis
    assert mgr.is_sync_locked("device:1") is True
    info = mgr.get_sync_lock_info("device:1")
    assert info["holder"] == "h1"

    # Release with holder delegates to Redis
    released = mgr.release_sync_lock("device:1", holder="h1")
    assert released is True

    # Simulate Redis connection failure during an operation
    mock_redis.get.side_effect = redis.exceptions.ConnectionError("Redis down")
    # Should not raise exception; instead fails over to in-memory store
    val = mgr.get("any_key")
    assert val is None
    assert mgr._is_redis_online is False
