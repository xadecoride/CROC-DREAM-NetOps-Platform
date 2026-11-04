"""Internet exposure, public tunnel URL resolution, and SSH reverse-tunnel forwarding to the authenticated console (frontend:80)."""

from __future__ import annotations

import logging
import os
import select as _select
import socket
import threading
from typing import Any

from fastapi import Request

from netops.constants import const

logger = logging.getLogger(__name__)

_TUNNEL_LOCK = threading.Lock()
_ACTIVE_TUNNEL_HOST: str | None = None
_ACTIVE_TUNNEL_SSH: Any = None
_ACTIVE_PUBLIC_PORT: int = int(const("console.role_ports.admin"))


def resolve_public_host(request: Request | None, settings: Any, rt: dict[str, Any]) -> str:
    """Resolve the external/internet hostname or IP without hardcoded loopback fallbacks."""
    pub_override = str(
        rt.get("public_web_url") or getattr(settings, "public_web_url", "") or ""
    ).strip()
    if pub_override:
        clean = pub_override.split("://")[-1].split("/")[0].split(":")[0].strip()
        if clean and clean not in ("localhost", "127.0.0.1"):
            return clean

    clab_host = str(rt.get("clab_host") or getattr(settings, "clab_host", "") or "").strip()
    if clab_host and clab_host not in ("localhost", "127.0.0.1"):
        return clab_host

    if request is not None:
        req_host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").strip()
        req_hostname = req_host.split(":")[0].strip()
        if req_hostname and req_hostname not in ("localhost", "127.0.0.1"):
            return req_hostname

    return clab_host or "localhost"


def build_exposure_payload(
    request: Request | None,
    settings: Any,
    rt: dict[str, Any],
) -> dict[str, Any]:
    """Build dynamic exposure status and public tunnel URL for Owner-issued invite links and codes."""
    exp_mode = str(rt.get("exposure_mode") or "public_tunnel")
    web_port = int(
        rt.get("frontend_port")
        or getattr(settings, "frontend_port", None)
        or const("console.owner_port")
    )
    pub_override = str(
        rt.get("public_web_url") or getattr(settings, "public_web_url", "") or ""
    ).strip().rstrip("/")

    pub_host = resolve_public_host(request, settings, rt)
    has_public_host = bool(pub_host and pub_host not in ("localhost", "127.0.0.1"))

    if exp_mode != "public_tunnel":
        stop_public_role_tunnels()
    elif has_public_host and not os.environ.get("PYTEST_CURRENT_TEST"):
        ensure_public_role_tunnels_async(settings, web_port)

    tunnel_port = _ACTIVE_PUBLIC_PORT if has_public_host else web_port
    if pub_override and not any(
        h in pub_override for h in ("localhost", "127.0.0.1")
    ):
        public_base = pub_override
    elif has_public_host:
        public_base = f"http://{pub_host}:{tunnel_port}"
    else:
        public_base = None

    return {
        "active": bool(exp_mode == "public_tunnel" and (public_base or has_public_host)),
        "mode": exp_mode,
        "public_tunnel_enabled": exp_mode == "public_tunnel",
        "local_url": f"http://localhost:{web_port}",
        "public_url": public_base if exp_mode == "public_tunnel" else None,
        "public_host": pub_host if has_public_host else None,
        "websocket_enabled": False,
    }


def stop_public_role_tunnels() -> None:
    """Close active SSH reverse tunnel when switching to local_only mode."""
    global _ACTIVE_TUNNEL_HOST, _ACTIVE_TUNNEL_SSH
    with _TUNNEL_LOCK:
        ssh = _ACTIVE_TUNNEL_SSH
        _ACTIVE_TUNNEL_SSH = None
        _ACTIVE_TUNNEL_HOST = None
    if ssh is not None:
        try:
            ssh.close()
        except Exception:
            _ = None


def redact_cascade_for_caller(
    cascade: list[dict[str, Any]], *, caller_is_bound_owner: bool
) -> list[dict[str, Any]]:
    """Redact plaintext LLM API keys when bootstrap status is queried by non-Owner roles."""
    if caller_is_bound_owner:
        return cascade
    redacted: list[dict[str, Any]] = []
    for item in cascade:
        copy_item = dict(item)
        if copy_item.get("api_key"):
            copy_item["api_key"] = ""
        redacted.append(copy_item)
    return redacted


def _pipe_sockets(a: socket.socket, b: socket.socket) -> None:
    try:
        while True:
            r, _, _ = _select.select([a, b], [], [], 60.0)
            if not r:
                break
            for sock in r:
                data = sock.recv(65536)
                if not data:
                    return
                target = b if sock is a else a
                target.sendall(data)
    except Exception:
        _ = None
    finally:
        for s in (a, b):
            try:
                s.close()
            except Exception:
                _ = None


