"""Unit tests for stand discovery synchronization and default pod gating."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from sqlalchemy.orm import Session

from netops.enums import DeviceStatus
from netops.models import Device, Stand
from netops.services.stand_sync import sync_stand_nodes_from_sot_or_portmap


def test_seed_default_pod_disabled_when_network_driver_is_scrapli(
    session: Session, tmp_path: Path
) -> None:
    """When network_driver is scrapli (live stand), seed_default_pod must NOT generate phantom devices."""
    stand = Stand(
        slug="real-dc-stand",
        name="Real DC Stand",
        driver="scrapli",
        jump_host="192.0.2.1",
        jump_port=22,
    )
    session.add(stand)
    session.commit()

    container = MagicMock()
    container.settings.network_driver = "scrapli"
    container.settings.intent_repo_path = tmp_path

    nodes = sync_stand_nodes_from_sot_or_portmap(
        session, container, stand, {}, seed_default_pod=True
    )

    assert nodes == [], "Expected 0 phantom devices seeded when network_driver is scrapli"
    dev_count = session.query(Device).filter(Device.fabric_id == "real-dc-stand").count()
    assert dev_count == 0, "No phantom devices should be created in DB for real stand"


def test_seed_default_pod_enabled_when_network_driver_is_offline(
    session: Session, tmp_path: Path
) -> None:
    """When network_driver is offline, seed_default_pod generates the 4 default lab nodes if empty."""
    stand = Stand(
        slug="offline-dc-stand",
        name="Offline DC Stand",
        driver="offline",
        jump_host="127.0.0.1",
        jump_port=2222,
    )
    session.add(stand)
    session.commit()

    container = MagicMock()
    container.settings.network_driver = "offline"
    container.settings.intent_repo_path = tmp_path

    nodes = sync_stand_nodes_from_sot_or_portmap(
        session, container, stand, {}, seed_default_pod=True
    )

    assert len(nodes) == 4, f"Expected 4 default pod nodes, got {len(nodes)}"
    dev_count = session.query(Device).filter(Device.fabric_id == "offline-dc-stand").count()
    assert dev_count == 4

    # Verify all seeded devices have UNKNOWN status
    devices = session.query(Device).filter(Device.fabric_id == "offline-dc-stand").all()
    for dev in devices:
        assert dev.status == DeviceStatus.UNKNOWN


def test_seed_default_pod_false_never_seeds_devices(
    session: Session, tmp_path: Path
) -> None:
    """When seed_default_pod=False, no devices are seeded even in offline mode."""
    stand = Stand(
        slug="empty-offline-stand",
        name="Empty Offline Stand",
        driver="offline",
    )
    session.add(stand)
    session.commit()

    container = MagicMock()
    container.settings.network_driver = "offline"
    container.settings.intent_repo_path = tmp_path

    nodes = sync_stand_nodes_from_sot_or_portmap(
        session, container, stand, {}, seed_default_pod=False
    )

    assert nodes == []
    dev_count = session.query(Device).filter(Device.fabric_id == "empty-offline-stand").count()
    assert dev_count == 0


def test_sync_stand_nodes_from_port_map_sets_unknown_status(
    session: Session, tmp_path: Path
) -> None:
    """Discovered nodes from port_map must start with DeviceStatus.UNKNOWN."""
    stand = Stand(
        slug="pmap-stand",
        name="Port Map Stand",
        driver="scrapli",
        jump_host="192.0.2.10",
        jump_port=22,
    )
    session.add(stand)
    session.commit()

    container = MagicMock()
    container.settings.network_driver = "scrapli"
    container.settings.intent_repo_path = tmp_path

    pmap = {"leaf-sync-1.croc.lab": "192.0.2.10:2201"}
    nodes = sync_stand_nodes_from_sot_or_portmap(
        session, container, stand, pmap, seed_default_pod=False
    )

    assert len(nodes) == 1
    assert nodes[0]["hostname"] == "leaf-sync-1.croc.lab"

    dev = session.query(Device).filter(Device.hostname == "leaf-sync-1.croc.lab").first()
    assert dev is not None
    assert dev.status == DeviceStatus.UNKNOWN


def test_seed_default_pod_disabled_when_stand_driver_is_scrapli_but_container_offline(
    session: Session, tmp_path: Path
) -> None:
    """When stand.driver is scrapli, default pod is not seeded even if container network_driver is offline."""
    stand = Stand(
        slug="live-hw-stand",
        name="Live Hardware Stand",
        driver="scrapli",
        jump_host="10.0.0.1",
        jump_port=22,
    )
    session.add(stand)
    session.commit()

    container = MagicMock()
    container.settings.network_driver = "offline"
    container.settings.intent_repo_path = tmp_path

    nodes = sync_stand_nodes_from_sot_or_portmap(
        session, container, stand, {}, seed_default_pod=True
    )
    assert nodes == []
    dev_count = session.query(Device).filter(Device.fabric_id == "live-hw-stand").count()
    assert dev_count == 0


def test_seed_default_pod_disabled_when_network_driver_is_live(
    session: Session, tmp_path: Path
) -> None:
    """When container network_driver is live, default pod is not seeded."""
    stand = Stand(
        slug="live-net-stand",
        name="Live Network Stand",
        driver="offline",
        jump_host="10.0.0.2",
        jump_port=22,
    )
    session.add(stand)
    session.commit()

    container = MagicMock()
    container.settings.network_driver = "live"
    container.settings.intent_repo_path = tmp_path

    nodes = sync_stand_nodes_from_sot_or_portmap(
        session, container, stand, {}, seed_default_pod=True
    )
    assert nodes == []
    dev_count = session.query(Device).filter(Device.fabric_id == "live-net-stand").count()
    assert dev_count == 0
