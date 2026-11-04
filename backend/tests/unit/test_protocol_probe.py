"""Протоколы телеметрии проверяются по-настоящему: живые сокеты, без подмены socket/urllib.

Главный регресс, который ловят эти тесты: «открытый TCP-порт = gNMI/SNMP/exporter».
"""

from __future__ import annotations

import http.server
import os
import socket
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from netops.enums import TelemetryProtocol
from netops.intent.models import ExporterSpec, GnmiSpec, PlatformProfile, SnmpSpec
from netops.services import protocol_probe
from netops.services.protocol_probe import (
    GnmiCapabilities,
    gnmic_capabilities,
    prometheus_exporter_ok,
    snmp_agent_versions,
    snmpv2c_get_sysdescr,
    snmpv3_discovery,
)
from netops.services.telemetry import detect_optimal_telemetry_protocol

PROM_TEXT = b"# HELP node_load1 1m load average.\n# TYPE node_load1 gauge\nnode_load1 0.42\n"


# ---------------------------------------------------------------------- живые серверы


@contextmanager
def tcp_listener() -> Iterator[int]:
    """TCP-порт, который принимает соединения, но не говорит ни на каком протоколе."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(8)
        yield srv.getsockname()[1]


@contextmanager
def http_server(body: bytes, status: int = 200) -> Iterator[int]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


@contextmanager
def snmp_agent(answer_versions: set[int], community: bytes = b"lab-ro") -> Iterator[int]:
    """UDP-«агент»: отвечает SEQUENCE{INTEGER version, ...} на запросы нужных версий."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(0.2)
    stop = threading.Event()

    def serve() -> None:
        while not stop.is_set():
            try:
                data, addr = sock.recvfrom(4096)
            except OSError:
                continue
            version = protocol_probe._message_version(data)
            if version not in answer_versions:
                continue
            if version == 1:
                if community not in data:
                    continue  # чужое community — настоящий агент молчит
                varbind = protocol_probe._seq(
                    protocol_probe._tlv(0x06, protocol_probe._SYS_DESCR_OID),
                    protocol_probe._octets(b"Linux TestSwitch"),
                )
                pdu = protocol_probe._tlv(
                    0xA2,
                    protocol_probe._int(1)
                    + protocol_probe._int(0)
                    + protocol_probe._int(0)
                    + protocol_probe._seq(varbind),
                )
                reply = protocol_probe._seq(
                    protocol_probe._int(1), protocol_probe._octets(community), pdu
                )
                sock.sendto(reply, addr)
            else:
                sock.sendto(data, addr)  # эхо сохраняет версию и структуру сообщения

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield sock.getsockname()[1]
    finally:
        stop.set()
        thread.join(1)
        sock.close()


def _profile(priority: list[str]) -> PlatformProfile:
    return PlatformProfile(
        platform="custom_switch",
        vendor="TestVendor",
        default_interface="eth0",
        commands={},
        telemetry={},
        gnmi=GnmiSpec(default_port=6030, check_command="show gnmi", match_tokens=["enabled"]),
        snmp=SnmpSpec(base_port=161),
        exporter=ExporterSpec(port=9100, path="/metrics"),
        protocol_priority=priority,
    )


def _caps_ok(*_args: object, **_kwargs: object) -> GnmiCapabilities:
    return GnmiCapabilities(ok=True, tls=True)


def _caps_refused(*_args: object, **_kwargs: object) -> GnmiCapabilities:
    return GnmiCapabilities(ok=False, detail="rpc error: Unavailable")


def _no_gnmic(*_args: object, **_kwargs: object) -> None:
    return None


# --------------------------------------------------------------------------- SNMP


def test_v2c_packet_matches_known_good_wire_bytes() -> None:
    known_good = (
        b"\x30\x29\x02\x01\x01\x04\x06public\xa0\x1c\x02\x04\x12\x34\x56\x78\x02\x01\x00"
        b"\x02\x01\x00\x30\x0e\x30\x0c\x06\x08\x2b\x06\x01\x02\x01\x01\x01\x00\x05\x00"
    )
    assert snmpv2c_get_sysdescr(b"public", 0x12345678) == known_good


def test_v3_discovery_is_credentialless_reportable_request() -> None:
    packet = snmpv3_discovery(0x1001)
    assert protocol_probe._message_version(packet) == 3
    assert b"\x04\x01\x04" in packet  # msgFlags = reportable, noAuthNoPriv
    assert b"public" not in packet


