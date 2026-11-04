from __future__ import annotations

import concurrent.futures
import threading
import time
from collections.abc import Sequence

import pytest

from netops.enums import Platform
from netops.network.base import ChangePlan, Credentials, DeviceTarget
from netops.network.scrapli_driver import (
    HUAWEI_COMMIT_LIST,
    ScrapliNetworkDriver,
    _latest_commit_id,
)

PLAN = ChangePlan(
    remediation="interface GE0/0/1\n description test\n",
    rollback="interface GE0/0/1\n undo description\n",
    intended="interface GE0/0/1\n description test\n",
)


def _target(hostname: str = "huawei-core-1", platform: Platform = Platform.HUAWEI_VRP) -> DeviceTarget:
    return DeviceTarget(
        hostname=hostname,
        platform=platform,
        host="127.0.0.1",
        port=2211,
        credentials=Credentials("admin", "admin"),
    )


class ScriptableMockConnection:
    """Mock connection that records sent commands and responds according to scripted callbacks or static outputs."""

    def __init__(
        self,
        command_responses: dict[str, str] | None = None,
        default_response: str = "",
        fail_on_command: dict[str, Exception] | None = None,
        delay: float = 0.0,
    ) -> None:
        self.commands: list[str] = []
        self.configs: list[list[str]] = []
        self.command_responses = command_responses or {}
        self.default_response = default_response
        self.fail_on_command = fail_on_command or {}
        self.delay = delay
        self._lock = threading.Lock()

    def __enter__(self) -> ScriptableMockConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def send_command(self, command: str) -> object:
        if self.delay > 0:
            time.sleep(self.delay)
        with self._lock:
            self.commands.append(command)
        if command in self.fail_on_command:
            raise self.fail_on_command[command]
        result = self.command_responses.get(command, self.default_response)
        return type("MockResponse", (), {"result": result, "failed": False})()

    def send_configs(self, lines: Sequence[str]) -> object:
        if self.delay > 0:
            time.sleep(self.delay)
        with self._lock:
            self.configs.append(list(lines))
        return type("MockResponse", (), {"result": "", "failed": False})()


# ==============================================================================
# SECTION 1: Adversarial Commit-ID Regex Edge Cases
# ==============================================================================

@pytest.mark.parametrize(
    ("raw_input", "expected"),
    [
        # Empty and whitespace
        ("", None),
        ("   ", None),
        ("\t\n\r\n", None),
        # Pure text / headers without digits
        ("CommitId Label User", None),
        ("--------------------------------------------------", None),
        ("Info: Current configuration commit list is empty.", None),
        ("Error: Command not recognized", None),
        # Digits strictly smaller than 6 digits
        ("1", None),
        ("42", None),
        ("123", None),
        ("2026", None),  # 4-digit year
        ("99999", None),  # 5 digits boundary
        ("1 2 3 4 5", None),
        ("2026-10-15 12:34:56", None),  # Date string without 6-digit contiguous numbers
        # Exact 6-digit boundary
        ("100000", "100000"),
        ("999999", "999999"),
        # Standard Huawei VRP commit IDs (10 digits)
        ("1000000013", "1000000013"),
        ("1000000000", "1000000000"),
        ("1000000042", "1000000042"),
        # Large numbers (12, 18, 24 digits)
        ("123456789012", "123456789012"),
        ("999999999999999999", "999999999999999999"),
        # Real-world Huawei VRP multi-column outputs
        (
            "-------------------------------------------------------------------\n"
            "CommitId     Label          User\n"
            "-------------------------------------------------------------------\n"
            "1000000015                  netops\n"
            "1000000014                  admin\n",
            "1000000015",
        ),
        (
            "CommitId     Time                     User\n"
            "1000000044   2026-10-16 03:00:00      noc-engineer\n"
            "1000000043   2026-10-16 02:00:00      admin\n",
            "1000000044",
        ),
        # Table with leading index column where index < 6 digits
        (
            "Index CommitId   User\n"
            "1     1000000099 admin\n"
            "2     1000000098 admin\n",
            "1000000099",
        ),
        # IP addresses and port numbers on the line
        (
            "User admin (192.168.100.200:2211) committed 1000000077\n",
            "1000000077",
        ),
        # Trailing/leading tabs, carriage returns, spaces
        (" \t\r\n 1000000088 \t\r\n ", "1000000088"),
        # Alphanumeric words containing numbers vs standalone numbers
        # 'admin123456' is single word token; next standalone 6-digit is 1000000090
        ("User: admin123456 commit 1000000090", "1000000090"),
        # Non-ASCII / Unicode / Cyrillic surrounding text
        ("Коммит конфигурации: 1000000033 оператор: Иванов", "1000000033"),
        ("🔥 Успешно: 1000000055 🚀", "1000000055"),
    ],
)
def test_adversarial_commit_id_regex_parsing(raw_input: str, expected: str | None) -> None:
    assert _latest_commit_id(raw_input) == expected


