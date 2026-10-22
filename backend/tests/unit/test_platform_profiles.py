from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from netops.enums import Platform
from netops.intent.models import (
    DiffFilters,
    PlatformProfile,
)
from netops.intent.repository import IntentRepository
from netops.services.discovery import (
    detect_platform_from_banner,
    detect_platform_from_version,
    load_platform_profiles,
)
from netops.services.telemetry import (
    collect_metrics_from_cli,
    detect_optimal_telemetry_protocol,
    register_parser,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
PLATFORMS_DIR = REPO_ROOT / "intent" / "platforms"
DIFF_FILTERS_PATH = REPO_ROOT / "backend" / "netops" / "network" / "diff_filters.yaml"

ALL_SEVEN_PLATFORMS = [
    "cisco_iosxe",
    "arista_eos",
    "huawei_vrp",
    "eltex_mes",
    "juniper_junos",
    "yadro_kornfe",
    "linux_server",
]


def test_all_seven_platform_profiles_exist_and_validate() -> None:
    """Requirement R2: All 7 platforms must have valid declarative YAML profiles."""
    assert PLATFORMS_DIR.is_dir(), f"{PLATFORMS_DIR} must exist"

    for plat_name in ALL_SEVEN_PLATFORMS:
        yaml_file = PLATFORMS_DIR / f"{plat_name}.yaml"
        assert yaml_file.is_file(), f"Missing required platform YAML: {yaml_file}"

        raw = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        assert isinstance(raw, dict), f"{yaml_file} must contain a mapping"

        profile = PlatformProfile.model_validate(raw)
        assert str(getattr(profile.platform, "value", profile.platform)).lower() == plat_name
        assert profile.vendor, "Vendor must not be empty"
        assert profile.default_interface, "default_interface must be specified"
        assert len(profile.commands) >= 5, "Must declare standard CLI commands"
        assert "cpu_pct" in profile.telemetry
        assert "memory_pct" in profile.telemetry
        assert "uplink_util_pct" in profile.telemetry
        assert profile.protocol_priority, "protocol_priority must not be empty"
        assert profile.emergency_commands, "emergency_commands must not be empty"


def test_platform_specific_hardware_and_protocol_specs() -> None:
    """Verify specific protocol ports and properties for key vendors."""
    profiles = {
        f.stem: PlatformProfile.model_validate(yaml.safe_load(f.read_text(encoding="utf-8")))
        for f in PLATFORMS_DIR.glob("*.yaml")
    }

    # Cisco IOS-XE
    cisco = profiles["cisco_iosxe"]
    assert cisco.gnmi is not None
    assert cisco.gnmi.default_port == 9339
    assert cisco.snmp.base_port == 161
    assert cisco.snmp.clab_offset == -600

    # Arista EOS
    arista = profiles["arista_eos"]
    assert arista.gnmi is not None
    assert arista.gnmi.default_port == 6030

    # Huawei VRP
    huawei = profiles["huawei_vrp"]
    assert huawei.gnmi is not None
    assert huawei.gnmi.default_port == 57400
    assert "GE" in huawei.interface_prefixes

    # Juniper Junos
    juniper = profiles["juniper_junos"]
    assert juniper.gnmi is not None
    assert juniper.gnmi.default_port == 32767

    # Yadro Kornfe
    yadro = profiles["yadro_kornfe"]
    assert yadro.gnmi is not None
    assert yadro.gnmi.default_port == 6030

    # Linux Server
    linux = profiles["linux_server"]
    assert linux.exporter.port == 9100
    assert "node_exporter" in linux.protocol_priority


def test_diff_filters_validates_against_pydantic_schema() -> None:
    """Requirement R2: diff_filters.yaml must validate cleanly against DiffFilters."""
    assert DIFF_FILTERS_PATH.is_file(), f"{DIFF_FILTERS_PATH} must exist"
    raw = yaml.safe_load(DIFF_FILTERS_PATH.read_text(encoding="utf-8"))
    filters = DiffFilters.model_validate(raw)

    assert filters.version == 1
    assert "cisco_iosxe" in filters.platforms
    assert "arista_eos" in filters.platforms
    assert "huawei_vrp" in filters.platforms
    assert "linux_server" in filters.platforms
    assert filters.common.strip_trailing_whitespace is True


def test_malformed_platform_yamls_raise_validation_error() -> None:
    """Strict schema enforcement: missing fields, wrong types, or unknown extra keys fail."""
    valid_base = {
        "platform": "arista_eos",
        "vendor": "Arista Networks",
        "default_password": "admin",
        "default_interface": "Ethernet1",
        "commands": {"version": "show version"},
        "telemetry": {
            "cpu_pct": {"command": "show cpu", "parser": "parse_arista_cpu"},
        },
    }

    # 1. Missing required field 'vendor'
    bad_missing = dict(valid_base)
    del bad_missing["vendor"]
    with pytest.raises(ValidationError):
        PlatformProfile.model_validate(bad_missing)

    # 2. Missing required field 'default_interface'
    bad_missing_iface = dict(valid_base)
    del bad_missing_iface["default_interface"]
    with pytest.raises(ValidationError):
        PlatformProfile.model_validate(bad_missing_iface)

    # 3. Wrong data type: offline_ports is string instead of list
    bad_type = dict(valid_base)
    bad_type["offline_ports"] = "2211"  # type: ignore[assignment]
    with pytest.raises(ValidationError):
        PlatformProfile.model_validate(bad_type)

    # 4. Unknown extra field (strictly forbidden by extra='forbid')
    bad_extra = dict(valid_base)
    bad_extra["unexpected_custom_key_12345"] = "malicious_payload"
    with pytest.raises(ValidationError):
        PlatformProfile.model_validate(bad_extra)


def test_dynamic_custom_platform_onboarding_without_python_modifications() -> None:
    """Requirement R3: Prospective hardware profile works dynamically with 0 code changes."""
    # Define an entirely new synthetic vendor OS
    custom_profile_data: dict[str, Any] = {
        "platform": "synthetic_datacenter_os",
        "vendor": "HyperSwitch Corp",
        "default_password": "custom_super_secret",
        "default_interface": "HundredGigE1/1/1",
        "offline_ports": [2999],
        "offline_interfaces": ["HundredGigE1/1/1", "HundredGigE1/1/2"],
        "ssh_banners": ["hyperswitch-v1", "custom_os"],
        "version_signatures": ["HyperSwitch OS Release 42"],
        "interface_prefixes": ["HundredGigE", "Loopback"],
        "lldp_port_prefixes": ["100GE", "eth"],
        "commands": {
            "version": "show system version",
            "memory": "show system memory",
            "cpu": "show system cpu",
            "running_config": "show configuration all",
            "hostname_filter": "show hostname",
            "interfaces_brief": "show interfaces brief",
            "lldp_neighbors": "show lldp",
        },
        "telemetry": {
            "cpu_pct": {
                "command": "show system cpu-utilization",
                "parser": "synthetic_custom_cpu_parser",
            },
            "uplink_util_pct": {
                "command": "show interfaces utilization",
                "parser": "synthetic_custom_link_parser",
            },
        },
        "gnmi": {
            "default_port": 8080,
            "check_command": "show gnmi service status",
            "match_tokens": ["running", "operational", "8080"],
        },
        "snmp": {
            "base_port": 1161,
            "clab_offset": 0,
        },
        "exporter": {
            "port": 9500,
            "path": "/metrics",
        },
        "protocol_priority": ["gnmi", "node_exporter", "snmp", "ssh_cli"],
        "emergency_commands": {
            "reset_bgp_sessions": "clear bgp all",
            "rollback_last_commit": "rollback 1",
            "restart_services": "systemctl restart routing",
            "reboot": "reboot force",
            "default": "show system version",
        },
    }

    # Validate against strict schema
    custom_profile = PlatformProfile.model_validate(custom_profile_data)
    assert custom_profile.vendor == "HyperSwitch Corp"
    assert custom_profile.gnmi is not None
    assert custom_profile.gnmi.default_port == 8080

    # Register custom metric parsers in the telemetry registry
    register_parser("synthetic_custom_cpu_parser", lambda out: 37.8)
    register_parser("synthetic_custom_link_parser", lambda out: 64.2)

    # Collect CLI metrics dynamically using only the custom profile
    mock_responses = {
        "show system cpu-utilization": "CPU 37.8%",
        "show interfaces utilization": "Link Load 64.2%",
    }
    collected = collect_metrics_from_cli(
        hostname="custom-leaf-1.lab",
        platform="synthetic_datacenter_os",
        send=lambda cmd: mock_responses.get(cmd, ""),
        platform_profile=custom_profile,
    )
    assert collected["cpu_pct"] == 37.8
    assert collected["uplink_util_pct"] == 64.2

    # gNMI засчитывается только при реально открытом порту + подтверждении CLI (gnmic нет)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        gnmi_port = listener.getsockname()[1]
        optimal_gnmi = detect_optimal_telemetry_protocol(
            "127.0.0.1",
            "synthetic_datacenter_os",
            ssh_send=lambda cmd: "gNMI service is running on port 8080",
            platform_profile=custom_profile,
            gnmi_port=gnmi_port,
            timeout=0.2,
        )
    assert optimal_gnmi["protocol"] == "gnmi"
    assert optimal_gnmi["port"] == gnmi_port
    assert optimal_gnmi["cli_supported"] is True

    # Fallback to ssh_cli when advanced service is down
    fallback = detect_optimal_telemetry_protocol(
        "127.0.0.1",
        "synthetic_datacenter_os",
        ssh_send=lambda cmd: "gNMI service inactive",
        platform_profile=custom_profile,
        timeout=0.05,
    )
    assert fallback["protocol"] == "ssh_cli"
    assert fallback["port"] == 22


def test_repository_get_platform_profile_and_caching() -> None:
    """IntentRepository must load profiles by platform key and cache instances."""
    repo = IntentRepository(PLATFORMS_DIR.parent)

    prof1 = repo.get_platform_profile("cisco_iosxe")
    prof2 = repo.get_platform_profile(Platform.CISCO_IOSXE)
    assert prof1 is prof2  # Identical cached instance

    with pytest.raises(KeyError):
        repo.get_platform_profile("non_existent_os_xyz")


def test_preflight_lint_catches_missing_platform_profile(tmp_path: Path) -> None:
    """Referential integrity: inventory referencing an undeclared platform must fail lint."""
    platforms_dir = tmp_path / "platforms"
    platforms_dir.mkdir(parents=True)

    # Only provide cisco_iosxe.yaml
    cisco_yaml = PLATFORMS_DIR / "cisco_iosxe.yaml"
    (platforms_dir / "cisco_iosxe.yaml").write_text(cisco_yaml.read_text(encoding="utf-8"))

    # Inventory references a switch running juniper_junos
    inventory_data = {
        "devices": [
            {
                "hostname": "core-juniper-1.lab",
                "management_ip": "10.100.0.1",
                "management_port": 22,
                "platform": "juniper_junos",
                "role": "spine",
                "auth_profile": "lab",
            }
        ]
    }
    (tmp_path / "inventory.yaml").write_text(yaml.safe_dump(inventory_data))

    repo = IntentRepository(tmp_path)
    issues = repo.lint()

    missing_platform_issues = [i for i in issues if i.code == "missing_platform_profile"]
    assert len(missing_platform_issues) == 1
    assert "juniper_junos" in missing_platform_issues[0].message
    assert missing_platform_issues[0].hostname == "core-juniper-1.lab"


def test_preflight_lint_catches_malformed_platform_yaml(tmp_path: Path) -> None:
    """Pre-flight lint must catch malformed YAML syntax in platforms/ directory."""
    platforms_dir = tmp_path / "platforms"
    platforms_dir.mkdir(parents=True)

    # Corrupted platform YAML
    (platforms_dir / "corrupted_os.yaml").write_text("platform: [unbalanced list")

    inventory_data = {"devices": []}
    (tmp_path / "inventory.yaml").write_text(yaml.safe_dump(inventory_data))

    repo = IntentRepository(tmp_path)
    issues = repo.lint()

    yaml_errs = [i for i in issues if i.code == "yaml_error"]
    assert len(yaml_errs) >= 1
    assert "corrupted_os.yaml" in yaml_errs[0].source


def test_discovery_helper_functions_use_platform_profiles() -> None:
    """Discovery helper functions dynamically resolve platform by banners and signatures."""
    profiles = load_platform_profiles(PLATFORMS_DIR.parent)
    assert len(profiles) >= 7

    # Banner detection
    assert detect_platform_from_banner("SSH-2.0-Cisco-1.25", profiles) == Platform.CISCO_IOSXE
    assert (
        detect_platform_from_banner("SSH-2.0-OpenSSH_8.4 Arista EOS", profiles)
        == Platform.ARISTA_EOS
    )
    assert detect_platform_from_banner("SSH-2.0-Huawei-VRP", profiles) == Platform.HUAWEI_VRP
    assert (
        detect_platform_from_banner("SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.1", profiles)
        == Platform.LINUX_SERVER
    )

    # Version output detection
    assert (
        detect_platform_from_version(
            "Cisco IOS XE Software, Version 17.03", Platform.CISCO_IOSXE, profiles
        )
        == Platform.CISCO_IOSXE
    )
    assert (
        detect_platform_from_version(
            "Arista cEOSLab version 4.28.0F", Platform.CISCO_IOSXE, profiles
        )
        == Platform.ARISTA_EOS
    )

