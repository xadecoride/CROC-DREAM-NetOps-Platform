from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from netops.enums import DeviceRole, DeviceStatus, DriftStatus, Platform
from netops.intent import IntentRepository
from netops.intent.models import InventoryDevice
from netops.models import Device
from netops.schemas.devices import DeviceCreate, DeviceRead, DeviceUpdate
from netops.schemas.drift import DriftReportItem, DriftScanRequest
from netops.services.freeze import (
    _reset_local_freeze,
    get_freeze_state,
    is_factory_frozen,
    set_factory_freeze,
)


def test_device_model_defaults() -> None:
    device = Device(
        hostname="border-1.croc.lab",
        management_ip="172.20.20.100",
        platform=Platform.ARISTA_EOS,
        role=DeviceRole.SPINE,
        auth_profile="lab",
    )
    assert device.fabric_id == "croc-dc1-pod1"
    assert device.site is None

    device2 = Device(
        hostname="border-2.croc.lab",
        management_ip="172.20.20.101",
        platform=Platform.ARISTA_EOS,
        role=DeviceRole.SPINE,
        auth_profile="lab",
        fabric_id="croc-dc2-pod1",
        site="dc2-spb",
    )
    assert device2.fabric_id == "croc-dc2-pod1"
    assert device2.site == "dc2-spb"


def test_device_schemas_backward_compatibility() -> None:
    # 1. DeviceCreate without fabric_id / site preserves default
    create_dto = DeviceCreate(
        hostname="leaf-99.croc.lab",
        management_ip="172.20.20.99",
        platform=Platform.ARISTA_EOS,
        role=DeviceRole.LEAF,
        auth_profile="lab",
    )
    assert create_dto.fabric_id == "croc-dc1-pod1"
    assert create_dto.site is None

    # 2. DeviceCreate with site synchronizes fabric_id
    create_dto2 = DeviceCreate(
        hostname="leaf-98.croc.lab",
        management_ip="172.20.20.98",
        platform=Platform.ARISTA_EOS,
        role=DeviceRole.LEAF,
        auth_profile="lab",
        site="dc2-pod5",
    )
    assert create_dto2.site == "dc2-pod5"
    assert create_dto2.fabric_id == "dc2-pod5"

    # 3. DeviceUpdate supports partial updates and stand normalization
    update_dto = DeviceUpdate(fabric_id="dc3-pod1")
    assert update_dto.fabric_id == "dc3-pod1"
    assert update_dto.site == "dc3-pod1"

    update_dto2 = DeviceUpdate(site="dc3-pod2")
    assert update_dto2.site == "dc3-pod2"
    assert update_dto2.fabric_id == "dc3-pod2"

    # 4. DeviceRead contains stand metadata
    read_dto = DeviceRead.model_validate(
        {
            "id": 1,
            "hostname": "leaf-1.croc.lab",
            "management_ip": "172.20.20.11",
            "management_port": 22,
            "platform": Platform.ARISTA_EOS,
            "role": DeviceRole.LEAF,
            "auth_profile": "lab",
            "status": DeviceStatus.UNKNOWN,
            "fabric_id": "dc2-pod1",
            "site": "dc2",
            "created_at": "2026-10-15T12:00:00Z",
            "updated_at": "2026-10-15T12:00:00Z",
            "last_checked_at": None,
        }
    )
    assert read_dto.fabric_id == "dc2-pod1"
    assert read_dto.site == "dc2"


def test_inventory_device_schema() -> None:
    # Default fallback
    inv_dev = InventoryDevice(
        hostname="leaf-1.croc.lab",
        management_ip="172.20.20.11",
        platform=Platform.ARISTA_EOS,
        role=DeviceRole.LEAF,
        auth_profile="lab",
    )
    assert inv_dev.fabric_id == "croc-dc1-pod1"
    assert inv_dev.site is None

    # Explicit stand
    inv_dev2 = InventoryDevice(
        hostname="leaf-2.croc.lab",
        management_ip="172.20.20.12",
        platform=Platform.ARISTA_EOS,
        role=DeviceRole.LEAF,
        auth_profile="lab",
        fabric_id="croc-dc2-pod1",
    )
    assert inv_dev2.fabric_id == "croc-dc2-pod1"
    assert inv_dev2.site == "croc-dc2-pod1"


def test_drift_scan_request_and_report_schemas() -> None:
    req = DriftScanRequest(fabric_id="croc-dc2-pod1")
    assert req.fabric_id == "croc-dc2-pod1"
    assert req.device_ids is None

    req_site = DriftScanRequest(site="dc3-pod1")
    assert req_site.site == "dc3-pod1"

    with pytest.raises(ValidationError):
        DriftScanRequest(unknown_field="invalid")  # type: ignore[call-arg]

    item = DriftReportItem(
        device_id=1,
        hostname="leaf-1",
        fabric_id="stand-10",
        site="dc10",
        status=DriftStatus.IN_SYNC,
        checked_at="2026-10-15T12:00:00Z",  # type: ignore[arg-type]
        job_id=None,
        unauthorized_lines=[],
        missing_lines=[],
        remediation_patch=None,
        error=None,
    )
    assert item.fabric_id == "stand-10"
    assert item.site == "dc10"
    assert item.fabric_id == "stand-10"
    assert item.site == "dc10"


