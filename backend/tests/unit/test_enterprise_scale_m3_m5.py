from __future__ import annotations

import socket
import uuid
from datetime import timedelta
from pathlib import Path

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from netops.db import utcnow
from netops.enums import DeviceRole, JobType, Platform, SnapshotKind, TargetStatus
from netops.models import ConfigSnapshot, Device, Job, JobLog, JobTarget
from netops.pipeline.pipelines import order_targets_into_waves
from netops.services.retention import purge_expired_records
from netops.services.telemetry import (
    clear_live_metrics,
    collect_fleet_metrics_parallel,
    detect_optimal_telemetry_protocol,
)


def test_config_snapshot_sha256_deduplication(session: Session) -> None:
    dev = Device(
        hostname="dedup-leaf-1.croc.lab",
        management_ip="10.250.1.10",
        platform=Platform.HUAWEI_VRP,
        role=DeviceRole.LEAF,
        auth_profile="leaf1_auth",
        fabric_id="stand-100",
    )
    session.add(dev)
    session.flush()

    job1 = Job(type=JobType.DRY_RUN, created_by="admin")
    job2 = Job(type=JobType.DRY_RUN, created_by="admin")
    session.add_all([job1, job2])
    session.flush()

    cfg_text = "sysname dedup-leaf-1\ninterface GE1/0/1\n ip address 10.0.0.1 30\n"
    s1 = ConfigSnapshot.capture_or_reuse(
        session,
        device_id=dev.id,
        kind=SnapshotKind.RUNNING,
        content=cfg_text,
        job_id=job1.id,
    )
    session.commit()

    # Second capture with identical content must reuse s1 instead of inserting a duplicate row
    s2 = ConfigSnapshot.capture_or_reuse(
        session,
        device_id=dev.id,
        kind=SnapshotKind.RUNNING,
        content=cfg_text,
        job_id=job2.id,
    )
    session.commit()

    assert s1.id == s2.id
    assert s2.config_hash == s1.sha256
    assert s2.job_id == job2.id

    count = session.scalar(
        select(func.count())
        .select_from(ConfigSnapshot)
        .where(ConfigSnapshot.device_id == dev.id, ConfigSnapshot.kind == SnapshotKind.RUNNING)
    )
    assert count == 1

    # Modified config creates a second row
    s3 = ConfigSnapshot.capture_or_reuse(
        session,
        device_id=dev.id,
        kind=SnapshotKind.RUNNING,
        content=cfg_text + " description changed\n",
        job_id=job2.id,
    )
    session.commit()
    assert s3.id != s1.id


def test_ttl_retention_purges_only_expired_job_logs(session: Session) -> None:
    now = utcnow()
    job = Job(type=JobType.DRIFT_SCAN, created_by="system")
    session.add(job)
    session.flush()

    old_log = JobLog(
        job_id=job.id,
        step="plan",
        message="old log",
        created_at=now - timedelta(days=20),
    )
    fresh_log = JobLog(
        job_id=job.id,
        step="plan",
        message="fresh log",
        created_at=now - timedelta(days=2),
    )
    session.add_all([old_log, fresh_log])
    session.commit()

    stats = purge_expired_records(
        session,
        job_log_retention=timedelta(days=14),
        now=now,
    )
    assert stats == {"job_logs_purged": 1}

    remaining_logs = session.scalars(select(JobLog).where(JobLog.job_id == job.id)).all()
    assert [lg.message for lg in remaining_logs] == ["fresh log"]


def test_topological_wave_canary_ordering_and_parallel_telemetry() -> None:
    class DummyDev:
        def __init__(self, hostname: str, role: DeviceRole) -> None:
            self.hostname = hostname
            self.role = role

    def _make_target(hostname: str, role: DeviceRole) -> JobTarget:
        t = JobTarget(job_id=uuid.uuid4(), device_id=1, hostname=hostname, status=TargetStatus.PENDING)
        t.device = DummyDev(hostname, role)  # type: ignore[assignment]
        return t

    # Even if Spines are passed before Leaves, Wave ordering must put Canary Leaf first, Leaf batch second, Spines last
    raw_targets = [
        _make_target("spine-1.croc.lab", DeviceRole.SPINE),
        _make_target("leaf-1.croc.lab", DeviceRole.LEAF),
        _make_target("leaf-2.croc.lab", DeviceRole.LEAF),
        _make_target("leaf-3.croc.lab", DeviceRole.LEAF),
        _make_target("spine-2.croc.lab", DeviceRole.SPINE),
    ]
    waves = order_targets_into_waves(raw_targets)
    assert [w[0] for w in waves] == [
        "wave-1-canary-leaf",
        "wave-2-leaf-batch",
        "wave-3-spine-core",
    ]
    assert [t.hostname for t in waves[0][1]] == ["leaf-1.croc.lab"]
    assert [t.hostname for t in waves[1][1]] == ["leaf-2.croc.lab", "leaf-3.croc.lab"]
    assert [t.hostname for t in waves[2][1]] == ["spine-1.croc.lab", "spine-2.croc.lab"]

    # Parallel fleet CLI metric collection & gNMI auto-detection
    items = [
        ("parallel-1.lab", "cisco_iosxe", lambda _c: "CPU utilization for five seconds: 10%/1%; one minute: 15%; five minutes: 12%"),
        ("parallel-2.lab", "arista_eos", lambda _c: "%Cpu(s): 10.0 us, 2.0 sy, 0.0 ni, 88.0 id"),
        ("parallel-3.lab", "eltex_mes", lambda _c: "CPU utilization for five seconds: 9%/1%; one minute: 11%; five minutes: 8%"),
    ]
    res = collect_fleet_metrics_parallel(items, max_workers=3)
    assert res["parallel-1.lab"]["cpu_pct"] == 15.0
    assert res["parallel-2.lab"]["cpu_pct"] == 12.0
    assert res["parallel-3.lab"]["cpu_pct"] == 11.0
    for h, _, _ in items:
        clear_live_metrics(h)

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        gnmi_port = listener.getsockname()[1]
        gnmi_info = detect_optimal_telemetry_protocol(
            "127.0.0.1",
            "arista_eos",
            ssh_send=lambda _c: "Transport: grpc default, State: enabled, Port: 6030",
            gnmi_port=gnmi_port,
            timeout=0.2,
        )
    assert gnmi_info["protocol"] == "gnmi"
    assert gnmi_info["port"] == gnmi_port

    fallback_info = detect_optimal_telemetry_protocol(
        "127.0.0.1",
        "eltex_mes",
        timeout=0.05,
    )
    assert fallback_info["protocol"] == "ssh_cli"


