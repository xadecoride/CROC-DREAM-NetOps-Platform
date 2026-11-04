"""Фоллбэк телеметрии: подтверждение деградации, журнал смен, чистка осиротевших override."""

from __future__ import annotations

from netops.services.state import get_state_manager
from netops.telemetry import fallback


def _plan(proto: str, **extra: object) -> dict[str, dict]:
    return {"leaf-3": {"protocol": proto, "host": "10.0.0.3", **extra}}


def test_transient_snmp_loss_is_confirmed_away() -> None:
    prev = _plan("snmp")
    plan = _plan("ssh_cli", error="timeout")
    calls: list[str] = []

    def reprobe(host: str) -> dict:
        calls.append(host)
        return {"protocol": "snmp", "host": "10.0.0.3"}

    fallback.confirm_downgrades(prev, plan, {}, reprobe)
    assert plan["leaf-3"]["protocol"] == "snmp"
    assert calls == ["leaf-3"]


def test_persistent_failure_downgrades_after_all_reprobes() -> None:
    plan = _plan("ssh_cli", error="timeout")
    calls: list[str] = []

    def reprobe(host: str) -> dict:
        calls.append(host)
        return {"protocol": "ssh_cli", "host": "10.0.0.3", "error": "timeout"}

    fallback.confirm_downgrades(_plan("snmp"), plan, {}, reprobe)
    assert plan["leaf-3"]["protocol"] == "ssh_cli"
    assert len(calls) == fallback.CONFIRM_ATTEMPTS


def test_override_and_ssh_origin_are_not_reprobed() -> None:
    def reprobe(host: str) -> dict:
        raise AssertionError("must not reprobe")

    fallback.confirm_downgrades(_plan("snmp"), _plan("ssh_cli"), {"leaf-3": "ssh_cli"}, reprobe)
    fallback.confirm_downgrades(_plan("ssh_cli"), _plan("ssh_cli"), {}, reprobe)


def test_changes_are_journaled_and_filtered() -> None:
    sm = get_state_manager()
    sm.set_db_state(fallback.EVENTS_KEY, [])
    events = fallback.record_changes(_plan("snmp"), _plan("ssh_cli", error="timeout"))
    assert events[0]["from"] == "snmp" and events[0]["to"] == "ssh_cli" and events[0]["reason"] == "timeout"
    assert fallback.record_changes(_plan("ssh_cli"), _plan("ssh_cli")) == []
    assert [e["hostname"] for e in fallback.get_events({"leaf-3"})] == ["leaf-3"]
    assert fallback.get_events({"other"}) == []


def test_huawei_renders_acls_before_interfaces_like_the_device() -> None:
    from netops.network.rendering import _sections_for

    assert _sections_for("huawei_vrp")[:3] == ("base", "acls", "interfaces")
    assert _sections_for("cisco_iosxe") == ("base", "interfaces", "acls", "bgp")


def test_bgp_command_comes_from_platform_yaml() -> None:
    from netops.services.telemetry import load_profile_for_platform

    assert load_profile_for_platform("huawei_vrp").commands["bgp_summary"] == "display bgp peer"
    assert load_profile_for_platform("cisco_iosxe").commands["bgp_summary"] == "show ip bgp summary"


def test_metric_samples_expose_active_protocol_and_fallbacks() -> None:
    get_state_manager().set_db_state(fallback.EVENTS_KEY, [])
    fallback.record_changes(_plan("snmp"), _plan("ssh_cli", error="timeout"))
    names = {(s.name, s.labels.get("protocol")) for s in fallback.metric_samples(_plan("ssh_cli"))}
    assert ("netops_telemetry_active_protocol", "ssh_cli") in names
    assert any(s.name == "netops_telemetry_fallback_events_total" and s.value == 1.0 for s in fallback.metric_samples({}))