def test_snmp_detects_v3_without_credentials_and_v2c_only_with_right_community() -> None:
    with snmp_agent({1, 3}) as port:
        assert snmp_agent_versions("127.0.0.1", port, community=None, timeout=0.5) == {"v3"}
        assert snmp_agent_versions("127.0.0.1", port, community="wrong", timeout=0.5) == {"v3"}
        assert snmp_agent_versions("127.0.0.1", port, community="lab-ro", timeout=0.5) == {
            "v3",
            "v2c",
        }


def test_tcp_listener_on_snmp_port_is_not_an_snmp_agent() -> None:
    """Старый баг: SNMP — это UDP, TCP-connect к тому же номеру порта ничего не доказывает."""
    with tcp_listener() as port:
        assert snmp_agent_versions("127.0.0.1", port, community="lab-ro", timeout=0.3) == set()
        detected = detect_optimal_telemetry_protocol(
            "127.0.0.1",
            "custom_switch",
            platform_profile=_profile(["snmp", "ssh_cli"]),
            snmp_port=port,
            snmp_community="lab-ro",
            timeout=0.3,
        )
    assert detected["protocol"] == TelemetryProtocol.SSH_CLI


def test_snmpv3_only_agent_is_reported_but_not_upgraded() -> None:
    with snmp_agent({3}) as port:
        detected = detect_optimal_telemetry_protocol(
            "127.0.0.1",
            "custom_switch",
            platform_profile=_profile(["snmp", "ssh_cli"]),
            snmp_port=port,
            snmp_community="lab-ro",
            timeout=0.5,
        )
    assert detected["protocol"] == TelemetryProtocol.SSH_CLI
    assert detected["snmp_agent"] == ["v3"]


# ----------------------------------------------------------------------- exporter


def test_exporter_requires_prometheus_text_format() -> None:
    with http_server(PROM_TEXT) as port:
        assert prometheus_exporter_ok("127.0.0.1", port, "/metrics", 1.0)
    with http_server(b"<html>nginx welcome</html>") as port:
        assert not prometheus_exporter_ok("127.0.0.1", port, "/metrics", 1.0)
    with http_server(PROM_TEXT, status=401) as port:
        assert not prometheus_exporter_ok("127.0.0.1", port, "/metrics", 1.0)
    with tcp_listener() as port:
        assert not prometheus_exporter_ok("127.0.0.1", port, "/metrics", 0.3)


def test_linux_server_upgrades_to_node_exporter_over_real_http() -> None:
    with http_server(PROM_TEXT) as port:
        detected = detect_optimal_telemetry_protocol(
            "127.0.0.1", "linux_server", exporter_port=port, timeout=1.0
        )
    assert detected["protocol"] == TelemetryProtocol.PROMETHEUS_EXPORTER
    assert detected["port"] == port
    assert detected["verified_by"] == "http_metrics"


# --------------------------------------------------------------------------- gNMI


def test_open_tcp_port_alone_is_not_gnmi() -> None:
    """Главный регресс: порт 6030 открыт, но без Capabilities/CLI это не gNMI."""
    with tcp_listener() as port:
        detected = detect_optimal_telemetry_protocol(
            "127.0.0.1",
            "custom_switch",
            platform_profile=_profile(["gnmi", "ssh_cli"]),
            gnmi_port=port,
            username="admin",
            password="admin",
            gnmi_probe=_no_gnmic,
            timeout=0.3,
        )
    assert detected["protocol"] == TelemetryProtocol.SSH_CLI


def test_gnmi_verified_by_capabilities_and_rejected_when_capabilities_fail() -> None:
    with tcp_listener() as port:
        kwargs = {
            "platform_profile": _profile(["gnmi", "ssh_cli"]),
            "gnmi_port": port,
            "username": "admin",
            "password": "admin",
            "timeout": 0.3,
        }
        ok = detect_optimal_telemetry_protocol(
            "127.0.0.1", "custom_switch", gnmi_probe=_caps_ok, **kwargs
        )
        # CLI говорит «enabled», но gnmic получил отказ — верим протоколу, а не тексту.
        refused = detect_optimal_telemetry_protocol(
            "127.0.0.1",
            "custom_switch",
            gnmi_probe=_caps_refused,
            ssh_send=lambda _cmd: "gNMI enabled",
            **kwargs,
        )
    assert ok["protocol"] == TelemetryProtocol.GNMI
    assert ok["verified_by"] == "gnmi_capabilities"
    assert ok["tls"] is True
    assert refused["protocol"] == TelemetryProtocol.SSH_CLI


