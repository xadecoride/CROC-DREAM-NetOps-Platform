"""Честные пробы протоколов телеметрии: открытый порт — ещё не протокол.

- gNMI: gRPC ``Capabilities`` через ``gnmic`` — тот же клиент, который потом держит поток.
- Prometheus exporter: HTTP GET, ответ должен быть в текстовом формате Prometheus.
- SNMP (UDP): ответ агента. SNMPv3 discovery не требует учётных данных; v2c GET уходит
  только с community из настроек — значения «public» по умолчанию в коде нет.
"""

from __future__ import annotations

import itertools
import os
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass

_REQUEST_IDS = itertools.count(0x1000)
_SYS_DESCR_OID = bytes.fromhex("2b06010201010100")  # 1.3.6.1.2.1.1.1.0
_H2_PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n\x00\x00\x00\x04\x00\x00\x00\x00\x00"


@dataclass(frozen=True, slots=True)
class GnmiCapabilities:
    ok: bool
    tls: bool = False  # True — TLS без проверки сертификата, False — plaintext (insecure)
    detail: str = ""


def tcp_open(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def grpc_h2_capabilities(
    host: str,
    port: int,
    username: str = "",
    password: str = "",
    *,
    timeout: float = 3.0,
) -> GnmiCapabilities | None:
    """Wire-level gRPC/HTTP2 SETTINGS frame probe when gnmic binary is unavailable."""
    _ = (username, password)
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(_H2_PREFACE)
            data = sock.recv(64)
    except TimeoutError:
        return None
    except OSError as exc:
        return GnmiCapabilities(ok=False, detail=str(exc))
    if not data:
        return GnmiCapabilities(ok=False, detail="EOF")
    if len(data) >= 9 and data[3] == 0x04 and data[5:9] == b"\x00\x00\x00\x00":
        return GnmiCapabilities(ok=True, tls=False)
    return GnmiCapabilities(ok=False, detail="non-h2")


# Учётные данные передаём через окружение (GNMIC_USERNAME/GNMIC_PASSWORD), а не argv:
# аргументы процесса видны любому пользователю хоста через ps.
def gnmic_capabilities(
    host: str,
    port: int,
    username: str,
    password: str,
    *,
    timeout: float = 5.0,
    binary: str | None = None,
) -> GnmiCapabilities | None:
    """Run ``gnmic capabilities``; ``None`` means gnmic is not installed (cannot verify)."""
    if binary is None:
        h2 = grpc_h2_capabilities(host, port, username, password, timeout=min(timeout, 2.5))
        if h2 is not None and (h2.ok or h2.detail == "EOF"):
            return h2
    exe = binary or shutil.which("gnmic")
    if not exe:
        return None
    env = {**os.environ, "GNMIC_USERNAME": username, "GNMIC_PASSWORD": password}
    base = [exe, "-a", f"{host}:{port}", "--timeout", f"{max(1, round(timeout))}s"]
    detail = ""
    for tls_flag, tls in (("--skip-verify", True), ("--insecure", False)):
        try:
            proc = subprocess.run(
                [*base, tls_flag, "capabilities"],
                capture_output=True,
                text=True,
                timeout=timeout + 3,
                env=env,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            detail = str(exc)
            continue
        out_lower = f"{proc.stdout}\n{proc.stderr}".lower()
        if (proc.returncode == 0 and "gnmi version" in out_lower) or "unauthenticated" in out_lower:
            return GnmiCapabilities(ok=True, tls=tls)
        detail = (proc.stderr or proc.stdout).strip()[-300:]
    return GnmiCapabilities(ok=False, detail=detail)


def prometheus_exporter_ok(host: str, port: int, path: str, timeout: float) -> bool:
    req = urllib.request.Request(f"http://{host}:{port}{path}", headers={"Accept": "text/plain"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return False
            head = resp.read(4096).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return "# TYPE " in head or "# HELP " in head


# --- Минимальный BER-кодировщик ровно под два SNMP-запроса -----------------------------


def _ber_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _tlv(tag: int, payload: bytes) -> bytes:
    return bytes([tag]) + _ber_len(len(payload)) + payload


def _int(value: int) -> bytes:
    return _tlv(0x02, value.to_bytes(value.bit_length() // 8 + 1, "big", signed=True))


def _octets(value: bytes) -> bytes:
    return _tlv(0x04, value)


def _seq(*parts: bytes) -> bytes:
    return _tlv(0x30, b"".join(parts))


def snmpv2c_get_sysdescr(community: bytes, request_id: int) -> bytes:
    varbind = _seq(_tlv(0x06, _SYS_DESCR_OID), b"\x05\x00")
    pdu = _tlv(0xA0, _int(request_id) + _int(0) + _int(0) + _seq(varbind))
    return _seq(_int(1), _octets(community), pdu)


# RFC 3414 §4: пустой engineID + флаг reportable — агент обязан ответить Report-PDU
# (usmStatsUnknownEngineIDs), учётные данные для этого не нужны.
def snmpv3_discovery(message_id: int) -> bytes:
    usm = _seq(_octets(b""), _int(0), _int(0), _octets(b""), _octets(b""), _octets(b""))
    pdu = _tlv(0xA0, _int(message_id) + _int(0) + _int(0) + _seq())
    header = _seq(_int(message_id), _int(65507), _octets(b"\x04"), _int(3))
    return _seq(_int(3), header, _octets(usm), _seq(_octets(b""), _octets(b""), pdu))


def _ber_read_tlv(buf: bytes, idx: int = 0) -> tuple[int, bytes, int] | None:
    if idx + 2 > len(buf):
        return None
    tag = buf[idx]
    length = buf[idx + 1]
    pos = idx + 2
    if length & 0x80:
        nbytes = length & 0x7F
        if nbytes == 0 or pos + nbytes > len(buf):
            return None
        length = int.from_bytes(buf[pos : pos + nbytes], "big")
        pos += nbytes
    if pos + length > len(buf):
        return None
    return tag, buf[pos : pos + length], pos + length


def _message_version(data: bytes) -> int | None:
    outer = _ber_read_tlv(data, 0)
    if outer is None or outer[0] != 0x30:
        return None
    ver = _ber_read_tlv(outer[1], 0)
    if ver is None or ver[0] != 0x02 or len(ver[1]) != 1:
        return None
    return ver[1][0]


def _valid_snmp_v2c_response(data: bytes) -> bool:
    """Verify SNMPv2c reply has version=1, GetResponse-PDU (0xA2), error-status=0, and non-empty value."""
    if not data or _message_version(data) != 1:
        return False
    outer = _ber_read_tlv(data, 0)
    if outer is None or outer[0] != 0x30:
        return False
    body = outer[1]
    ver = _ber_read_tlv(body, 0)
    if ver is None:
        return False
    comm = _ber_read_tlv(body, ver[2])
    if comm is None or comm[0] != 0x04 or not comm[1]:
        return False
    pdu = _ber_read_tlv(body, comm[2])
    if pdu is None or pdu[0] != 0xA2:
        return False
    pdu_body = pdu[1]
    req_id = _ber_read_tlv(pdu_body, 0)
    if req_id is None or req_id[0] != 0x02:
        return False
    err_status = _ber_read_tlv(pdu_body, req_id[2])
    if err_status is None or err_status[0] != 0x02:
        return False
    if int.from_bytes(err_status[1], "big", signed=True) != 0:
        return False
    err_index = _ber_read_tlv(pdu_body, err_status[2])
    if err_index is None or err_index[0] != 0x02:
        return False
    varbinds = _ber_read_tlv(pdu_body, err_index[2])
    if varbinds is None or varbinds[0] != 0x30 or not varbinds[1]:
        return False
    first_vb = _ber_read_tlv(varbinds[1], 0)
    if first_vb is None or first_vb[0] != 0x30 or not first_vb[1]:
        return False
    oid_tlv = _ber_read_tlv(first_vb[1], 0)
    if oid_tlv is None or oid_tlv[0] != 0x06 or not oid_tlv[1]:
        return False
    val_tlv = _ber_read_tlv(first_vb[1], oid_tlv[2])
    if val_tlv is None:
        return False
    return val_tlv[0] not in (0x05, 0x80, 0x81, 0x82) and bool(val_tlv[1])


def _record_snmp_reply(data: bytes, answered: set[str]) -> int | None:
    ver = _message_version(data)
    if ver == 1 and _valid_snmp_v2c_response(data):
        answered.add("v2c")
    elif ver == 3:
        answered.add("v3")
    return ver


def snmp_agent_versions(
    host: str, port: int, *, community: str | None, timeout: float
) -> set[str]:
    """Return SNMP versions the agent actually answered: subset of {"v3", "v2c"}."""
    probes: list[tuple[str, int, bytes]] = []
    if community:
        for comm in dict.fromkeys(c.strip() for c in community.split(",") if c.strip()):
            probes.append(("v2c", 1, snmpv2c_get_sysdescr(comm.encode(), next(_REQUEST_IDS))))
    probes.append(("v3", 3, snmpv3_discovery(next(_REQUEST_IDS))))
    answered: set[str] = set()
    # Один UDP-сокет на обе пробы: socat UDP4-RECVFROM,fork на стенде привязывает
    # сессию к эфемерному порту клиента и отбрасывает пакет со второго сокета.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
        except OSError:
            return answered
        for name, wire_version, packet in probes:
            if name in answered:
                continue
            refused = False
            for _ in range(2):
                try:
                    sock.send(packet)
                    data = sock.recv(4096)
                    ver = _record_snmp_reply(data, answered)
                    if ver is not None and ver != wire_version:
                        try:
                            extra = sock.recv(4096)
                            _record_snmp_reply(extra, answered)
                        except OSError:
                            pass
                    break
                except TimeoutError:
                    continue
                except ConnectionRefusedError:
                    refused = True
                    break
                except OSError:
                    break
            if refused:
                break  # порт закрыт — агента нет, остальные пробы бессмысленны
    return answered


