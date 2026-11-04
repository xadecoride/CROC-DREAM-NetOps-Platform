from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from ipaddress import IPv4Address, IPv4Interface, IPv4Network
from itertools import combinations
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    PlainValidator,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from netops.enums import DeviceRole, DeviceStatus, Platform

_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"

Hostname = Annotated[str, StringConstraints(max_length=253, pattern=rf"^{_LABEL}(?:\.{_LABEL})*$")]
Asn = Annotated[int, Field(ge=1, le=4_294_967_295, description="Номер AS (2 или 4 байта)")]
Identifier = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[\w.\-]+$")]
InterfaceName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z][\w./:\-]*(?: [\w./:\-]+)?$"),
]
Description = Annotated[str, StringConstraints(max_length=240, strip_whitespace=True)]
PortNumber = Annotated[int, Field(ge=1, le=65535, description="TCP/UDP port number (1-65535)")]


class IntentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def get(self, key: str, default: Any = None) -> Any:
        if key in type(self).model_fields:
            val = getattr(self, key)
            return default if val is None else val
        return default

    def __getitem__(self, key: str) -> Any:
        if key in type(self).model_fields:
            return getattr(self, key)
        raise KeyError(key) from None


class InterfaceMode(StrEnum):
    L3 = "l3"
    L2 = "l2"


class InterfaceIntent(IntentModel):
    name: InterfaceName
    description: Description | None = None
    enabled: bool = True
    mode: InterfaceMode = InterfaceMode.L3
    ipv4_address: IPv4Interface | None = Field(
        default=None, description="Адрес с маской, например 10.10.0.1/31"
    )
    mtu: int = Field(default=1500, ge=68, le=9216)
    # Входящий ACL на интерфейсе: имя из блока acls этого же устройства.
    acl_in: Identifier | None = None
    access_vlan: int | None = Field(default=None, ge=1, le=4094)
    ospf_area: str | int | None = None

    @field_validator("ipv4_address", mode="before")
    @classmethod
    def _require_prefix_length(cls, value: object) -> object:
        if isinstance(value, str) and "/" not in value:
            raise PydanticCustomError(
                "missing_prefix_length",
                "The address must include a prefix length, e.g. 10.0.0.1/31",
            )
        return value

    @field_validator("ipv4_address")
    @classmethod
    def _check_address(
        cls, value: IPv4Interface | None, info: ValidationInfo
    ) -> IPv4Interface | None:
        if value is None:
            return None
        if info.data.get("mode") is InterfaceMode.L2:
            raise PydanticCustomError(
                "l2_interface_address", "An L2 (switched) interface cannot have an IPv4 address"
            )
        network = value.network
        # В /31 и /32 нет адреса сети и broadcast (RFC 3021).
        if network.prefixlen < 31 and value.ip in (
            network.network_address,
            network.broadcast_address,
        ):
            raise PydanticCustomError(
                "reserved_address",
                "{address} is the network or broadcast address of {network}",
                {"address": str(value.ip), "network": str(network)},
            )
        return value


class BgpNeighborIntent(IntentModel):
    peer_ip: IPv4Address
    remote_asn: Asn
    description: Description | None = None
    # MD5-пароль не попадает в ответы API и в логи.
    password: str | None = Field(
        default=None, min_length=1, max_length=80, repr=False, exclude=True
    )
    announced_prefixes: list[IPv4Network] = Field(default_factory=list)


class BgpIntent(IntentModel):
    asn: Asn
    router_id: IPv4Address
    neighbors: list[BgpNeighborIntent] = Field(default_factory=list)
    evpn: bool = False
    vxlan_vni: int | None = Field(default=None, ge=1, le=16777215)

    @field_validator("router_id")
    @classmethod
    def _check_router_id(cls, value: IPv4Address) -> IPv4Address:
        if value.is_unspecified or value.is_multicast:
            raise PydanticCustomError("invalid_router_id", "Router ID must be a unicast address")
        return value

    @field_validator("neighbors")
    @classmethod
    def _unique_peers(cls, neighbors: list[BgpNeighborIntent]) -> list[BgpNeighborIntent]:
        _ensure_unique((str(n.peer_ip) for n in neighbors), "duplicate_neighbor", "BGP neighbor")
        return neighbors


class AclAction(StrEnum):
    PERMIT = "permit"
    DENY = "deny"


