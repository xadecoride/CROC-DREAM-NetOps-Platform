"""Adversarial Challenge Test Suite: Protocol Cascade, Negation Word Guards & Failure Recovery.

Authored by Challenger 2 (M3) to empirically stress-test:
1. Negation word attacks: ensure outputs with "inactive", "disabled", "not running",
   "not configured", "stopped", "down" do NOT trigger false-positive protocol upgrades.
2. Uncovered negation gaps ("not active", "not enabled", "failed", "stopped").
3. Network failures & timeouts: socket connect, UDP recv, HTTP, DNS failure.
4. Priority sequence enforcement across heterogeneous platform profiles.
5. Fallback to TelemetryProtocol.SSH_CLI when all candidate probes fail.
"""

from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from typing import Any
from unittest.mock import MagicMock

import pytest

from netops.enums import TelemetryProtocol
from netops.intent.models import GnmiSpec, PlatformProfile
from netops.services.telemetry import detect_optimal_telemetry_protocol

# ==============================================================================
# 1. Negation Word Attacks (Baseline Required Behavior)
# ==============================================================================


@pytest.mark.parametrize(
    "phrase",
    [
        "gRPC service is inactive",
        "service is inactive",
        "grpc: disabled",
        "Management API gNMI is disabled",
        "daemon is not running",
        "not running",
        "gRPC service is not configured",
        "not configured",
        "gRPC server state: stopped",
        "service status: down",
    ],
)
def test_gnmi_negation_words_reject_protocol_upgrade(
    phrase: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate CLI outputs containing negation words and verify gNMI is NOT upgraded."""
    monkeypatch.setattr(
        socket, "create_connection", MagicMock(side_effect=OSError("Port closed"))
    )

    detected = detect_optimal_telemetry_protocol(
        "10.0.0.1",
        "arista_eos",
        ssh_send=lambda cmd: phrase,
        timeout=0.02,
    )
    # Must reject gNMI and fall back to ssh_cli
    assert detected["protocol"] != TelemetryProtocol.GNMI
    assert detected["protocol"] == TelemetryProtocol.SSH_CLI
    assert detected["cli_supported"] is True


@pytest.mark.parametrize(
    "phrase",
    [
        "snmp-server is disabled",
        "snmp-agent is inactive",
        "snmp-server is not configured",
        "snmp-server: invalid community",
    ],
)
def test_snmp_negation_words_reject_protocol_upgrade(
    phrase: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate CLI outputs containing negation words and verify SNMP is NOT upgraded."""
    monkeypatch.setattr(
        socket, "create_connection", MagicMock(side_effect=OSError("Port closed"))
    )

    detected = detect_optimal_telemetry_protocol(
        "10.0.0.1",
        "arista_eos",
        ssh_send=lambda cmd: "gNMI disabled" if "gnmi" in cmd else phrase,
        timeout=0.02,
    )
    assert detected["protocol"] != TelemetryProtocol.SNMP
    assert detected["protocol"] == TelemetryProtocol.SSH_CLI


# ==============================================================================
# 2. Negation Gaps & Vulnerability Reproductions
# ==============================================================================


@pytest.mark.parametrize(
    "phrase",
    [
        "grpc service is not active",
        "grpc service is not enabled",
        "grpc status: failed",
        "grpc service is shutdown",
    ],
)
def test_gnmi_negation_gap_vulnerabilities(
    phrase: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that gNMI negation pattern catches 'not active', 'not enabled', 'failed', 'shutdown'."""
    monkeypatch.setattr(
        socket, "create_connection", MagicMock(side_effect=OSError("Port closed"))
    )

    detected = detect_optimal_telemetry_protocol(
        "10.0.0.1",
        "arista_eos",
        ssh_send=lambda cmd: phrase,
        timeout=0.02,
    )
    assert detected["protocol"] == TelemetryProtocol.SSH_CLI
    assert detected["cli_supported"] is True


@pytest.mark.parametrize(
    "phrase",
    [
        "snmp-server is stopped",
        "snmp-server is down",
        "snmp-server is not running",
        "snmp service: shutdown",
        "snmp status: failed",
    ],
)
def test_snmp_negation_gap_vulnerabilities(
    phrase: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that SNMP negation pattern catches 'stopped', 'down', 'not running', 'shutdown', 'failed'."""
    monkeypatch.setattr(
        socket, "create_connection", MagicMock(side_effect=OSError("Port closed"))
    )

    detected = detect_optimal_telemetry_protocol(
        "10.0.0.1",
        "arista_eos",
        ssh_send=lambda cmd: "disabled" if "gnmi" in cmd else phrase,
        timeout=0.02,
    )
    assert detected["protocol"] == TelemetryProtocol.SSH_CLI
    assert detected["cli_supported"] is True


def test_node_exporter_negation_gap_vulnerability(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that node_exporter CLI check guards against 'stopped / inactive' and falls back to SSH_CLI."""
    monkeypatch.setattr(
        socket, "create_connection", MagicMock(side_effect=OSError("Port closed"))
    )

    detected = detect_optimal_telemetry_protocol(
        "10.0.0.1",
        "linux_server",
        ssh_send=lambda cmd: "service node_exporter: stopped / inactive (dead)",
        timeout=0.02,
    )
    assert detected["protocol"] == TelemetryProtocol.SSH_CLI
    assert detected["cli_supported"] is True



# ==============================================================================
# 3. Timeout / Network Failure Simulation
# ==============================================================================


def test_timeout_socket_connect_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate TCP connection timeout on all sockets."""
    def _mock_timeout(*_: Any, **__: Any) -> None:
        raise TimeoutError("TCP connection timed out")

    monkeypatch.setattr(socket, "create_connection", _mock_timeout)

    detected = detect_optimal_telemetry_protocol(
        "192.0.2.1",
        "arista_eos",
        ssh_send=lambda cmd: "gNMI disabled",
        timeout=0.01,
    )
    assert detected["protocol"] == TelemetryProtocol.SSH_CLI
    assert detected["port"] == 22


def test_timeout_connection_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate RST packet / connection refused on candidate ports."""
    monkeypatch.setattr(
        socket, "create_connection", MagicMock(side_effect=ConnectionRefusedError("RST"))
    )

    detected = detect_optimal_telemetry_protocol(
        "10.0.0.1",
        "cisco_iosxe",
        ssh_send=lambda cmd: "show gnmi: disabled",
        timeout=0.01,
    )
    assert detected["protocol"] == TelemetryProtocol.SSH_CLI


def test_timeout_dns_resolution_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate DNS resolution failure (socket.gaierror)."""
    dns_err = socket.gaierror(-2, "Name or service not known")
    monkeypatch.setattr(socket, "create_connection", MagicMock(side_effect=dns_err))


    detected = detect_optimal_telemetry_protocol(
        "nonexistent-host.local",
        "huawei_vrp",
        ssh_send=lambda cmd: "display grpc: inactive",
        timeout=0.01,
    )
    assert detected["protocol"] == TelemetryProtocol.SSH_CLI


def test_timeout_snmp_udp_recvfrom_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate SNMP UDP socket timeout when device drops UDP probe."""
    monkeypatch.setattr(
        socket, "create_connection", MagicMock(side_effect=OSError("Filtered"))
    )

    detected = detect_optimal_telemetry_protocol(
        "10.0.0.1",
        "arista_eos",
        ssh_send=lambda cmd: "disabled",
        timeout=0.01,
    )
    assert detected["protocol"] == TelemetryProtocol.SSH_CLI


def test_timeout_http_exporter_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate HTTP node_exporter endpoint timeout and socket timeout."""
    def _mock_urlopen(*_: Any, **__: Any) -> None:
        raise urllib.error.URLError("HTTP connection timed out")

    monkeypatch.setattr(urllib.request, "urlopen", _mock_urlopen)
    monkeypatch.setattr(socket, "create_connection", MagicMock(side_effect=OSError("Timeout")))

    detected = detect_optimal_telemetry_protocol(
        "10.0.0.1",
        "linux_server",
        ssh_send=lambda cmd: "",
        timeout=0.01,
    )
    assert detected["protocol"] == TelemetryProtocol.SSH_CLI


def test_timeout_ssh_send_exception_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate SSH command raising unexpected RuntimeError/TimeoutError."""
    def _failing_ssh(cmd: str) -> str:
        raise RuntimeError("SSH channel timed out")

    monkeypatch.setattr(socket, "create_connection", MagicMock(side_effect=OSError("Closed")))

    detected = detect_optimal_telemetry_protocol(
        "10.0.0.1",
        "arista_eos",
        ssh_send=_failing_ssh,
        timeout=0.01,
    )
    assert detected["protocol"] == TelemetryProtocol.SSH_CLI
    assert detected["cli_supported"] is True  # ssh_send callable was provided


def test_timeout_bounded_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empirically verify that detection completes in bounded time under socket failures."""
    monkeypatch.setattr(socket, "create_connection", MagicMock(side_effect=OSError("Timeout")))

    start = time.perf_counter()
    detected = detect_optimal_telemetry_protocol(
        "10.0.0.1",
        "arista_eos",
        ssh_send=lambda cmd: "disabled",
        timeout=0.02,
    )
    elapsed = time.perf_counter() - start
    assert detected["protocol"] == TelemetryProtocol.SSH_CLI
    assert elapsed < 0.5, f"Protocol cascade took {elapsed:.2f}s, expected < 0.5s"


# ==============================================================================
# 4. Priority Sequence & Honors platform_profile.protocol_priority
# ==============================================================================


def test_priority_bypass_vulnerability_when_ssh_cli_first() -> None:
    """Verify that when ssh_cli is placed first in protocol_priority, it is honored immediately."""
    prof = PlatformProfile(
        platform="legacy_switch",
        vendor="LegacyNet",
        default_interface="eth0",
        commands={},
        telemetry={},
        gnmi=GnmiSpec(default_port=6030, check_command="show gnmi", match_tokens=["active"]),
        protocol_priority=["ssh_cli", "gnmi"],
    )

    detected = detect_optimal_telemetry_protocol(
        "10.0.0.1",
        "legacy_switch",
        platform_profile=prof,
        ssh_send=lambda cmd: "active",
        timeout=0.01,
    )
    assert detected["protocol"] == TelemetryProtocol.SSH_CLI


# ==============================================================================
# 5. Fallback to TelemetryProtocol.SSH_CLI
# ==============================================================================


def test_fallback_when_all_probes_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """When all candidate protocol probes fail, return SSH_CLI with port 22."""
    monkeypatch.setattr(socket, "create_connection", MagicMock(side_effect=OSError("Closed")))

    detected = detect_optimal_telemetry_protocol(
        "10.0.0.1",
        "arista_eos",
        ssh_send=lambda cmd: "",
        timeout=0.01,
    )
    assert detected["protocol"] == TelemetryProtocol.SSH_CLI
    assert detected["port"] == 22
    assert detected["tcp_open"] is False


def test_fallback_when_profile_is_none() -> None:
    """When platform profile cannot be loaded, safely fall back to SSH_CLI."""
    detected = detect_optimal_telemetry_protocol(
        "10.0.0.1",
        "nonexistent_unknown_platform_123",
        timeout=0.01,
    )
    assert detected["protocol"] == TelemetryProtocol.SSH_CLI
    assert detected["port"] == 22
    assert detected["cli_supported"] is False
    assert detected["tcp_open"] is False


def test_fallback_preserves_ssh_send_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify cli_supported reflects whether ssh_send was available during fallback."""
    monkeypatch.setattr(socket, "create_connection", MagicMock(side_effect=OSError("Closed")))

    detected_with_ssh = detect_optimal_telemetry_protocol(
        "192.0.2.1",
        "arista_eos",
        ssh_send=lambda cmd: "disabled",
        timeout=0.01,
    )
    assert detected_with_ssh["protocol"] == TelemetryProtocol.SSH_CLI
    assert detected_with_ssh["cli_supported"] is True

    detected_without_ssh = detect_optimal_telemetry_protocol(
        "192.0.2.1",
        "arista_eos",
        ssh_send=None,
        timeout=0.01,
    )
    assert detected_without_ssh["protocol"] == TelemetryProtocol.SSH_CLI
    assert detected_without_ssh["cli_supported"] is False

