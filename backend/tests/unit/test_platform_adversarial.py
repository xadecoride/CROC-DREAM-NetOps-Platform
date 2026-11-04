from __future__ import annotations

import tempfile
import typing
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from netops.enums import DeviceRole
from netops.intent.lint import lint_platforms
from netops.intent.models import (
    CommonDiffFilterSpec,
    DiffFilters,
    DropBlockPatternSpec,
    DropLinePatternSpec,
    ExporterSpec,
    GnmiSpec,
    Inventory,
    InventoryDevice,
    MaskSecretSpec,
    PlatformDiffFilterSpec,
    PlatformProfile,
    SnmpSpec,
    TelemetryMetricSpec,
)
from netops.intent.repository import IntentRepository
from netops.network.normalization import DIFF_FILTERS_PATH, load_diff_filters
from netops.services.telemetry import collect_metrics_from_cli

# ---------------------------------------------------------------------------
# 1. Adversarial Extra Fields Testing: extra="forbid" enforcement
# ---------------------------------------------------------------------------


def test_extra_forbidden_fields_rejected_across_all_schema_models() -> None:
    """Adversarial test: verify every sub-model strictly forbids unrecognized keys."""
    cases: list[tuple[type, dict[str, Any]]] = [
        (
            PlatformProfile,
            {
                "platform": "cisco_iosxe",
                "vendor": "Cisco",
                "default_interface": "Gi1",
                "rogue_root_key": "unauthorized",
            },
        ),
        (
            TelemetryMetricSpec,
            {"command": "sh ver", "parser": "parse_v", "rogue_metric_param": True},
        ),
        (
            GnmiSpec,
            {"default_port": 6030, "check_command": "sh gnmi", "rogue_token": "injection"},
        ),
        (
            SnmpSpec,
            {"base_port": 161, "clab_offset": 0, "rogue_snmp_key": 999},
        ),
        (
            ExporterSpec,
            {"port": 9100, "path": "/metrics", "rogue_exporter_key": "drop_table"},
        ),
        (
            DiffFilters,
            {"version": 1, "platforms": {}, "rogue_filter_key": "bypass"},
        ),
        (
            CommonDiffFilterSpec,
            {"rogue_common_field": False},
        ),
        (
            PlatformDiffFilterSpec,
            {"comment_prefix": "!", "rogue_vendor_param": "crash"},
        ),
        (
            DropLinePatternSpec,
            {"re": "^password", "rogue_pattern_field": "exploit"},
        ),
        (
            DropBlockPatternSpec,
            {"start_re": "^router", "rogue_block_field": "exploit"},
        ),
        (
            MaskSecretSpec,
            {"re": "^secret", "replace": "***", "rogue_mask_field": "exploit"},
        ),
    ]

    for model_cls, payload in cases:
        with pytest.raises(ValidationError) as exc_info:
            model_cls.model_validate(payload)
        error_types = [err["type"] for err in exc_info.value.errors()]
        assert "extra_forbidden" in error_types, (
            f"{model_cls.__name__} did not raise 'extra_forbidden' on payload: {payload}"
        )


# ---------------------------------------------------------------------------
# 2. Missing Required Keys Testing
# ---------------------------------------------------------------------------


def test_missing_required_keys_in_profile_and_spec() -> None:
    """Adversarial test: omitting required keys must raise validation errors."""
    # PlatformProfile requires platform, vendor, default_interface
    with pytest.raises(ValidationError) as exc:
        PlatformProfile.model_validate({"vendor": "Cisco", "default_interface": "Gi1"})
    assert any(
        err["loc"] == ("platform",) and err["type"] == "missing"
        for err in exc.value.errors()
    )

    with pytest.raises(ValidationError) as exc:
        PlatformProfile.model_validate({"platform": "cisco_iosxe", "default_interface": "Gi1"})
    assert any(
        err["loc"] == ("vendor",) and err["type"] == "missing"
        for err in exc.value.errors()
    )

    with pytest.raises(ValidationError) as exc:
        PlatformProfile.model_validate({"platform": "cisco_iosxe", "vendor": "Cisco"})
    assert any(
        err["loc"] == ("default_interface",) and err["type"] == "missing"
        for err in exc.value.errors()
    )

    # TelemetryMetricSpec requires command and parser
    with pytest.raises(ValidationError) as exc:
        TelemetryMetricSpec.model_validate({"parser": "p"})
    assert any(
        err["loc"] == ("command",) and err["type"] == "missing"
        for err in exc.value.errors()
    )

    with pytest.raises(ValidationError) as exc:
        TelemetryMetricSpec.model_validate({"command": "sh ver"})
    assert any(
        err["loc"] == ("parser",) and err["type"] == "missing"
        for err in exc.value.errors()
    )

    # GnmiSpec requires default_port and check_command
    with pytest.raises(ValidationError) as exc:
        GnmiSpec.model_validate({"check_command": "sh gnmi"})
    assert any(
        err["loc"] == ("default_port",) and err["type"] == "missing"
        for err in exc.value.errors()
    )

    with pytest.raises(ValidationError) as exc:
        GnmiSpec.model_validate({"default_port": 6030})
    assert any(
        err["loc"] == ("check_command",) and err["type"] == "missing"
        for err in exc.value.errors()
    )

    # DiffFilters requires platforms
    with pytest.raises(ValidationError) as exc:
        DiffFilters.model_validate({"version": 1})
    assert any(
        err["loc"] == ("platforms",) and err["type"] == "missing"
        for err in exc.value.errors()
    )