class AclProtocol(StrEnum):
    IP = "ip"
    TCP = "tcp"
    UDP = "udp"
    ICMP = "icmp"
    OSPF = "ospf"


def _parse_acl_endpoint(value: object) -> Literal["any"] | IPv4Network:
    if isinstance(value, IPv4Network):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.lower() == "any":
            return "any"
        try:
            return IPv4Network(text)
        except ValueError as exc:
            raise PydanticCustomError(
                "acl_endpoint",
                "Expected 'any' or an IPv4 network: {reason}",
                {"reason": str(exc)},
            ) from exc
    raise PydanticCustomError("acl_endpoint", "Expected 'any' or an IPv4 network")


AclEndpoint = Annotated[
    Literal["any"] | IPv4Network,
    PlainValidator(_parse_acl_endpoint, json_schema_input_type=str),
    PlainSerializer(str, return_type=str),
]


class AclRule(IntentModel):
    sequence: int = Field(ge=1, le=4_294_967_295)
    action: AclAction
    protocol: AclProtocol
    source: AclEndpoint = "any"
    destination: AclEndpoint = "any"
    src_port: str | int | None = None
    dst_port: str | int | None = None


class AclIntent(IntentModel):
    name: Identifier
    type: str | None = None
    rules: list[AclRule] = Field(min_length=1)

    @field_validator("rules")
    @classmethod
    def _ordered_unique_sequences(cls, rules: list[AclRule]) -> list[AclRule]:
        _ensure_unique((str(r.sequence) for r in rules), "duplicate_sequence", "ACL sequence")
        return sorted(rules, key=lambda rule: rule.sequence)


class DeviceIntent(IntentModel):
    hostname: Hostname
    interfaces: list[InterfaceIntent] = Field(default_factory=list)
    bgp: BgpIntent | None = None
    acls: list[AclIntent] = Field(default_factory=list)

    @field_validator("interfaces")
    @classmethod
    def _check_interfaces(cls, interfaces: list[InterfaceIntent]) -> list[InterfaceIntent]:
        _ensure_unique((i.name.lower() for i in interfaces), "duplicate_interface", "Interface")
        addressed = [(i.name, i.ipv4_address) for i in interfaces if i.ipv4_address is not None]
        for (first, first_address), (second, second_address) in combinations(addressed, 2):
            if first_address.network.overlaps(second_address.network):
                raise PydanticCustomError(
                    "overlapping_subnets",
                    "{first} ({first_address}) overlaps with {second} ({second_address})",
                    {
                        "first": first,
                        "first_address": str(first_address),
                        "second": second,
                        "second_address": str(second_address),
                    },
                )
        return interfaces

    @field_validator("acls")
    @classmethod
    def _unique_acl_names(cls, acls: list[AclIntent]) -> list[AclIntent]:
        _ensure_unique((a.name for a in acls), "duplicate_acl", "ACL")
        return acls

    # Опечатку в имени ACL лучше поймать здесь, чем получить патч, снимающий фильтр.
    @model_validator(mode="after")
    def _known_acl_bindings(self) -> Self:
        declared = {acl.name for acl in self.acls}
        for interface in self.interfaces:
            if interface.acl_in is not None and interface.acl_in not in declared:
                raise PydanticCustomError(
                    "unknown_acl",
                    "Interface {interface} references ACL {acl}, which is not declared",
                    {"interface": interface.name, "acl": interface.acl_in},
                )
        return self


def normalize_stand_dict(data: Any) -> Any:
    if isinstance(data, dict):
        data = dict(data)
        if data.get("site") and not data.get("fabric_id"):
            data["fabric_id"] = data["site"]
        elif data.get("fabric_id") and not data.get("site"):
            data["site"] = data["fabric_id"]
    return data


