from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from netops.db import utcnow
from netops.enums import DeviceStatus, JobStatus, JobType, UserRole
from netops.models import Device, Job
from netops.services.state import get_state_manager
from netops.telemetry import collector, metrics
from netops.telemetry.metrics import Sample
from netops.toolchain import Toolchain
from tests.conftest import auth


def _values(samples: list[Sample], name: str) -> dict[tuple[tuple[str, str], ...], float]:
    return {
        tuple(sorted(sample.labels.items())): sample.value
        for sample in samples
        if sample.name == name
    }


def test_platform_samples_cover_devices_and_jobs(
    session: Session, devices: dict[str, Device]
) -> None:
    session.get_one(Device, devices["leaf-2.croc.lab"].id).status = DeviceStatus.DRIFT_DETECTED
    session.add(
        Job(
            type=JobType.DRY_RUN,
            status=JobStatus.SUCCESS,
            created_by="tester",
            finished_at=utcnow(),
        )
    )
    session.commit()

    samples = collector.platform_samples(session)

    totals = _values(samples, metrics.DEVICES_TOTAL)
    assert totals[(("status", "IN_SYNC"),)] == 0
    assert totals[(("status", "DRIFT_DETECTED"),)] == 1
    assert totals[(("status", "UNKNOWN"),)] == 3
    assert _values(samples, metrics.DRIFT_DEVICES)[()] == 1

    statuses = _values(samples, metrics.DEVICE_STATUS)
    leaf2 = {
        ("hostname", "leaf-2.croc.lab"),
        ("platform", "cisco_iosxe"),
        ("role", "leaf"),
    }
    assert statuses[tuple(sorted(leaf2 | {("status", "DRIFT_DETECTED")}))] == 1
    assert statuses[tuple(sorted(leaf2 | {("status", "IN_SYNC")}))] == 0

    jobs = _values(samples, metrics.JOBS_TOTAL)
    assert jobs[(("status", "SUCCESS"), ("type", "DRY_RUN"))] == 1
    assert jobs[(("status", "FAILED"), ("type", "DEPLOY"))] == 0
    assert _values(samples, metrics.JOB_LAST_FINISHED)[(("type", "DRY_RUN"),)] > 0


def test_device_samples_come_from_the_lab(
    session: Session, devices: dict[str, Device], toolchain: Toolchain, lab_path: Path
) -> None:
    (lab_path / "spine-2.croc.lab.cfg").unlink()

    samples = collector.device_samples(session, toolchain)

    reachable = _values(samples, metrics.DEVICE_REACHABLE)
    assert reachable[(("hostname", "spine-1.croc.lab"),)] == 1
    assert reachable[(("hostname", "spine-2.croc.lab"),)] == 0

    bgp = _values(samples, metrics.BGP_SESSION_UP)
    assert (
        bgp[(("hostname", "leaf-1.croc.lab"), ("peer", "10.0.1.0"), ("state", "Established"))] == 1
    )
    interfaces = _values(samples, metrics.INTERFACE_UP)
    leaf1 = ("hostname", "leaf-1.croc.lab")
    assert interfaces[(leaf1, ("interface", "GigabitEthernet2"), ("state", "up/up"))] == 1
    # GigabitEthernet4 в intent выключен, поэтому up/up от него не ждём.
    down = (leaf1, ("interface", "GigabitEthernet4"), ("state", "administratively down/down"))
    assert interfaces[down] == 0
    assert set(_values(samples, metrics.PING_LOSS).values()) == {0.0}


def test_publish_replaces_snapshot_and_respects_ttl() -> None:
    collector.publish([Sample(metrics.DRIFT_DEVICES, 9)])
    collected_at = collector.publish(
        [
            Sample(metrics.DRIFT_DEVICES, 1, {"fabric_id": "dc1"}),
            Sample(
                metrics.DEVICE_CPU_UTIL,
                23.5,
                {"hostname": "spine-1.croc.lab", "fabric_id": "dc1"},
            ),
        ]
    )

    samples, latest_at = collector.latest()
    assert latest_at == collected_at
    assert len(samples) == 2

    host_samples, host_at = collector.latest_for_host("spine-1.croc.lab")
    assert host_at == collected_at
    assert host_samples == [
        Sample(
            metrics.DEVICE_CPU_UTIL,
            23.5,
            {"hostname": "spine-1.croc.lab", "fabric_id": "dc1"},
        )
    ]
    fab_samples, fab_at = collector.latest_for_fabric("dc1")
    assert fab_at == collected_at
    assert len(fab_samples) == 2

    # Истёкший срез не должен отдаваться как «живой».
    collector.publish([Sample(metrics.DRIFT_DEVICES, 2)], ttl_seconds=60)
    state = get_state_manager()
    expires_at, raw = state._local_kv[collector.LATEST_SNAPSHOT_KEY]
    assert expires_at is not None
    state._local_kv[collector.LATEST_SNAPSHOT_KEY] = (0.0, raw)
    assert collector.latest() == ([], None)


def test_metrics_endpoint_needs_no_token(
    client: TestClient, session: Session, devices: dict[str, Device]
) -> None:
    assert client.get("/metrics").text == ""

    collector.publish(collector.platform_samples(session))
    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert f"# TYPE {metrics.DEVICES_TOTAL} gauge" in body
    assert f'{metrics.DEVICE_STATUS}{{hostname="leaf-1.croc.lab"' in body
    assert metrics.COLLECTED_AT in body


def test_metrics_endpoint_is_not_hidden_from_viewers(client: TestClient) -> None:
    assert client.get("/metrics", headers=auth(UserRole.VIEWER)).status_code == 200


def test_telemetry_collect_and_collectors_endpoints(
    client: TestClient, devices: dict[str, Device]
) -> None:
    res = client.post("/api/v1/telemetry/collect", headers=auth(UserRole.OPERATOR))
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["samples_collected"] > 0
    assert metrics.DEVICES_TOTAL in client.get("/metrics").text

    collectors_res = client.get("/api/v1/telemetry/collectors", headers=auth(UserRole.VIEWER))
    assert collectors_res.status_code == 200
    assert "protocols" in collectors_res.json()

