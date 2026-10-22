"""SSH session pool, jump-host connection helpers, and shell execution for ScrapliNetworkDriver."""

from __future__ import annotations

import contextlib
import ipaddress
import logging
import os
import re
import socket
import threading
import time
from collections.abc import Sequence
from typing import Any

from netops.constants import const, unset_password_marker
from netops.enums import Platform
from netops.network.ssh_locks import (
    SSHLockUnavailableError,
    _DistributedDeviceLock,
    _get_device_lock,
    get_jump_lock,
)

_SHARED_JUMP_CLIENTS: dict[tuple[str, int, str], Any] = {}
_DEAD_ENDPOINTS: dict[tuple[str, int], float] = {}
_POOL_GUARD = threading.Lock()
_SESSION_POOL: dict[tuple[str, int], dict[str, object]] = {}
_LAST_CLI_METRICS_TS: dict[str, float] = {}
_SESSION_IDLE_TTL = float(const("network.ssh_session_idle_ttl_seconds"))
_SESSION_POOL_MAX = int(const("network.ssh_session_pool_max"))
_CLI_METRICS_DEDUP_SEC = 12.0

__all__ = [
    "SSHLockUnavailableError",
    "_DistributedDeviceLock",
    "_get_device_lock",
    "sweep_session_pool",
]



def _close_ssh_objects(
    shell: Any, client: Any, jump_client: Any, platform: Platform | None = None
) -> None:
    if shell is not None:
        with contextlib.suppress(Exception):
            cmd = "return\nquit\ny\n" if platform == Platform.HUAWEI_VRP else "exit\n"
            shell.send(cmd)
            time.sleep(0.05)
            shell.close()
    if client is not None:
        with contextlib.suppress(Exception):
            client.close()
    if jump_client is not None and jump_client not in list(_SHARED_JUMP_CLIENTS.values()):
        with contextlib.suppress(Exception):
            jump_client.close()


def _close_pooled_session(host: str, port: int) -> None:
    with _POOL_GUARD:
        entry = _SESSION_POOL.pop((host, port), None)
    if not entry:
        return
    _close_ssh_objects(
        entry.get("shell"),
        entry.get("client"),
        entry.get("jump_client"),
        entry.get("platform"),  # type: ignore[arg-type]
    )


def sweep_session_pool() -> None:
    """Reap pooled sessions idle > TTL and evict LRU entries above the configured cap."""
    now = time.monotonic()
    with _POOL_GUARD:
        by_age = sorted(_SESSION_POOL, key=lambda k: float(_SESSION_POOL[k].get("last_used") or 0.0))
        victims = [
            k for k in by_age if now - float(_SESSION_POOL[k].get("last_used") or 0.0) > _SESSION_IDLE_TTL
        ]
        rest = [k for k in by_age if k not in victims]
        victims += rest[: max(0, len(rest) - _SESSION_POOL_MAX)]
    for host, port in victims:
        with _get_device_lock(host, port).try_exclusive() as free:
            if free:  # never close a session another thread is using
                _close_pooled_session(host, port)


def _prune_dead_jump_clients() -> None:
    for key, client in list(_SHARED_JUMP_CLIENTS.items()):
        with contextlib.suppress(Exception):
            transport = client.get_transport()
            if transport is not None and transport.is_active():
                continue
        if _SHARED_JUMP_CLIENTS.get(key) is client:
            _SHARED_JUMP_CLIENTS.pop(key, None)
        _close_quietly(client)


def _check_tcp_endpoint(host: str, port: int, timeout: float = 2.5) -> bool:
    if "PYTEST_CURRENT_TEST" in os.environ:
        return True
    dead_ts = _DEAD_ENDPOINTS.get((host, port))
    if dead_ts is not None and (time.monotonic() - dead_ts) < 8.0:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            if port == 22:
                sock.settimeout(min(timeout, 1.5))
                if not sock.recv(64).startswith(b"SSH-"):
                    _DEAD_ENDPOINTS[(host, port)] = time.monotonic()
                    return False
            _DEAD_ENDPOINTS.pop((host, port), None)
            return True
    except OSError:
        _DEAD_ENDPOINTS[(host, port)] = time.monotonic()
        return False


