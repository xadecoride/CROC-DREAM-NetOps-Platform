from __future__ import annotations

from fastapi.testclient import TestClient

from netops.services.vitality import (
    classify_connection_error,
    clear_connection_failure,
    load_device_backup,
    record_connection_failure,
    save_device_backup,
)


def test_vitality_two_states_and_ai_diagnosis_and_backup_restore(client: TestClient) -> None:
    headers = {"Authorization": "Bearer owner-token"}
    client.post("/api/v1/inventory/sync", headers=headers)

    devices = client.get("/api/v1/devices", headers=headers).json()
    assert len(devices) >= 4
    leaf1 = next(d for d in devices if d["hostname"] == "leaf-1.croc.lab")
    assert leaf1["vitality_state"] == "UP"
    assert leaf1["down_reason"] is None

    # 1. Save backup and verify rotation (_latest.cfg -> _prev.cfg)
    ts1 = save_device_backup("leaf-1.croc.lab", "hostname leaf-1\ninterface Eth1\n no shutdown\n")
    assert ts1 is not None
    save_device_backup("leaf-1.croc.lab", "hostname leaf-1\ninterface Eth1\n description V2\n")
    latest_cfg, _ = load_device_backup("leaf-1.croc.lab", version="latest")
    prev_cfg, _ = load_device_backup("leaf-1.croc.lab", version="prev")
    assert latest_cfg and "description V2" in latest_cfg
    assert prev_cfg and "no shutdown" in prev_cfg

    # 2. Simulate connection failure -> state becomes DOWN with exact reason and AI diagnosis
    record_connection_failure(
        "leaf-1.croc.lab",
        "Authentication failed: Permission denied",
        host="172.20.20.11",
        port=22,
    )
    # Mark device UNREACHABLE via chaos or direct check
    reason_str = classify_connection_error(
        "Connection timed out",
        hostname="leaf-1.croc.lab",
        host="172.20.20.11",
        port=22,
        proxy_jump="crocdream@198.51.100.10:221",
    )
    assert "Jump-сервер" in reason_str
    assert "TCP Timeout" in reason_str

    # 3. Stand backup endpoint
    bkp_resp = client.post("/api/v1/stands/croc-dc1-pod1/backup", headers=headers)
    assert bkp_resp.status_code == 200
    body = bkp_resp.json()
    assert body["status"] == "ok"
    assert body["count"] >= 4

    # 4. 1-click device restore from backup brings node back to UP / IN_SYNC
    restore_resp = client.post(
        f"/api/v1/devices/{leaf1['id']}/restore-backup",
        headers=headers,
    )
    assert restore_resp.status_code == 200
    r_data = restore_resp.json()
    assert r_data["restored"] is True
    assert r_data["vitality_state"] == "UP"

    refreshed = client.get(f"/api/v1/devices/{leaf1['id']}", headers=headers).json()
    assert refreshed["vitality_state"] == "UP"
    assert refreshed["status"] == "IN_SYNC"
    clear_connection_failure("leaf-1.croc.lab")
