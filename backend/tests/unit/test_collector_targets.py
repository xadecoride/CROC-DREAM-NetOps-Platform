from __future__ import annotations

import json

import yaml

from netops.enums import TelemetryProtocol
from netops.telemetry.targets import (
    GNMIC_TARGETS_FILE,
    get_exporter_sd_targets,
    get_snmp_sd_targets,
    is_externally_collected,
    last_collector_plan,
    refresh_collector_targets,
)


def test_refresh_collector_targets_writes_files_without_secrets(
    session, toolchain, settings, devices
) -> None:
    hosts = sorted(devices)
    assert len(hosts) >= 3
    by_ip = {str(devices[h].management_ip): h for h in hosts}
    verdicts = {
        hosts[0]: {"protocol": TelemetryProtocol.GNMI, "port": 6030, "tls": False},
        hosts[1]: {"protocol": TelemetryProtocol.PROMETHEUS_EXPORTER, "port": 9101, "path": "/app"},
        hosts[2]: {"protocol": TelemetryProtocol.SNMP, "port": 161},
    }
    calls: list[dict] = []

    def fake_detect(host, platform, **kwargs):
        calls.append(kwargs)
        hostname = by_ip[host]
        if hostname == hosts[-1] and len(hosts) > 3:
            raise RuntimeError("probe exploded")
        return verdicts.get(hostname, {"protocol": TelemetryProtocol.SSH_CLI, "port": 22})

    summary = refresh_collector_targets(session, toolchain, settings, detect=fake_detect)

    assert all(c["username"] == "admin" and c["password"] == "admin" for c in calls)
    out = settings.offline_lab_path
    gnmic = yaml.safe_load((out / GNMIC_TARGETS_FILE).read_text())
    assert list(gnmic) == [hosts[0]]
    assert gnmic[hosts[0]]["insecure"] is True
    assert gnmic[hosts[0]]["event-tags"]["hostname"] == hosts[0]

    exporter = get_exporter_sd_targets()
    assert exporter[0]["labels"]["__metrics_path__"] == "/app"
    assert exporter[0]["targets"][0].endswith(":9101")
    snmp = get_snmp_sd_targets()
    assert [t["labels"]["hostname"] for t in snmp] == [hosts[2]]
    assert snmp[0]["labels"]["snmp_module"] in ("host_resources", "cisco_cpu", "huawei_cpu")

    # Verify no .json files were written to out dir and no secrets leaked
    assert list(out.glob("*.json")) == []
    for serialized in (
        (out / GNMIC_TARGETS_FILE).read_text(),
        json.dumps(exporter),
        json.dumps(snmp),
    ):
        assert "admin" not in serialized
        assert "password" not in serialized

    assert summary["protocols"]["gnmi"] == 1
    assert sum(summary["protocols"].values()) == len(hosts)
    assert last_collector_plan()["devices"].keys() == summary["devices"].keys()
    assert is_externally_collected(hosts[0]) is True
    assert is_externally_collected(hosts[1]) is True
    assert is_externally_collected(hosts[2]) is True
    if len(hosts) > 3:
        assert is_externally_collected(hosts[-1]) is False
    assert is_externally_collected("nonexistent.croc.lab") is False


def test_set_device_protocol_override_persists_to_postgres(
    session, toolchain, settings, devices
) -> None:
    from netops.telemetry.targets import get_protocol_overrides, set_device_protocol_override

    target_host = sorted(devices)[0]
    summary = set_device_protocol_override(session, toolchain, settings, target_host, "snmp")
    assert get_protocol_overrides().get(target_host) == "snmp"
    assert summary["devices"][target_host]["protocol"] == "snmp"
    assert summary["devices"][target_host]["selected_mode"] == "snmp"
    assert any(t["labels"]["hostname"] == target_host for t in get_snmp_sd_targets())


def test_unsupported_protocol_override_is_rejected() -> None:
    """Huawei VRP не умеет SNMP: оверрайд отклоняется и не подменяет рабочий ssh_cli."""
    from netops.telemetry.targets import _apply_protocol_override

    item = {"protocol": "ssh_cli", "port": 22, "host": "10.0.0.1", "platform": "huawei_vrp"}
    out = _apply_protocol_override(item, "snmp", "huawei_vrp", 22)
    assert out["override_rejected"] == "snmp_not_supported"
    assert out["protocol"] == "ssh_cli"