def test_gnmi_without_gnmic_needs_cli_confirmation_and_open_port() -> None:
    profile = _profile(["gnmi", "ssh_cli"])
    with tcp_listener() as port:
        detected = detect_optimal_telemetry_protocol(
            "127.0.0.1",
            "custom_switch",
            platform_profile=profile,
            gnmi_port=port,
            ssh_send=lambda _cmd: "gNMI transport grpc: enabled",
            gnmi_probe=_no_gnmic,
            timeout=0.3,
        )
    assert detected["protocol"] == TelemetryProtocol.GNMI
    assert detected["verified_by"] == "cli+tcp"
    assert detected["tls"] is None

    # CLI «enabled», но порт закрыт (файрвол) — сборщик не подключится, апгрейда нет.
    with tcp_listener() as closed_port:
        pass
    detected_closed = detect_optimal_telemetry_protocol(
        "127.0.0.1",
        "custom_switch",
        platform_profile=profile,
        gnmi_port=closed_port,
        ssh_send=lambda _cmd: "gNMI transport grpc: enabled",
        gnmi_probe=_no_gnmic,
        timeout=0.3,
    )
    assert detected_closed["protocol"] == TelemetryProtocol.SSH_CLI


def test_priority_order_is_honoured_when_several_protocols_are_proven() -> None:
    with tcp_listener() as gnmi_port, snmp_agent({1}) as snmp_port:
        common = {
            "gnmi_port": gnmi_port,
            "snmp_port": snmp_port,
            "snmp_community": "lab-ro",
            "username": "admin",
            "password": "admin",
            "gnmi_probe": _caps_ok,
            "timeout": 0.5,
        }
        gnmi_first = detect_optimal_telemetry_protocol(
            "127.0.0.1", "custom_switch", platform_profile=_profile(["gnmi", "snmp"]), **common
        )
        snmp_first = detect_optimal_telemetry_protocol(
            "127.0.0.1", "custom_switch", platform_profile=_profile(["snmp", "gnmi"]), **common
        )
        gnmi_down = detect_optimal_telemetry_protocol(
            "127.0.0.1",
            "custom_switch",
            platform_profile=_profile(["gnmi", "snmp"]),
            **{**common, "gnmi_probe": _caps_refused},
        )
    assert gnmi_first["protocol"] == TelemetryProtocol.GNMI
    assert snmp_first["protocol"] == TelemetryProtocol.SNMP
    assert snmp_first["verified_by"] == "snmp_v2c_response"
    assert gnmi_down["protocol"] == TelemetryProtocol.SNMP


# ------------------------------------------------------------- gnmic как процесс


def _fake_gnmic(tmp_path: Path, script: str) -> str:
    exe = tmp_path / "gnmic"
    exe.write_text("#!/bin/sh\n" + script)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


def test_gnmic_capabilities_passes_credentials_via_env_not_argv(tmp_path: Path) -> None:
    log = tmp_path / "argv.log"
    exe = _fake_gnmic(
        tmp_path,
        f'echo "$@ USER=$GNMIC_USERNAME PASS=$GNMIC_PASSWORD" > {log}\n'
        'echo "gNMI version: 0.10.0"\necho "supported models:"\n',
    )
    caps = gnmic_capabilities("10.0.0.1", 6030, "ops", "s3cret", timeout=1, binary=exe)
    assert caps == GnmiCapabilities(ok=True, tls=True)
    recorded = log.read_text()
    assert "USER=ops PASS=s3cret" in recorded
    argv_part = recorded.split(" USER=")[0]
    assert "s3cret" not in argv_part
    assert "-a 10.0.0.1:6030" in argv_part
    assert "--skip-verify" in argv_part


def test_gnmic_capabilities_falls_back_to_plaintext_then_fails(tmp_path: Path) -> None:
    plaintext_only = _fake_gnmic(
        tmp_path,
        'case "$*" in *--insecure*) echo "gNMI version: 0.7.0";; '
        '*) echo "tls: first record does not look like a TLS handshake" >&2; exit 1;; esac\n',
    )
    assert gnmic_capabilities("h", 1, "u", "p", timeout=1, binary=plaintext_only) == (
        GnmiCapabilities(ok=True, tls=False)
    )

    broken = tmp_path / "broken"
    broken.mkdir()
    refused = _fake_gnmic(broken, 'echo "rpc error: code = Unavailable" >&2\nexit 1\n')
    caps = gnmic_capabilities("h", 1, "u", "p", timeout=1, binary=refused)
    assert caps is not None
    assert caps.ok is False
    assert "Unavailable" in caps.detail


def test_gnmic_missing_means_cannot_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", os.devnull)
    assert gnmic_capabilities("h", 1, "u", "p") is None
