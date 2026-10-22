from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from pydantic import ValidationError

from netops.errors import NetOpsError
from netops.intent.models import DeviceIntent, Inventory, PlatformProfile

FABRIC_SOURCE = "<fabric>"


@dataclass(frozen=True, slots=True)
class IntentIssue:
    source: str
    location: str
    message: str
    hostname: str | None = None
    code: str = "invalid"

    def __str__(self) -> str:
        where = f"{self.source}:{self.location}" if self.location else self.source
        return f"{where}: {self.message}"


class IntentValidationError(NetOpsError):
    def __init__(self, issues: Sequence[IntentIssue]) -> None:
        super().__init__(f"Intent validation failed with {len(issues)} issue(s)")
        self.issues = tuple(issues)


def issues_from_validation_error(
    error: ValidationError, *, source: str, hostname: str | None = None
) -> list[IntentIssue]:
    return [
        IntentIssue(
            source=source,
            location=".".join(str(part) for part in item["loc"]),
            message=item["msg"],
            hostname=hostname,
            code=item["type"],
        )
        for item in error.errors(include_url=False)
    ]


def lint_fabric(
    intents: Iterable[DeviceIntent], fabric_id: str | None = None
) -> list[IntentIssue]:
    router_ids: defaultdict[str, list[str]] = defaultdict(list)
    addresses: defaultdict[str, list[tuple[str, str]]] = defaultdict(list)

    for intent in intents:
        if intent.bgp is not None:
            router_ids[str(intent.bgp.router_id)].append(intent.hostname)
        for index, interface in enumerate(intent.interfaces):
            if interface.ipv4_address is not None:
                desc_lower = (interface.description or "").lower()
                name_lower = interface.name.lower()
                ip_str = str(interface.ipv4_address.ip)
                if "anycast" in desc_lower or name_lower.startswith("vbdif"):
                    continue
                addresses[ip_str].append(
                    (intent.hostname, f"interfaces.{index}.ipv4_address")
                )

    issues: list[IntentIssue] = []
    for router_id, hostnames in sorted(router_ids.items()):
        if len(hostnames) > 1:
            issues.extend(
                IntentIssue(
                    source=FABRIC_SOURCE,
                    location="bgp.router_id",
                    message=f"Router ID {router_id} is shared by {', '.join(sorted(hostnames))}",
                    hostname=hostname,
                    code="duplicate_router_id",
                )
                for hostname in hostnames
            )
    for address, owners in sorted(addresses.items()):
        if len(owners) > 1:
            devices = ", ".join(sorted({hostname for hostname, _ in owners}))
            issues.extend(
                IntentIssue(
                    source=FABRIC_SOURCE,
                    location=location,
                    message=f"IP address {address} is assigned more than once ({devices})",
                    hostname=hostname,
                    code="duplicate_ip_address",
                )
                for hostname, location in owners
            )

    issues.extend(_audit_p2p_subnets(intents))
    return issues


def _audit_p2p_subnets(intents: Iterable[DeviceIntent]) -> list[IntentIssue]:
    subnets_31: defaultdict[str, list[str]] = defaultdict(list)
    for intent in intents:
        for interface in intent.interfaces:
            if (
                interface.ipv4_address is not None
                and interface.ipv4_address.network.prefixlen == 31
            ):
                subnets_31[str(interface.ipv4_address.network)].append(intent.hostname)

    issues: list[IntentIssue] = []
    for net, hosts in sorted(subnets_31.items()):
        if len(hosts) > 2:
            hosts_str = ", ".join(sorted(hosts))
            for h in hosts:
                issues.append(
                    IntentIssue(
                        source=FABRIC_SOURCE,
                        location="interfaces.ipv4_address",
                        message=f"Подсеть /31 {net} назначена более чем двум узлам: {hosts_str}",
                        hostname=h,
                        code="oversubscribed_p2p_subnet",
                    )
                )
    return issues


def lint_platforms(
    platforms: Mapping[str, PlatformProfile],
    inventory: Inventory | None,
    *,
    inventory_source: str = "inventory.yaml",
) -> list[IntentIssue]:
    """Check referential integrity: inventory devices must have PlatformProfile."""
    issues: list[IntentIssue] = []
    if inventory is None:
        return issues
    known_platforms = set(platforms.keys())
    for device in inventory.devices:
        plat_str = str(getattr(device.platform, "value", device.platform)).lower()
        if plat_str not in known_platforms:
            issues.append(
                IntentIssue(
                    source=inventory_source,
                    location="platform",
                    message=(
                        f"Device {device.hostname!r} references unknown or missing "
                        f"platform profile {plat_str!r}"
                    ),
                    hostname=device.hostname,
                    code="missing_platform_profile",
                )
            )
    return issues


