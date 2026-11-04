from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from netops.enums import Platform
from netops.intent.models import DeviceIntent, InventoryDevice


@dataclass(frozen=True, slots=True)
class Credentials:
    username: str
    password: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class DeviceTarget:
    hostname: str
    platform: Platform
    host: str
    port: int
    credentials: Credentials = field(repr=False)
    proxy_jump: str | None = None


@dataclass(frozen=True, slots=True)
class FetchResult:
    config: str | None = None
    error: str | None = None

    @classmethod
    def success(cls, config: str) -> FetchResult:
        return cls(config=config)

    @classmethod
    def failure(cls, error: str) -> FetchResult:
        return cls(error=error)


@dataclass(frozen=True, slots=True)
class ChangePlan:
    remediation: str
    rollback: str
    intended: str


@dataclass(frozen=True, slots=True)
class ConfigDiff:
    remediation: str
    rollback: str
    unauthorized_lines: tuple[str, ...] = ()
    missing_lines: tuple[str, ...] = ()

    @property
    def has_changes(self) -> bool:
        return bool(self.remediation.strip())

    @property
    def in_sync(self) -> bool:
        return not (self.has_changes or self.unauthorized_lines or self.missing_lines)


@dataclass(frozen=True, slots=True)
class BgpSessionState:
    state: str
    prefixes_accepted: int | None = None

    @property
    def established(self) -> bool:
        return self.state.strip().lower() == "established"


@dataclass(frozen=True, slots=True)
class InterfaceState:
    status: str
    protocol: str

    @property
    def is_up(self) -> bool:
        return self.status.strip().lower() == "up" and self.protocol.strip().lower() == "up"

    def __str__(self) -> str:
        return f"{self.status}/{self.protocol}"


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    bgp_sessions: Mapping[str, BgpSessionState] = field(default_factory=dict)
    interfaces: Mapping[str, InterfaceState] = field(default_factory=dict)
    ping_loss_percent: Mapping[str, float] = field(default_factory=dict)
    cpu_pct: float | None = None
    memory_pct: float | None = None
    uplink_util_pct: float | None = None
    disk_util_pct: float | None = None
    network_rate_mbps: float | None = None
    load_1m: float | None = None
    interface_errors_rate: float | None = None
    fdb_mac_count: float | None = None
    tcp_retransmits_rate: float | None = None
    conntrack_usage_pct: float | None = None


@dataclass(frozen=True, slots=True)
class HealthExpectations:
    bgp_peers: tuple[str, ...] = ()
    interfaces: Mapping[str, bool] = field(default_factory=dict)

    @classmethod
    def from_intent(cls, intent: DeviceIntent) -> HealthExpectations:
        peers = tuple(str(n.peer_ip) for n in intent.bgp.neighbors) if intent.bgp else ()
        return cls(
            bgp_peers=peers,
            interfaces={interface.name: interface.enabled for interface in intent.interfaces},
        )

    def to_json(self) -> dict[str, Any]:
        return {"bgp_peers": list(self.bgp_peers), "interfaces": dict(self.interfaces)}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> HealthExpectations:
        return cls(
            bgp_peers=tuple(data.get("bgp_peers", ())),
            interfaces=dict(data.get("interfaces", {})),
        )


@runtime_checkable
class ConfigRenderer(Protocol):
    def render(self, device: InventoryDevice, intent: DeviceIntent) -> str: ...


@runtime_checkable
class DiffEngine(Protocol):
    def compare(self, platform: Platform, running: str, intended: str) -> ConfigDiff: ...


@runtime_checkable
class ConfigCollector(Protocol):
    # Ошибку по отдельному устройству возвращать как FetchResult.failure, а не исключением.
    def fetch_running_configs(
        self, targets: Sequence[DeviceTarget]
    ) -> Mapping[str, FetchResult]: ...


# apply: Cisco IOS-XE — commit confirmed <timeout>, Arista EOS — configure session
# с commit timer. confirm фиксирует изменение, rollback отменяет сессию или накатывает
# plan.rollback.
@runtime_checkable
class ConfigDeployer(Protocol):
    def apply(self, target: DeviceTarget, plan: ChangePlan, *, confirm_timeout: int) -> None: ...

    def confirm(self, target: DeviceTarget) -> None: ...

    def rollback(self, target: DeviceTarget, plan: ChangePlan) -> None: ...


# show ip bgp summary, show ip interface brief и ping (5 пакетов) до expected.bgp_peers;
# expected равен None, если intent неизвестен.
@runtime_checkable
class HealthProbe(Protocol):
    def snapshot(
        self, target: DeviceTarget, expected: HealthExpectations | None
    ) -> HealthSnapshot: ...