def _handle_reverse_channel(chan: Any, target_host: str, target_port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    try:
        sock.connect((target_host, target_port))
        sock.settimeout(None)
        _pipe_sockets(chan, sock)
    except Exception:
        try:
            chan.close()
        except Exception:
            _ = None
        try:
            sock.close()
        except Exception:
            _ = None


def ensure_public_role_tunnels_async(settings: Any, base_web_port: int | None = None) -> None:
    """Start background SSH reverse tunnel on clab_host forwarding to local authenticated frontend:80."""
    if base_web_port is None:
        base_web_port = int(const("console.owner_port"))
    clab_host = str(getattr(settings, "clab_host", "") or "").strip()
    clab_user = str(getattr(settings, "clab_user", "") or "").strip()
    clab_port = int(getattr(settings, "clab_port", None) or const("network.clab_ssh_port"))
    clab_pw_obj = getattr(settings, "clab_password", None)
    clab_pw = (
        clab_pw_obj.get_secret_value()
        if hasattr(clab_pw_obj, "get_secret_value")
        else str(clab_pw_obj or "")
    )
    if not clab_host or clab_host in ("localhost", "127.0.0.1") or not clab_user or not clab_pw:
        return

    global _ACTIVE_TUNNEL_HOST, _ACTIVE_TUNNEL_SSH
    stale_ssh = None
    with _TUNNEL_LOCK:
        if _ACTIVE_TUNNEL_SSH is not None:
            transport = _ACTIVE_TUNNEL_SSH.get_transport()
            if clab_host == _ACTIVE_TUNNEL_HOST and transport is not None and transport.is_active():
                return
            stale_ssh = _ACTIVE_TUNNEL_SSH
            _ACTIVE_TUNNEL_SSH = None
        _ACTIVE_TUNNEL_HOST = clab_host
    if stale_ssh is not None:
        try:
            stale_ssh.close()
        except Exception:
            _ = None

    def _worker() -> None:
        global _ACTIVE_TUNNEL_SSH, _ACTIVE_PUBLIC_PORT
        try:
            import paramiko

            ssh = paramiko.SSHClient()
            strict_ssh = os.getenv("NETOPS_SSH_STRICT_HOST_KEY", "").lower() in ("1", "true", "yes")
            ssh.set_missing_host_key_policy(paramiko.RejectPolicy() if strict_ssh else paramiko.WarningPolicy())
            if strict_ssh:
                ssh.load_system_host_keys()
            ssh.connect(
                hostname=clab_host,
                port=clab_port,
                username=clab_user,
                password=clab_pw,
                timeout=float(const("tunnel.connect_timeout_seconds")),
            )
            transport = ssh.get_transport()
            if transport is None:
                return
            transport.set_keepalive(int(const("tunnel.keepalive_seconds")))

            frontend_host = os.environ.get("NETOPS_FRONTEND_INTERNAL_HOST") or const("tunnel.frontend_internal_host")

            def _dispatch_reverse(chan: Any, _orig: Any, _srv: Any) -> None:
                # Always forward internet traffic to port 80 (authenticated session gate, never hardcoded Bearer)
                threading.Thread(
                    target=_handle_reverse_channel,
                    args=(chan, frontend_host, int(const("tunnel.frontend_internal_port"))),
                    daemon=True,
                ).start()

            bound_primary: int | None = None
            candidate_ports = [
                base_web_port + int(off) for off in const("tunnel.public_port_offsets")
            ]
            for attempt in range(2):
                for candidate_port in candidate_ports:
                    try:
                        transport.request_port_forward("", candidate_port, handler=_dispatch_reverse)
                        if bound_primary is None:
                            bound_primary = candidate_port
                    except Exception as exc:
                        logger.debug("Reverse port forward %s skipped: %s", candidate_port, exc)
                if bound_primary is not None or attempt == 1:
                    break
                # Stale sshd-session from a previous container restart may still hold 5174..5176
                try:
                    import time

                    ports_regex = "|".join(f":{p}\\b" for p in candidate_ports)
                    kill_cmd = (
                        f"pids=$(ss -tlnp 2>/dev/null | grep -E '{ports_regex}' "
                        r"| sed -n 's/.*pid=\([0-9]*\).*/\1/p' | sort -u); "
                        'for p in $pids; do [ "$p" != "$$" ] && kill -9 "$p" 2>/dev/null || true; done'
                    )
                    _, stdout, _ = ssh.exec_command(kill_cmd, timeout=3.0)
                    stdout.read()
                    time.sleep(0.5)
                except Exception:
                    _ = None

            if bound_primary is not None:
                _ACTIVE_PUBLIC_PORT = bound_primary

            with _TUNNEL_LOCK:
                _ACTIVE_TUNNEL_SSH = ssh
        except Exception as exc:
            logger.debug("Public tunnel setup skipped: %s", exc)

    threading.Thread(target=_worker, daemon=True).start()
