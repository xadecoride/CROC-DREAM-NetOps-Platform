"""Live network driver for Containerlab using Scrapli (spec 2.4, 2.6).

Connects over SSH to Cisco IOS-XE and Arista EOS nodes, runs commands,
stages changes inside transaction sessions, and takes operational health snapshots.
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
from collections.abc import Mapping, Sequence

from netops.enums import Platform
from netops.network.base import (
    BgpSessionState,
    ChangePlan,
    DeviceTarget,
    FetchResult,
    HealthExpectations,
    HealthSnapshot,
    InterfaceState,
)

logger = logging.getLogger(__name__)


class ScrapliNetworkDriver:
    """Implements ConfigCollector, ConfigDeployer, and HealthProbe via Scrapli."""

    def __init__(self, max_workers: int = 4, timeout_socket: int = 15) -> None:
        self.max_workers = max_workers
        self.timeout_socket = timeout_socket

    def _get_connection(self, target: DeviceTarget):
        try:
            from scrapli.driver.core import EOSDriver, IOSXEDriver
        except ImportError:
            raise RuntimeError("Scrapli is not installed in the environment.") from None

        driver_cls = IOSXEDriver if target.platform == Platform.CISCO_IOSXE else EOSDriver
        creds = target.credentials
        return driver_cls(
            host=target.host,
            port=target.port,
            auth_username=creds.username,
            auth_password=getattr(creds, "password", ""),
            auth_strict_key=False,
            transport="system",
            timeout_socket=self.timeout_socket,
        )

    def fetch_running_configs(self, targets: Sequence[DeviceTarget]) -> Mapping[str, FetchResult]:
        results: dict[str, FetchResult] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_host = {
                executor.submit(self._fetch_single, target): target.hostname
                for target in targets
            }
            for future in concurrent.futures.as_completed(future_to_host):
                hostname = future_to_host[future]
                try:
                    config = future.result()
                    results[hostname] = FetchResult.success(config)
                except Exception as exc:
                    logger.warning("Failed to collect running-config from %s: %s", hostname, exc)
                    results[hostname] = FetchResult.failure(str(exc))
        return results

    def _fetch_single(self, target: DeviceTarget) -> str:
        cmd = "show running-config"
        with self._get_connection(target) as conn:
            response = conn.send_command(cmd)
            if response.failed:
                raise RuntimeError(f"Command '{cmd}' failed: {response.result}")
            return response.result

    def apply(self, target: DeviceTarget, plan: ChangePlan, *, confirm_timeout: int) -> None:
        lines = [line.strip() for line in plan.remediation.splitlines() if line.strip() and not line.startswith("!")]
        if not lines:
            return

        with self._get_connection(target) as conn:
            if target.platform == Platform.CISCO_IOSXE:
                conn.send_configs(lines)
                conn.send_command(f"commit confirmed {confirm_timeout}")
            elif target.platform == Platform.ARISTA_EOS:
                session_name = "NETOPS_DEPLOY"
                conn.send_command(f"configure session {session_name}")
                conn.send_configs(lines)
                conn.send_command(f"commit timer {confirm_timeout}")

    def confirm(self, target: DeviceTarget) -> None:
        with self._get_connection(target) as conn:
            if target.platform == Platform.CISCO_IOSXE:
                conn.send_command("commit")
            elif target.platform == Platform.ARISTA_EOS:
                conn.send_command("configure session NETOPS_DEPLOY")
                conn.send_command("commit")

    def rollback(self, target: DeviceTarget, plan: ChangePlan) -> None:
        with self._get_connection(target) as conn:
            if target.platform == Platform.CISCO_IOSXE:
                conn.send_command("abort")
            elif target.platform == Platform.ARISTA_EOS:
                conn.send_command("configure session NETOPS_DEPLOY")
                conn.send_command("abort")

            rollback_lines = [
                line.strip() for line in plan.rollback.splitlines() if line.strip() and not line.startswith("!")
            ]
            if rollback_lines:
                conn.send_configs(rollback_lines)

    def snapshot(self, target: DeviceTarget, expected: HealthExpectations | None) -> HealthSnapshot:
        bgp_sessions: dict[str, BgpSessionState] = {}
        interfaces: dict[str, InterfaceState] = {}
        ping_losses: dict[str, float] = {}

        with self._get_connection(target) as conn:
            bgp_out = conn.send_command("show ip bgp summary").result
            for line in bgp_out.splitlines():
                match = re.search(r"^(\d+\.\d+\.\d+\.\d+)\s+.*?\s+(\d+|Active|Idle|Connect)$", line.strip())
                if match:
                    peer_ip, state_or_pfx = match.groups()
                    if state_or_pfx.isdigit():
                        bgp_sessions[peer_ip] = BgpSessionState("Established", prefixes_accepted=int(state_or_pfx))
                    else:
                        bgp_sessions[peer_ip] = BgpSessionState(state_or_pfx, prefixes_accepted=0)

            int_out = conn.send_command("show ip interface brief").result
            for line in int_out.splitlines():
                parts = line.split()
                if target.platform == Platform.CISCO_IOSXE and len(parts) >= 6:
                    if parts[0].startswith("Gigabit") or parts[0].startswith("Loop"):
                        interfaces[parts[0]] = InterfaceState(status=parts[4], protocol=parts[5])
                elif target.platform == Platform.ARISTA_EOS and len(parts) >= 4:
                    if parts[0].startswith("Ethernet") or parts[0].startswith("Loop"):
                        status = "up" if "up" in parts[1].lower() else "down"
                        proto = "up" if "up" in parts[2].lower() else "down"
                        interfaces[parts[0]] = InterfaceState(status=status, protocol=proto)

            if expected:
                for peer in expected.bgp_peers:
                    ping_cmd = (
                        f"ping {peer} repeat 5"
                        if target.platform == Platform.CISCO_IOSXE
                        else f"ping {peer} count 5"
                    )
                    ping_out = conn.send_command(ping_cmd).result
                    rate_match = re.search(r"Success rate is (\d+) percent", ping_out)
                    ping_losses[peer] = (100.0 - float(rate_match.group(1))) if rate_match else 0.0

        return HealthSnapshot(
            bgp_sessions=bgp_sessions,
            interfaces=interfaces,
            ping_loss_percent=ping_losses,
        )