def test_adversarial_commit_id_performance_and_no_catastrophic_backtracking() -> None:
    """Stress test with 20,000 lines of non-matching log and matching commit at the end."""
    non_matching_lines = [f"Log entry line {i}: interface GE0/0/{i % 24} status changed" for i in range(20000)]
    non_matching_lines.append("1000000099 admin system commit")
    huge_output = "\n".join(non_matching_lines)

    start = time.perf_counter()
    commit_id = _latest_commit_id(huge_output)
    elapsed = time.perf_counter() - start

    assert commit_id == "1000000099"
    # Must parse in less than 0.1s
    assert elapsed < 0.1, f"Parsing took too long: {elapsed:.4f}s"


# ==============================================================================
# SECTION 2: Rollback and Apply Edge Cases on Huawei VRP
# ==============================================================================

def test_huawei_apply_disconnect_during_commit_list_raises_and_leaves_dict_clean() -> None:
    """If SSH disconnects during 'display configuration commit list', apply fails immediately

    and _commit_ids does not retain partial or corrupt state.
    """
    driver = ScrapliNetworkDriver()
    failing_conn = ScriptableMockConnection(
        fail_on_command={HUAWEI_COMMIT_LIST: ConnectionResetError("SSH connection reset by peer")}
    )
    driver._get_connection = lambda target, **_kw: failing_conn  # type: ignore[method-assign]
    target = _target("huawei-leaf-1")

    with pytest.raises(ConnectionResetError, match="SSH connection reset by peer"):
        driver.apply(target, PLAN, confirm_timeout=180)

    assert "huawei-leaf-1" not in driver._commit_ids
    assert failing_conn.commands == [HUAWEI_COMMIT_LIST]
    assert failing_conn.configs == []


def test_huawei_rollback_after_apply_disconnect_falls_back_to_last_1() -> None:
    """When apply fails during commit list, subsequent emergency rollback falls back cleanly

    to 'rollback configuration last 1' without raising KeyErrors.
    """
    driver = ScrapliNetworkDriver()
    conn = ScriptableMockConnection()
    driver._get_connection = lambda target, **_kw: conn  # type: ignore[method-assign]
    target = _target("huawei-leaf-1")

    # Driver has no recorded commit ID for target
    assert "huawei-leaf-1" not in driver._commit_ids
    driver.rollback(target, PLAN)

    assert conn.commands == [
        "system-view",
        "rollback configuration last 1",
        "commit",
    ]


def test_huawei_apply_empty_commit_list_falls_back_to_last_1_on_rollback() -> None:
    """When device returns an empty commit list (e.g. fresh switch), apply records None

    and rollback cleanly executes 'rollback configuration last 1'.
    """
    driver = ScrapliNetworkDriver()
    conn = ScriptableMockConnection(
        command_responses={HUAWEI_COMMIT_LIST: "Info: No commit history found.\n"}
    )
    driver._get_connection = lambda target, **_kw: conn  # type: ignore[method-assign]
    target = _target("huawei-leaf-2")

    driver.apply(target, PLAN, confirm_timeout=180)
    assert driver._commit_ids["huawei-leaf-2"] is None

    driver.rollback(target, PLAN)
    assert "huawei-leaf-2" not in driver._commit_ids
    assert "rollback configuration last 1" in conn.commands


def test_huawei_double_rollback_safety() -> None:
    """Executing rollback twice: first uses recorded commit ID, second falls back to last 1."""
    driver = ScrapliNetworkDriver()
    conn = ScriptableMockConnection(
        command_responses={HUAWEI_COMMIT_LIST: "CommitId 1000000050 admin\n"}
    )
    driver._get_connection = lambda target, **_kw: conn  # type: ignore[method-assign]
    target = _target("huawei-leaf-3")

    driver.apply(target, PLAN, confirm_timeout=180)
    assert driver._commit_ids["huawei-leaf-3"] == "1000000050"

    # First rollback
    driver.rollback(target, PLAN)
    assert "rollback configuration to commit-id 1000000050" in conn.commands
    assert "huawei-leaf-3" not in driver._commit_ids

    # Second rollback
    conn.commands.clear()
    driver.rollback(target, PLAN)
    assert conn.commands == [
        "system-view",
        "rollback configuration last 1",
        "commit",
    ]