def resolve_clab_jump_defaults(stand_slug: str | None = None) -> tuple[str, str, int, str | None]:
    if stand_slug:
        with contextlib.suppress(Exception):
            from netops.services.stands import resolve_stand_connection

            info = resolve_stand_connection(stand_slug)
            if info.get("jump_host"):
                return (
                    str(info.get("jump_user") or "crocdream"),
                    str(info["jump_host"]),
                    int(info.get("jump_port") or 221),
                    info.get("jump_password"),
                )
    user, host = os.getenv("NETOPS_CLAB_USER", ""), os.getenv("NETOPS_CLAB_HOST", "")
    port_raw, pwd = os.getenv("NETOPS_CLAB_PORT", ""), os.getenv("NETOPS_CLAB_PASSWORD", "") or None
    if not host and "PYTEST_CURRENT_TEST" not in os.environ:
        with contextlib.suppress(Exception):
            from netops.services.stands import resolve_stand_connection

            info = resolve_stand_connection(None)
            host = str(info.get("jump_host") or "")
            user = user or str(info.get("jump_user") or "crocdream")
            port_raw = port_raw or str(info.get("jump_port") or 221)
            pwd = pwd or info.get("jump_password")
    return user or "crocdream", host, int(port_raw) if str(port_raw).isdigit() else 221, pwd


def resolve_container_endpoint(
    host: str, dest_host: str, dest_port: int, stand_slug: str | None = None
) -> tuple[str, int]:
    if dest_host == host and (stand_slug or "PYTEST_CURRENT_TEST" not in os.environ):
        with contextlib.suppress(Exception):
            from netops.services.stands import resolve_stand_connection
            from netops.services.state import get_state_manager

            info = resolve_stand_connection(stand_slug or host)
            pmap = info.get("port_map")
            if isinstance(pmap, dict) and str(dest_port) in pmap:
                return str(pmap[str(dest_port)]), 22
            rt_data = get_state_manager().get_db_state("runtime_settings", {})
            if isinstance(rt_data, dict):
                port_map = rt_data.get("clab_port_map")
                if isinstance(port_map, dict) and str(dest_port) in port_map:
                    return str(port_map[str(dest_port)]), 22
    return ("127.0.0.1" if dest_host == host else dest_host), dest_port


def build_candidate_passwords(platform_value: str, password: str) -> list[str]:
    from netops.constants import fallback_password

    candidate_passwords: list[str] = []
    if password and password != unset_password_marker():
        candidate_passwords.append(password)
    with contextlib.suppress(Exception):
        from netops.services.discovery import load_platform_profiles

        plat_prof = load_platform_profiles().get(platform_value)
        own_default = str(plat_prof.get("default_password") or "") if plat_prof else ""
        if own_default and own_default not in candidate_passwords:
            candidate_passwords.append(own_default)
    fb = fallback_password()
    if fb and fb not in candidate_passwords:
        candidate_passwords.append(fb)
    if not candidate_passwords and password:
        candidate_passwords.append(password)
    return candidate_passwords


def _new_ssh_client(paramiko_mod: Any) -> Any:
    client = paramiko_mod.SSHClient()
    if os.getenv("NETOPS_SSH_STRICT_HOST_KEY", "").lower() in ("1", "true", "yes"):
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko_mod.RejectPolicy())
    else:
        client.set_missing_host_key_policy(paramiko_mod.WarningPolicy())
    return client


def _close_quietly(*objs: Any) -> None:
    for obj in objs:
        if obj is not None:
            with contextlib.suppress(Exception):
                obj.close()