def test_grafana_provisioning_and_docker_compose_validity() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    compose_data = yaml.safe_load((repo_root / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "grafana" in compose_data["services"]
    assert "grafana-data" in compose_data["volumes"]

    ds_path = repo_root / "monitoring/grafana/provisioning/datasources/datasources.yml"
    ds_data = yaml.safe_load(ds_path.read_text(encoding="utf-8"))
    ds_uids = {d["uid"] for d in ds_data["datasources"]}
    assert {"prometheus", "postgres-netops"} <= ds_uids

    dash_yaml_path = repo_root / "monitoring/grafana/dashboards/croc-dream-noc.yaml"
    dash_data = yaml.safe_load(dash_yaml_path.read_text(encoding="utf-8"))
    assert dash_data["uid"] == "croc-dream-noc"
    assert len(dash_data["panels"]) >= 6


def test_celery_priority_queues_and_distributed_ssh_lock(settings) -> None:
    from typing import Any

    from netops.network.ssh_pool import _DistributedDeviceLock, _get_device_lock
    from netops.worker.celery_app import (
        COLLECT_METRICS_TASK,
        FAIL_STALE_JOBS_TASK,
        GENERATE_REGULAR_REPORT_TASK,
        PURGE_EXPIRED_RECORDS_TASK,
        QUEUE_BACKGROUND,
        QUEUE_CRITICAL,
        QUEUE_DEFAULT,
        QUEUE_TELEMETRY,
        REFRESH_COLLECTOR_TARGETS_TASK,
        RUN_JOB_TASK,
        SCHEDULED_DRIFT_SCAN_TASK,
        CeleryJobDispatcher,
        create_celery_app,
    )

    app = create_celery_app(settings)
    assert app.conf.task_default_queue == QUEUE_DEFAULT
    routes = app.conf.task_routes
    assert routes[COLLECT_METRICS_TASK]["queue"] == QUEUE_TELEMETRY
    assert routes[REFRESH_COLLECTOR_TARGETS_TASK]["queue"] == QUEUE_TELEMETRY
    assert routes[SCHEDULED_DRIFT_SCAN_TASK]["queue"] == QUEUE_BACKGROUND
    assert routes[FAIL_STALE_JOBS_TASK]["queue"] == QUEUE_BACKGROUND
    assert routes[PURGE_EXPIRED_RECORDS_TASK]["queue"] == QUEUE_BACKGROUND
    assert routes[GENERATE_REGULAR_REPORT_TASK]["queue"] == QUEUE_BACKGROUND

    dispatched: list[dict[str, Any]] = []

    class FakeCeleryApp:
        def send_task(self, name: str, **opts: Any) -> None:
            dispatched.append({"name": name, **opts})

    disp = CeleryJobDispatcher(FakeCeleryApp())  # type: ignore[arg-type]
    j_deploy = uuid.uuid4()
    j_remed = uuid.uuid4()
    j_dry = uuid.uuid4()
    disp.dispatch(j_deploy, JobType.DEPLOY)
    disp.dispatch(j_remed, JobType.DRIFT_REMEDIATE)
    disp.dispatch(j_dry, JobType.DRY_RUN)
    assert dispatched[0] == {
        "name": RUN_JOB_TASK,
        "args": [str(j_deploy)],
        "task_id": str(j_deploy),
        "queue": QUEUE_CRITICAL,
    }
    assert dispatched[1]["queue"] == QUEUE_CRITICAL
    assert dispatched[2]["queue"] == QUEUE_DEFAULT

    # Distributed device lock reentrant behavior
    lock = _get_device_lock("10.250.99.1", 22)
    assert isinstance(lock, _DistributedDeviceLock)
    with lock:
        assert lock._depth == 1
        with lock:
            assert lock._depth == 2
        assert lock._depth == 1
    assert lock._depth == 0