def test_huawei_confirm_then_rollback() -> None:
    """Confirm pops the commit ID; any subsequent rollback uses last 1."""
    driver = ScrapliNetworkDriver()
    conn = ScriptableMockConnection(
        command_responses={HUAWEI_COMMIT_LIST: "CommitId 1000000060 admin\n"}
    )
    driver._get_connection = lambda target, **_kw: conn  # type: ignore[method-assign]
    target = _target("huawei-leaf-4")

    driver.apply(target, PLAN, confirm_timeout=180)
    assert driver._commit_ids["huawei-leaf-4"] == "1000000060"

    driver.confirm(target)
    assert "huawei-leaf-4" not in driver._commit_ids

    driver.rollback(target, PLAN)
    assert "rollback configuration last 1" in conn.commands


# ==============================================================================
# SECTION 3: Concurrency Stress Test Across 100 Simulated Devices
# ==============================================================================

def test_concurrency_across_100_devices() -> None:
    """Simulate 100 devices executing apply, confirm, and rollback concurrently

    using a shared ScrapliNetworkDriver instance.
    Verify:
    1. Zero cross-talk between hostnames.
    2. Exact commit ID mapping per device.
    3. Thread-safe dictionary access without race conditions or data corruption.
    """
    driver = ScrapliNetworkDriver(max_workers=20)
    num_devices = 100
    devices = [_target(f"huawei-node-{i:03d}") for i in range(num_devices)]

    # Map each device to its own connection mock and pre-assigned commit ID
    connections: dict[str, ScriptableMockConnection] = {}
    expected_commit_ids: dict[str, str] = {}

    for i, dev in enumerate(devices):
        cid = f"1000{i:06d}"
        expected_commit_ids[dev.hostname] = cid
        conn = ScriptableMockConnection(
            command_responses={HUAWEI_COMMIT_LIST: f"CommitId {cid} admin\n"},
            delay=0.001,  # Small sleep to maximize thread interleaving
        )
        connections[dev.hostname] = conn

    # Dynamic connection resolver based on target hostname
    driver._get_connection = lambda target, **_kw: connections[target.hostname]  # type: ignore[method-assign]

    # Step 1: Concurrently apply changes across all 100 devices
    def run_apply(dev: DeviceTarget) -> None:
        driver.apply(dev, PLAN, confirm_timeout=180)

    with concurrent.futures.ThreadPoolExecutor(max_workers=25) as executor:
        futures = [executor.submit(run_apply, dev) for dev in devices]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    # Verify all 100 devices have exactly their own commit ID recorded
    assert len(driver._commit_ids) == num_devices
    for dev in devices:
        assert driver._commit_ids[dev.hostname] == expected_commit_ids[dev.hostname]

    # Step 2: Concurrently:
    # 50 devices confirm
    # 50 devices rollback
    def run_action(dev: DeviceTarget, action: str) -> None:
        if action == "confirm":
            driver.confirm(dev)
        else:
            driver.rollback(dev, PLAN)

    actions = {dev.hostname: ("confirm" if i % 2 == 0 else "rollback") for i, dev in enumerate(devices)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=25) as executor:
        futures = [executor.submit(run_action, dev, actions[dev.hostname]) for dev in devices]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    # Step 3: Verify results
    # All commit IDs should have been popped from driver._commit_ids
    assert len(driver._commit_ids) == 0

    for dev in devices:
        conn = connections[dev.hostname]
        action = actions[dev.hostname]
        cid = expected_commit_ids[dev.hostname]
        if action == "confirm":
            # For Huawei VRP, confirm sends NO extra commands to the device
            assert conn.commands == [HUAWEI_COMMIT_LIST, "commit"]
        else:
            # For rollback, exact commit ID must match this specific device
            assert f"rollback configuration to commit-id {cid}" in conn.commands
            # Verify no other device's commit ID was called
            for other_dev, other_cid in expected_commit_ids.items():
                if other_dev != dev.hostname:
                    assert f"rollback configuration to commit-id {other_cid}" not in conn.commands
