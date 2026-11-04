"""Unit tests for LLM Risk Analyzer service and heuristic engine."""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import patch

from pydantic import SecretStr

from netops.services.llm import analyze_diff_heuristic, chat_with_llm
from netops.settings import Settings


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
    patch_text = """
router bgp 65101
 neighbor 10.0.1.0 password fabric-secret
"""
    result = analyze_diff_heuristic(
        hostname="leaf-1.croc.lab",
        platform="cisco_iosxe",
        remediation_patch=patch_text,
        rollback_patch="",
    )
    assert result.risk_level in ["HIGH", "CRITICAL"]
    assert result.is_safe is False
    assert any("BGP" in p for p in result.key_points)


def test_heuristic_detects_acl_deny_risk() -> None:
    patch_text = """
ip access-list extended MGMT-IN
 20 deny ip any any
"""
    result = analyze_diff_heuristic(
        hostname="leaf-1.croc.lab",
        platform="cisco_iosxe",
        remediation_patch=patch_text,
        rollback_patch="",
    )
    assert result.risk_level in ["HIGH", "CRITICAL"]
    assert result.is_safe is False
    assert any("ACL" in p for p in result.key_points)


def test_heuristic_detects_interface_shutdown_critical() -> None:
    patch_text = """
interface GigabitEthernet3
 shutdown
"""
    result = analyze_diff_heuristic(
        hostname="leaf-2.croc.lab",
        platform="cisco_iosxe",
        remediation_patch=patch_text,
        rollback_patch="",
    )
    assert result.risk_level == "CRITICAL"
    assert result.is_safe is False
    assert any("shutdown" in p.lower() for p in result.key_points)


def test_heuristic_cosmetic_description_is_safe() -> None:
    patch_text = """
interface Ethernet1
 description Uplink to spine-1 [verified]
"""
    result = analyze_diff_heuristic(
        hostname="spine-1.croc.lab",
        platform="arista_eos",
        remediation_patch=patch_text,
        rollback_patch="",
    )
    assert result.risk_level == "LOW"
    assert result.is_safe is True


def test_chat_with_llm_timeout_floor_default() -> None:
    sig = inspect.signature(chat_with_llm)
    assert sig.parameters["timeout_floor"].default == 2.0


def test_chat_with_llm_unconfigured_returns_none() -> None:
    settings = Settings(groq_api_key=None, gemini_api_key=None, llm_api_key=None)
    res = asyncio.run(chat_with_llm(settings, "system", "user"))
    assert res is None


def test_chat_with_llm_provider_success() -> None:
    settings = Settings(groq_api_key=SecretStr("mock-key"))
    mock_response = '{"choices": [{"message": {"content": "Анализ завершен."}}]}'
    with patch("netops.services.llm._sync_http_request", return_value=mock_response):
        res = asyncio.run(chat_with_llm(settings, "system", "user"))
        assert res is not None
        assert res[0] == "Анализ завершен."
        assert "Groq" in res[1]