def test_factory_freeze_scoped_operations() -> None:
    _reset_local_freeze()
    try:
        assert not is_factory_frozen()
        assert not is_factory_frozen("croc-dc1-pod1")
        assert not is_factory_frozen("croc-dc2-pod1")

        # 1. Freeze Stand 2 only
        res = set_factory_freeze(True, reason="Emerg fail in pod2", fabric_id="croc-dc2-pod1")
        assert res["frozen"] is True
        assert res["fabric_id"] == "croc-dc2-pod1"

        # Stand 2 is frozen, but Stand 1 and global remain unfrozen
        assert is_factory_frozen("croc-dc2-pod1") is True
        assert is_factory_frozen("croc-dc1-pod1") is False
        assert is_factory_frozen() is False

        # Global freeze state reflects Stand 2
        global_state = get_freeze_state()
        assert global_state["frozen"] is False
        assert "croc-dc2-pod1" in global_state.get("fabrics", {})

        # Stand 2 specific state query
        stand2_state = get_freeze_state("croc-dc2-pod1")
        assert stand2_state["frozen"] is True
        assert stand2_state["reason"] == "Emerg fail in pod2"

        # 2. Global freeze blocks everything
        set_factory_freeze(True, reason="Total DC blackout")
        assert is_factory_frozen() is True
        assert is_factory_frozen("croc-dc1-pod1") is True
        assert is_factory_frozen("croc-dc2-pod1") is True

        # 3. Clear global freeze, Stand 2 remains frozen
        set_factory_freeze(False)
        assert is_factory_frozen() is False
        assert is_factory_frozen("croc-dc1-pod1") is False
        assert is_factory_frozen("croc-dc2-pod1") is True

        # 4. Clear Stand 2 freeze
        set_factory_freeze(False, fabric_id="croc-dc2-pod1")
        assert is_factory_frozen("croc-dc2-pod1") is False
    finally:
        _reset_local_freeze()


def test_scoped_preflight_lint_isolates_conflicts(tmp_path: Path) -> None:
    repo_dir = tmp_path / "intent_repo"
    devices_dir = repo_dir / "devices"
    devices_dir.mkdir(parents=True)

    # Multi-stand inventory with 2 stands: croc-dc1-pod1 and croc-dc2-pod1
    inventory_data = {
        "devices": [
            {
                "hostname": "pod1-leaf1.lab",
                "management_ip": "172.20.1.11",
                "platform": "arista_eos",
                "role": "leaf",
                "auth_profile": "lab",
                "fabric_id": "croc-dc1-pod1",
            },
            {
                "hostname": "pod2-leaf1.lab",
                "management_ip": "172.20.2.11",
                "platform": "arista_eos",
                "role": "leaf",
                "auth_profile": "lab",
                "fabric_id": "croc-dc2-pod1",
            },
            {
                "hostname": "pod2-leaf2.lab",
                "management_ip": "172.20.2.12",
                "platform": "arista_eos",
                "role": "leaf",
                "auth_profile": "lab",
                "fabric_id": "croc-dc2-pod1",
            },
        ]
    }
    (repo_dir / "inventory.yaml").write_text(yaml.safe_dump(inventory_data))

    # Pod 1 Leaf 1 has Router ID 10.255.0.1
    (devices_dir / "pod1-leaf1.lab.yaml").write_text(
        yaml.safe_dump(
            {
                "hostname": "pod1-leaf1.lab",
                "bgp": {"asn": 65101, "router_id": "10.255.0.1"},
                "interfaces": [{"name": "Loopback0", "ipv4_address": "10.255.0.1/32"}],
            }
        )
    )

    # Pod 2 Leaf 1 ALSO legitimately uses Router ID 10.255.0.1 (cross-stand reuse)
    (devices_dir / "pod2-leaf1.lab.yaml").write_text(
        yaml.safe_dump(
            {
                "hostname": "pod2-leaf1.lab",
                "bgp": {"asn": 65201, "router_id": "10.255.0.1"},
                "interfaces": [{"name": "Loopback0", "ipv4_address": "10.255.0.1/32"}],
            }
        )
    )

    # Pod 2 Leaf 2 has its own Router ID 10.255.0.2
    (devices_dir / "pod2-leaf2.lab.yaml").write_text(
        yaml.safe_dump(
            {
                "hostname": "pod2-leaf2.lab",
                "bgp": {"asn": 65202, "router_id": "10.255.0.2"},
                "interfaces": [{"name": "Loopback0", "ipv4_address": "10.255.0.2/32"}],
            }
        )
    )

    repo = IntentRepository(repo_dir)

    # 1. Global lint: pod1-leaf1 and pod2-leaf1 sharing 10.255.0.1 MUST NOT cause collision!
    issues = repo.lint()
    assert len(issues) == 0

    # 2. Scoped lint for Pod 1: 0 issues
    assert len(repo.lint(fabric_id="croc-dc1-pod1")) == 0

    # 3. Scoped lint for Pod 2: 0 issues
    assert len(repo.lint(fabric_id="croc-dc2-pod1")) == 0

    # 4. Now introduce an intra-stand conflict within Pod 2 (pod2-leaf2 duplicates 10.255.0.1)
    (devices_dir / "pod2-leaf2.lab.yaml").write_text(
        yaml.safe_dump(
            {
                "hostname": "pod2-leaf2.lab",
                "bgp": {"asn": 65202, "router_id": "10.255.0.1"},
                "interfaces": [{"name": "Loopback0", "ipv4_address": "10.255.0.1/32"}],
            }
        )
    )

    # Global lint: detects intra-stand collision in Pod 2
    global_issues = repo.lint()
    dup_rids = [i for i in global_issues if i.code == "duplicate_router_id"]
    assert len(dup_rids) == 2
    assert {i.hostname for i in dup_rids} == {"pod2-leaf1.lab", "pod2-leaf2.lab"}

    # Scoped lint for Pod 1: STILL completely green (isolated from Pod 2 issue)
    assert len(repo.lint(fabric_id="croc-dc1-pod1")) == 0

    # Scoped lint for Pod 2: isolated reports intra-stand conflict
    pod2_issues = repo.lint(fabric_id="croc-dc2-pod1")
    assert len(pod2_issues) == 4  # 2 for duplicate_router_id + 2 for duplicate_ip_address
    assert all(i.hostname in {"pod2-leaf1.lab", "pod2-leaf2.lab"} for i in pod2_issues)


