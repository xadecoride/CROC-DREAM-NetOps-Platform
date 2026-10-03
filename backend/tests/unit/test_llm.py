"""Unit tests for LLM Risk Analyzer service and heuristic engine."""

from __future__ import annotations

from netops.services.llm import analyze_diff_heuristic


def test_heuristic_empty_patch_is_low_risk() -> None:
    result = analyze_diff_heuristic(
        hostname="spine-1.croc.lab",
        platform="arista_eos",
        remediation_patch="",
        rollback_patch="",
    )
    assert result.risk_level == "LOW"
    assert result.is_safe is True
    assert "полностью синхронизирована" in result.summary


def test_heuristic_detects_bgp_password_risk() -> None:
    patch = """
router bgp 65101
 neighbor 10.0.1.0 password fabric-secret
"""
    result = analyze_diff_heuristic(
        hostname="leaf-1.croc.lab",
        platform="cisco_iosxe",
        remediation_patch=patch,
        rollback_patch="",
    )
    assert result.risk_level in ["HIGH", "CRITICAL"]
    assert result.is_safe is False
    assert any("BGP" in p for p in result.key_points)


def test_heuristic_detects_acl_deny_risk() -> None:
    patch = """
ip access-list extended MGMT-IN
 20 deny ip any any
"""
    result = analyze_diff_heuristic(
        hostname="leaf-1.croc.lab",
        platform="cisco_iosxe",
        remediation_patch=patch,
        rollback_patch="",
    )
    assert result.risk_level in ["HIGH", "CRITICAL"]
    assert result.is_safe is False
    assert any("ACL" in p for p in result.key_points)


def test_heuristic_detects_interface_shutdown_critical() -> None:
    patch = """
interface GigabitEthernet3
 shutdown
"""
    result = analyze_diff_heuristic(
        hostname="leaf-2.croc.lab",
        platform="cisco_iosxe",
        remediation_patch=patch,
        rollback_patch="",
    )
    assert result.risk_level == "CRITICAL"
    assert result.is_safe is False
    assert any("shutdown" in p.lower() for p in result.key_points)


def test_heuristic_cosmetic_description_is_safe() -> None:
    patch = """
interface Ethernet1
 description Uplink to spine-1 [verified]
"""
    result = analyze_diff_heuristic(
        hostname="spine-1.croc.lab",
        platform="arista_eos",
        remediation_patch=patch,
        rollback_patch="",
    )
    assert result.risk_level == "LOW"
    assert result.is_safe is True