def open_jump_channel(
    adapter: Any, paramiko_mod: Any, jump_spec: str, dest_host: str, dest_port: int
) -> Any:
    spec = (jump_spec or "").strip()
    stand_slug = spec[6:].strip() if spec.startswith("stand:") else None
    user, host, port, jump_pwd = resolve_clab_jump_defaults(stand_slug or dest_host)
    if spec and spec != "auto" and not stand_slug:
        if "@" in spec:
            u_part, spec = spec.split("@", 1)
            user, jump_pwd = u_part.split(":", 1) if ":" in u_part else (u_part, jump_pwd)
        if ":" in spec:
            h_part, p_part = spec.rsplit(":", 1)
            host, port = h_part, int(p_part) if p_part.isdigit() else port
        else:
            host = spec

    target_ip, target_port = resolve_container_endpoint(host, dest_host, dest_port, stand_slug)
    if not host:
        raise TimeoutError("Jump host is not configured")

    jump_key = (host, port, user)
    use_shared_jump = "PYTEST_CURRENT_TEST" not in os.environ

    _prune_dead_jump_clients()
    with get_jump_lock(jump_key):
        chan = _open_jump_channel_locked(
            adapter, paramiko_mod, jump_key, jump_pwd, use_shared_jump, target_ip, target_port
        )
    time.sleep(0.08)
    return chan


def _open_jump_channel_locked(
    adapter: Any,
    paramiko_mod: Any,
    jump_key: tuple[str, int, str],
    jump_pwd: str | None,
    use_shared_jump: bool,
    target_ip: str,
    target_port: int,
) -> Any:
    host, port, user = jump_key
    existing_jump = _SHARED_JUMP_CLIENTS.get(jump_key) if use_shared_jump else adapter._jump_client
    if existing_jump is not None:
        t_existing = existing_jump.get_transport()
        if t_existing is not None and t_existing.is_active():
            with contextlib.suppress(Exception):
                chan = t_existing.open_channel(
                    "direct-tcpip", (target_ip, target_port), ("127.0.0.1", 0)
                )
                adapter._jump_client = existing_jump
                return chan
        _close_quietly(existing_jump)
        if use_shared_jump:
            _SHARED_JUMP_CLIENTS.pop(jump_key, None)
        adapter._jump_client = None

    jump = _new_ssh_client(paramiko_mod)
    key_path = os.path.expanduser(os.getenv("NETOPS_CLAB_KEY_PATH", "~/.ssh/id_ed25519"))
    key_file = key_path if os.path.isfile(key_path) else None
    jump.connect(
        host, port=port, username=user, password=jump_pwd, key_filename=key_file,
        look_for_keys=not bool(jump_pwd), allow_agent=not bool(jump_pwd),
        timeout=min(adapter.timeout, 8), banner_timeout=15, auth_timeout=12,
    )
    try:
        transport = jump.get_transport()
        if transport is None:
            raise TimeoutError("Jump host SSH transport is not active")
        transport.set_keepalive(25)
        chan = transport.open_channel("direct-tcpip", (target_ip, target_port), ("127.0.0.1", 0))
    except Exception:
        adapter._jump_client = None
        _close_quietly(jump)
        raise
    if use_shared_jump:
        _SHARED_JUMP_CLIENTS[jump_key] = jump
    adapter._jump_client = jump
    return chan