def test_scoped_preflight_lint_catches_device_yaml_syntax_error(tmp_path: Path) -> None:
    repo_dir = tmp_path / "intent_repo"
    devices_dir = repo_dir / "devices"
    devices_dir.mkdir(parents=True)

    # Multi-stand inventory
    inventory_data = {
        "devices": [
            {
                "hostname": "pod1-leaf1",
                "management_ip": "172.20.1.11",
                "platform": "arista_eos",
                "role": "leaf",
                "auth_profile": "lab",
                "fabric_id": "pod1",
            },
            {
                "hostname": "pod2-leaf1",
                "management_ip": "172.20.2.11",
                "platform": "arista_eos",
                "role": "leaf",
                "auth_profile": "lab",
                "fabric_id": "pod2",
            },
        ]
    }
    (repo_dir / "inventory.yaml").write_text(yaml.safe_dump(inventory_data))

    # Pod 1 device has broken YAML syntax
    (devices_dir / "pod1-leaf1.yaml").write_text("hostname: [broken yaml\n")

    # Pod 2 device has valid YAML
    (devices_dir / "pod2-leaf1.yaml").write_text(
        yaml.safe_dump(
            {
                "hostname": "pod2-leaf1",
                "bgp": {"asn": 65201, "router_id": "10.255.0.1"},
                "interfaces": [{"name": "Loopback0", "ipv4_address": "10.255.0.1/32"}],
            }
        )
    )

    repo = IntentRepository(repo_dir)

    # 1. Scoped lint for pod1 catches YAML syntax error in pod1-leaf1.yaml
    pod1_issues = repo.lint(fabric_id="pod1")
    assert len(pod1_issues) == 1
    assert pod1_issues[0].code == "yaml_error"
    assert "devices/pod1-leaf1.yaml" in pod1_issues[0].source

    # 2. Scoped lint for pod2 is completely isolated and reports 0 issues
    pod2_issues = repo.lint(fabric_id="pod2")
    assert len(pod2_issues) == 0

    # 3. Empty device file in pod2 is also caught when scoped to pod2
    (devices_dir / "pod2-leaf1.yaml").write_text("")
    pod2_empty_issues = repo.lint(fabric_id="pod2")
    assert len(pod2_empty_issues) == 1
    assert pod2_empty_issues[0].code == "empty_file"
    assert "devices/pod2-leaf1.yaml" in pod2_empty_issues[0].source

    # 4. Broken inventory.yaml is caught across all stands during scoped lint
    (repo_dir / "inventory.yaml").write_text("devices: [broken inventory yaml\n")
    repo_broken_inv = IntentRepository(repo_dir)
    inv_issues = repo_broken_inv.lint(fabric_id="pod1")
    assert len(inv_issues) > 0
    assert any(i.code == "yaml_error" and "inventory.yaml" in i.source for i in inv_issues)
