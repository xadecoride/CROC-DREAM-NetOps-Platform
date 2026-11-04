from __future__ import annotations

from fastapi.testclient import TestClient


def test_prometheus_metrics_endpoint(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]


def test_api_v1_metrics_endpoint(client: TestClient) -> None:
    resp = client.get("/api/v1/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    text = resp.text
    assert "# HELP netops_up" in text
    assert "netops_up 1" in text
    assert "netops_devices_total" in text
    assert "netops_jobs_total" in text
    assert "netops_drift_records_total" in text