def connect_with_sock(
    adapter: Any, paramiko_mod: Any, username: str, password: str, sock: Any = None
) -> None:
    candidate_passwords = build_candidate_passwords(adapter.target.platform.value, password)
    last_err: Exception | None = None
    auth_errors = (
        paramiko_mod.ssh_exception.AuthenticationException,
        paramiko_mod.ssh_exception.SSHException,
        EOFError,
    )
    for idx, cand_pw in enumerate(candidate_passwords):
        current_sock = sock if idx == 0 else None
        if idx > 0 and sock is not None:
            time.sleep(0.35)
            current_sock = open_jump_channel(
                adapter, paramiko_mod, adapter.proxy_jump or "auto", adapter.target.host, adapter.target.port
            )
        try:
            auth_exc_cls = paramiko_mod.ssh_exception.AuthenticationException
            for attempt in range(2):
                try:
                    adapter.client.connect(
                        adapter.target.host, port=adapter.target.port, username=username,
                        password=cand_pw, sock=current_sock, look_for_keys=False, allow_agent=False,
                        timeout=min(adapter.timeout, 12), banner_timeout=15, auth_timeout=12,
                    )
                    break
                except (paramiko_mod.ssh_exception.SSHException, EOFError) as transient:
                    if (
                        attempt == 1
                        or current_sock is not None
                        or isinstance(
                            transient,
                            (auth_exc_cls, paramiko_mod.ssh_exception.BadAuthenticationType),
                        )
                    ):
                        raise
                    _close_quietly(adapter.client)
                    time.sleep(0.4)
            adapter.authenticated_password = cand_pw
            return
        except paramiko_mod.ssh_exception.BadAuthenticationType as bad_auth_exc:
            last_err = bad_auth_exc
            transport = None
            try:
                transport = adapter.client.get_transport()
                if transport is None or not transport.is_active():
                    if current_sock is not None:
                        time.sleep(0.25)
                        current_sock = open_jump_channel(
                            paramiko_mod, adapter.proxy_jump or "auto", adapter.target.host, adapter.target.port
                        )
                        transport = paramiko_mod.Transport(current_sock)
                    else:
                        transport = paramiko_mod.Transport((adapter.target.host, adapter.target.port))
                    adapter.client._transport = transport
                    transport.start_client(timeout=min(adapter.timeout, 12))
                transport.auth_interactive(
                    username, lambda _t, _i, prompts, pw=cand_pw: [pw for _ in prompts]
                )
                adapter.authenticated_password = cand_pw
                return
            except auth_errors as interactive_exc:
                last_err = interactive_exc
                _close_quietly(current_sock, adapter.client)
                continue
            except Exception:
                _close_quietly(transport, current_sock, adapter.client)
                raise
        except auth_errors as auth_exc:
            last_err = auth_exc
            _close_quietly(current_sock, adapter.client)
            continue
    if last_err is not None:
        raise last_err


def establish_fresh_session(adapter: Any) -> None:
    import paramiko

    logging.getLogger("paramiko").setLevel(logging.CRITICAL)
    adapter.client = _new_ssh_client(paramiko)
    password = getattr(adapter.target.credentials, "password", "")
    username = adapter.target.credentials.username
    jump_user, clab_host, clab_port, jump_pwd = resolve_clab_jump_defaults()
    if (
        clab_host
        and adapter.target.host == clab_host
        and int(adapter.target.port) == int(clab_port)
        and "PYTEST_CURRENT_TEST" not in os.environ
    ):
        if jump_user and (not username or username == "admin"):
            username = jump_user
        if jump_pwd and (not password or password == "admin"):
            password = jump_pwd
    is_internal_docker_ip = False
    if clab_host and adapter.target.host != clab_host:
        with contextlib.suppress(ValueError):
            is_internal_docker_ip = ipaddress.ip_address(
                adapter.target.host
            ) in ipaddress.ip_network("172.16.0.0/12")
    is_unrouted_docker_target = bool(
        clab_host and is_internal_docker_ip and "PYTEST_CURRENT_TEST" not in os.environ
    )

    try:
        sock = None
        if adapter.proxy_jump:
            sock = open_jump_channel(
                adapter, paramiko, adapter.proxy_jump, adapter.target.host, adapter.target.port
            )
        elif is_unrouted_docker_target and (
            clab_host not in ("127.0.0.1", "localhost", "::1")
            or not _check_tcp_endpoint(adapter.target.host, adapter.target.port, timeout=1.2)
        ):
            sock = open_jump_channel(
                adapter, paramiko, "auto", adapter.target.host, adapter.target.port
            )
        connect_with_sock(adapter, paramiko, username, password, sock=sock)

        adapter.shell = adapter.client.invoke_shell()
        with contextlib.suppress(Exception):
            adapter.shell.settimeout(float(getattr(adapter, "timeout", 15.0) or 15.0))
        time.sleep(0.8)
        while adapter.shell.recv_ready():
            _ = adapter.shell.recv(8192)

        sent_paging_init = False
        if adapter.target.platform in (Platform.ARISTA_EOS, Platform.CISCO_IOSXE):
            adapter.shell.send("enable\nterminal length 0\n")
            sent_paging_init = True
        elif adapter.target.platform == Platform.HUAWEI_VRP:
            adapter.shell.send("screen-length 0 temporary\n")
            sent_paging_init = True

        if sent_paging_init:
            init_deadline = time.time() + 4.0
            init_buf = ""
            while time.time() < init_deadline:
                if adapter.shell.recv_ready():
                    init_buf += adapter.shell.recv(8192).decode("utf-8", errors="ignore")
                    time.sleep(0.1)
                elif ("length 0" in init_buf) and init_buf.rstrip().endswith(("#", ">", "]")):
                    break
                else:
                    time.sleep(0.15)
    except Exception:
        _close_ssh_objects(None, adapter.client, adapter._jump_client)
        raise