# ---------------------------------------------------------------------------
# 3. Malformed YAML, Syntax Corruption, & Truncation in IntentRepository
# ---------------------------------------------------------------------------


def test_repository_handles_malformed_truncated_and_corrupt_yamls(tmp_path: Path) -> None:
    """IntentRepository must gracefully catch malformed/truncated YAMLs as IntentIssues."""
    platforms_dir = tmp_path / "platforms"
    platforms_dir.mkdir(parents=True)

    bad_files = {
        "syntax_error.yaml": "platform: [unbalanced sequence",
        "tab_corruption.yaml": "platform: cisco\n\tvendor: tab_indented",
        "truncated_mid_string.yaml": "platform: \"cisco_truncated",
        "truncated_mid_dict.yaml": "platform: cisco\ncommands:\n  show_cpu:",
        "null_byte.yaml": "platform: cisco\x00_iosxe\nvendor: Cisco",
        "top_level_list.yaml": "- item1\n- item2",
        "top_level_scalar.yaml": "just_a_string_not_a_dict",
        "empty_file.yaml": "",
        "comments_only.yaml": "# Just comments\n# Nothing else\n",
    }

    for name, content in bad_files.items():
        (platforms_dir / name).write_text(content, encoding="utf-8")

    (tmp_path / "inventory.yaml").write_text("devices: []", encoding="utf-8")

    repo = IntentRepository(tmp_path)
    issues = repo.lint()

    issue_map = {Path(i.source).name: i for i in issues}

    assert "syntax_error.yaml" in issue_map
    assert issue_map["syntax_error.yaml"].code == "yaml_error"

    assert "tab_corruption.yaml" in issue_map
    assert issue_map["tab_corruption.yaml"].code == "yaml_error"

    assert "truncated_mid_string.yaml" in issue_map
    assert issue_map["truncated_mid_string.yaml"].code == "yaml_error"

    assert "null_byte.yaml" in issue_map
    assert issue_map["null_byte.yaml"].code == "yaml_error"

    assert "empty_file.yaml" in issue_map
    assert issue_map["empty_file.yaml"].code == "empty_file"

    assert "comments_only.yaml" in issue_map
    assert issue_map["comments_only.yaml"].code == "empty_file"

    assert "top_level_list.yaml" in issue_map
    assert issue_map["top_level_list.yaml"].code in ("model_type", "invalid")

    assert "top_level_scalar.yaml" in issue_map
    assert issue_map["top_level_scalar.yaml"].code in ("model_type", "invalid")


# ---------------------------------------------------------------------------
# 4. Pre-Flight Linting with Unknown Platform 'alien_nos'
# ---------------------------------------------------------------------------


def test_lint_platforms_with_alien_nos_mock_inventory() -> None:
    """Pre-flight lint must deterministically produce missing_platform_profile for alien_nos."""
    mock_device = InventoryDevice.model_construct(
        hostname="alien-switch-1.lab",
        management_ip=IPv4Address("10.10.10.1"),
        management_port=22,
        platform="alien_nos",
        role=DeviceRole.LEAF,
        auth_profile="lab",
    )
    mock_inventory = Inventory.model_construct(devices=[mock_device])

    known_profiles = {
        "cisco_iosxe": PlatformProfile.model_construct(
            platform="cisco_iosxe", vendor="Cisco", default_interface="Gi1"
        )
    }

    issues = lint_platforms(known_profiles, mock_inventory)
    assert len(issues) == 1
    assert issues[0].code == "missing_platform_profile"
    assert issues[0].hostname == "alien-switch-1.lab"
    assert "alien_nos" in issues[0].message


def test_repository_lint_handles_alien_nos_in_inventory_file(tmp_path: Path) -> None:
    """When an inventory file declares alien_nos, repo.lint() produces an issue without crashing."""
    inv_data = {
        "devices": [
            {
                "hostname": "alien-switch-1.lab",
                "management_ip": "10.10.10.1",
                "management_port": 22,
                "platform": "alien_nos",
                "role": "leaf",
                "auth_profile": "lab",
            }
        ]
    }
    (tmp_path / "inventory.yaml").write_text(yaml.safe_dump(inv_data), encoding="utf-8")

    repo = IntentRepository(tmp_path)
    issues = repo.lint()

    assert len(issues) >= 1
    # Because InventoryDevice.platform is typed as Platform (enum), it rejects alien_nos
    # with code='enum' during schema validation
    assert any("alien_nos" in i.message or i.location.endswith("platform") for i in issues)