# id и status есть в модели устройства из ТЗ. Статус в файле справочный: в базе его
# выставляет Drift Engine.
class InventoryDevice(IntentModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | str | None = None
    hostname: Hostname
    management_ip: IPv4Address
    management_port: PortNumber = 22
    platform: Platform
    role: DeviceRole
    auth_profile: Identifier
    status: DeviceStatus | None = None
    fabric_id: str = "croc-dc1-pod1"
    site: str | None = None
    rack: str | None = None
    unit: int | None = Field(default=None, ge=1, le=60)

    _normalize_stand = model_validator(mode="before")(normalize_stand_dict)


class Inventory(IntentModel):
    devices: list[InventoryDevice] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _no_credentials(cls, data: Any) -> Any:
        if isinstance(data, dict) and "auth_profiles" in data:
            raise PydanticCustomError(
                "credentials_in_inventory",
                "Credentials must not be stored in inventory.yaml; "
                "set them in NETOPS_AUTH_PROFILES and keep only auth_profile names here",
            )
        return data

    @field_validator("devices")
    @classmethod
    def _unique_devices(cls, devices: list[InventoryDevice]) -> list[InventoryDevice]:
        _ensure_unique((d.hostname.lower() for d in devices), "duplicate_hostname", "Hostname")
        _ensure_unique((str(d.id) for d in devices if d.id is not None), "duplicate_id", "Id")
        _ensure_unique(
            (f"{d.management_ip}:{d.management_port}" for d in devices),
            "duplicate_management_endpoint",
            "Management endpoint",
        )
        return devices


def _ensure_unique(values: Iterable[str], error_type: str, subject: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise PydanticCustomError(
                error_type,
                "{subject} {value} is declared more than once",
                {"subject": subject, "value": value},
            )
        seen.add(value)


# --- Declarative Platform Profiles & Diff Filters (R2) ---


class TelemetryMetricSpec(IntentModel):
    command: str
    parser: str


class GnmiSpec(IntentModel):
    default_port: PortNumber
    check_command: str
    match_tokens: list[str] = Field(default_factory=list)


class SnmpSpec(IntentModel):
    base_port: PortNumber = 161
    clab_offset: int = 0
    hardware_module: str = "host_resources"


class ExporterSpec(IntentModel):
    port: PortNumber = 9100
    path: str = "/metrics"


class PlatformProfile(IntentModel):
    platform: Platform | str
    vendor: str = Field(min_length=1)
    default_password: str = ""
    default_interface: str = Field(min_length=1)
    offline_ports: list[PortNumber] = Field(default_factory=list)
    offline_interfaces: list[str] = Field(default_factory=list)
    ssh_banners: list[str] = Field(default_factory=list)
    version_signatures: list[str] = Field(default_factory=list)
    interface_prefixes: list[str] = Field(default_factory=list)
    lldp_port_prefixes: list[str] = Field(default_factory=list)
    commands: dict[str, str] = Field(default_factory=dict)
    render_order: list[str] = Field(default_factory=list)
    telemetry: dict[str, TelemetryMetricSpec] = Field(default_factory=dict)
    gnmi: GnmiSpec | None = None
    snmp: SnmpSpec = Field(default_factory=SnmpSpec)
    exporter: ExporterSpec = Field(default_factory=ExporterSpec)
    protocol_priority: list[str] = Field(
        default_factory=lambda: ["gnmi", "node_exporter", "snmp", "ssh_cli"]
    )
    emergency_commands: dict[str, str] = Field(default_factory=dict)


class DropLinePatternSpec(IntentModel):
    re: str
    why: str | None = None
    confidence: str | None = None


class DropBlockPatternSpec(IntentModel):
    start_re: str
    why: str | None = None
    confidence: str | None = None


class MaskSecretSpec(IntentModel):
    re: str
    replace: str
    why: str | None = None
    confidence: str | None = None


class PlatformDiffFilterSpec(IntentModel):
    indent: str | int = 1
    comment_prefix: str = "!"
    drop_line_patterns: list[DropLinePatternSpec] = Field(default_factory=list)
    drop_blocks: list[DropBlockPatternSpec] = Field(default_factory=list)
    mask_secrets: list[MaskSecretSpec] = Field(default_factory=list)
    managed_sections: list[str] = Field(default_factory=list)
    unmanaged_examples: list[str] = Field(default_factory=list)
    note: str | None = None


class CommonDiffFilterSpec(IntentModel):
    strip_trailing_whitespace: bool = True
    collapse_blank_lines: bool = True
    drop_comment_only_lines: bool = True
    implicit_lines_to_drop_from_intended: dict[str, list[str]] = Field(default_factory=dict)


class DiffFilters(IntentModel):
    version: int = 1
    note: str | None = None
    common: CommonDiffFilterSpec = Field(default_factory=CommonDiffFilterSpec)
    platforms: dict[Platform | str, PlatformDiffFilterSpec]
    dynamic_show_output_fields: dict[str, list[str]] = Field(default_factory=dict)