def execute_shell_command(adapter: Any, cmd: str) -> str:
    if adapter.shell is None:
        raise RuntimeError("SSH shell session is not initialized")
    try:
        while adapter.shell.recv_ready():
            _ = adapter.shell.recv(16384)
        adapter.shell.send(f"{cmd}\n")
    except Exception:
        adapter._invalidate_pool = True
        raise
    time.sleep(0.15)
    output = ""
    cmd_prefix = cmd.strip()[:16]
    deadline = time.time() + adapter.timeout
    last_recv_at = time.time()
    while time.time() < deadline:
        if adapter.shell.recv_ready():
            output += adapter.shell.recv(16384).decode("utf-8", errors="ignore")
            last_recv_at = time.time()
            time.sleep(0.05)
        elif output and (not cmd_prefix or cmd_prefix in output):
            stripped = output.rstrip()
            last_line = stripped.rsplit("\n", 1)[-1].strip()
            has_body = "\n" in stripped
            if adapter.target.platform == Platform.HUAWEI_VRP:
                is_prompt = has_body and (
                    (last_line.startswith("<") and last_line.endswith(">"))
                    or (last_line.startswith("[") and last_line.endswith("]"))
                )
            else:
                is_prompt = (
                    has_body
                    and last_line != "#"
                    and len(last_line) > 1
                    and last_line.endswith(("#", ">", "]", "$", "%"))
                )
            if is_prompt:
                time.sleep(0.05)
                if not adapter.shell.recv_ready():
                    break
                continue
            if has_body and (time.time() - last_recv_at) >= 2.5:
                break
            time.sleep(0.1)
        else:
            time.sleep(0.1)
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", output)


def execute_shell_configs(adapter: Any, lines: Sequence[str]) -> str:
    if adapter.shell is None:
        raise RuntimeError("SSH shell session is not initialized")

    if adapter.target.platform == Platform.HUAWEI_VRP:
        adapter.shell.send("system-view\n")
        time.sleep(0.2)
    elif adapter.target.platform != Platform.ARISTA_EOS:
        adapter.shell.send("configure terminal\n")
        time.sleep(0.2)
    for line in lines:
        adapter.shell.send(f"{line}\n")
        time.sleep(0.08)
    if adapter.target.platform == Platform.HUAWEI_VRP:
        adapter.shell.send("commit\nreturn\n")
    elif adapter.target.platform != Platform.ARISTA_EOS:
        adapter.shell.send("end\n")

    time.sleep(0.5)
    output = ""
    while adapter.shell.recv_ready():
        output += adapter.shell.recv(16384).decode("utf-8", errors="ignore")
        time.sleep(0.08)
    return output