# ---------------------------------------------------------------------------
# 5. Non-Integer and Invalid Port Types Testing
# ---------------------------------------------------------------------------


def test_non_integer_ports_rejected() -> None:
    """Non-integer ports (strings, floats, None, objects) must be rejected by Pydantic."""
    for bad_port in ["not_a_port", 8080.5, None, [80]]:
        with pytest.raises(ValidationError):
            GnmiSpec.model_validate({"default_port": bad_port, "check_command": "sh gnmi"})

        with pytest.raises(ValidationError):
            SnmpSpec.model_validate({"base_port": bad_port})

        with pytest.raises(ValidationError):
            ExporterSpec.model_validate({"port": bad_port})

        with pytest.raises(ValidationError):
            PlatformProfile.model_validate(
                {
                    "platform": "cisco_iosxe",
                    "vendor": "Cisco",
                    "default_interface": "Gi1",
                    "offline_ports": [bad_port],
                }
            )


# ---------------------------------------------------------------------------
# 6. Empirical Boundary Audit: Port Range Constraints
# ---------------------------------------------------------------------------


def test_empirical_port_boundary_behavior() -> None:
    """
    Empirical challenger probe:
    Document whether GnmiSpec, SnmpSpec, ExporterSpec, and offline_ports
    enforce TCP/UDP 1..65535 boundary limits.
    """
    # InventoryDevice enforces 1..65535
    with pytest.raises(ValidationError):
        InventoryDevice.model_validate(
            {
                "hostname": "sw1",
                "management_ip": "10.0.0.1",
                "management_port": 0,
                "platform": "cisco_iosxe",
                "role": "leaf",
                "auth_profile": "lab",
            }
        )
    with pytest.raises(ValidationError):
        InventoryDevice.model_validate(
            {
                "hostname": "sw1",
                "management_ip": "10.0.0.1",
                "management_port": 65536,
                "platform": "cisco_iosxe",
                "role": "leaf",
                "auth_profile": "lab",
            }
        )

    # Hardened behavior: GnmiSpec, SnmpSpec, ExporterSpec reject out-of-boundary ports
    with pytest.raises(ValidationError):
        GnmiSpec.model_validate({"default_port": 0, "check_command": "sh gnmi"})

    with pytest.raises(ValidationError):
        GnmiSpec.model_validate({"default_port": 65536, "check_command": "sh gnmi"})

    with pytest.raises(ValidationError):
        SnmpSpec.model_validate({"base_port": -1})

    with pytest.raises(ValidationError):
        ExporterSpec.model_validate({"port": 100000})


def test_platform_profile_dict_access_prevents_method_leakage() -> None:
    """PlatformProfile.__getitem__ and .get must not expose internal BaseModel methods."""
    profile = PlatformProfile(
        platform="cisco_iosxe",
        vendor="Cisco Systems",
        default_interface="GigabitEthernet2",
    )
    with pytest.raises(KeyError):
        _ = profile["model_dump"]

    with pytest.raises(KeyError):
        _ = profile["model_validate"]

    with pytest.raises(KeyError):
        _ = profile["unknown_attr_that_does_not_exist"]

    assert profile.get("model_dump") is None
    assert profile.get("model_dump", "safe_default") == "safe_default"
    assert profile.get("nonexistent_key", 42) == 42
    assert profile["vendor"] == "Cisco Systems"
    assert profile.get("vendor") == "Cisco Systems"


def test_telemetry_type_hints_inspection_no_name_error() -> None:
    """Runtime type hint inspection on collect_metrics_from_cli must not raise NameError."""
    hints = typing.get_type_hints(collect_metrics_from_cli)
    assert hints["return"] == dict[str, float]
    assert hints["send"] is typing.Any


def test_load_diff_filters_supports_custom_prospective_platform() -> None:
    """load_diff_filters must support prospective custom platform strings without ValueError."""
    raw = yaml.safe_load(DIFF_FILTERS_PATH.read_text(encoding="utf-8"))
    raw["platforms"]["custom_prospective_os"] = {
        "indent": 1,
        "comment_prefix": "!",
        "drop_line_patterns": [{"re": r"^building configuration.*$"}],
    }

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tmp:
        yaml.safe_dump(raw, tmp)
        tmp_path = Path(tmp.name)

    try:
        rules = load_diff_filters(tmp_path)
        assert "custom_prospective_os" in rules
        custom_rule = rules["custom_prospective_os"]
        assert custom_rule.indent_unit == 1
    finally:
        tmp_path.unlink(missing_ok=True)


