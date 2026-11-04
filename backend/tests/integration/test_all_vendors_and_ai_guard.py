"""Integration tests for multi-vendor support (Arista, Cisco, Huawei) and AI Telemetry Guard."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from netops.api.app import create_app
from netops.enums import Platform, UserRole
from netops.intent.repository import IntentRepository
from netops.network.diff import HierConfigDiffEngine
from netops.network.normalization import ConfigNormalizer
from netops.network.rendering import JinjaConfigRenderer
from netops.settings import Settings
from tests.conftest import TOKENS

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_multi_vendor_rendering_and_diff(intent_repo: Path) -> None:
    """Verify that templates, normalization, and HierConfig diff work for Cisco, Arista, and Huawei."""
    templates_dir = PROJECT_ROOT / "templates"

    repo = IntentRepository(intent_repo, inventory_file="inventory.yaml")
    snapshot = repo.load()
    renderer = JinjaConfigRenderer(templates_dir)
    normalizer = ConfigNormalizer.from_file(None)
    diff_engine = HierConfigDiffEngine()

    platforms_tested = set()
    for dev in snapshot.inventory.devices:
        intent = snapshot.intent_for(dev.hostname)
        rendered = renderer.render(dev, intent)
        assert len(rendered) > 100, f"Rendered config for {dev.hostname} is too short"

        norm = normalizer.normalize(dev.platform, rendered)
        assert len(norm) > 50

        diff = diff_engine.compare(dev.platform, norm, norm)
        assert diff is not None
        assert diff.in_sync is True
        platforms_tested.add(dev.platform)

    # Also verify Huawei VRP template rendering, normalization, and diff
    sample_dev = snapshot.inventory.devices[0].model_copy(update={"platform": Platform.HUAWEI_VRP})
    sample_intent = snapshot.intent_for(sample_dev.hostname)
    hw_rendered = renderer.render(sample_dev, sample_intent)
    hw_norm = normalizer.normalize(Platform.HUAWEI_VRP, hw_rendered)
    hw_diff = diff_engine.compare(Platform.HUAWEI_VRP, hw_norm, hw_norm)
    assert hw_diff.in_sync is True
    platforms_tested.add(Platform.HUAWEI_VRP)

    assert Platform.ARISTA_EOS in platforms_tested
    assert Platform.CISCO_IOSXE in platforms_tested
    assert Platform.HUAWEI_VRP in platforms_tested


def test_ai_guard_api_endpoints(settings: Settings, engine) -> None:
    """Test AI Guard endpoints in the FastAPI app."""
    app = create_app(settings, engine=engine)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {TOKENS[UserRole.ADMIN]}"}

    # 1. Test Overview endpoint (supports both /api/v1/ai-guard/overview and /api/v1/insights/ai-guard/overview)
    resp = client.get("/api/v1/ai-guard/overview", headers=headers)
    assert resp.status_code == 200
    overview = resp.json()
    assert isinstance(overview, list)

    # 2. Test Simulate Anomaly endpoint
    sim_payload = {"hostname": "leaf-1.croc.lab", "anomaly_type": "blackhole"}
    sim_resp = client.post("/api/v1/ai-guard/simulate", json=sim_payload, headers=headers)
    assert sim_resp.status_code == 200
    assert sim_resp.json()["status"] == "ok"

    # 3. Test check device with anomaly
    devices_resp = client.get("/api/v1/devices", headers=headers)
    assert devices_resp.status_code == 200
    leaf1 = next((d for d in devices_resp.json() if d["hostname"] == "leaf-1.croc.lab"), None)
    if leaf1 is not None:
        check_resp = client.get(f"/api/v1/ai-guard/check/{leaf1['id']}", headers=headers)
        assert check_resp.status_code == 200
        data = check_resp.json()
        assert data["healthy"] is False
        assert "Критическое падение трафика" in data["problem"]

    # Clear anomaly
    client.post(
        "/api/v1/ai-guard/simulate",
        json={"hostname": "leaf-1.croc.lab", "anomaly_type": "clear"},
        headers=headers,
    )
