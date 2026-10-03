from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from netops.enums import UserRole
from tests.conftest import auth


def test_viewer_cannot_break_the_lab(client: TestClient, lab_path: Path) -> None:
    config = lab_path / "leaf-1.croc.lab.cfg"
    before = config.read_text()
    response = client.post(
        "/api/v1/system/chaos", json={"scenario": "reset_lab"}, headers=auth(UserRole.VIEWER)
    )
    assert response.status_code == 403
    assert config.read_text() == before


def test_reset_restores_intended_configs(client: TestClient, lab_path: Path) -> None:
    config = lab_path / "leaf-1.croc.lab.cfg"
    config.write_text("hostname leaf-1.croc.lab\n")
    response = client.post(
        "/api/v1/system/chaos", json={"scenario": "reset_lab"}, headers=auth(UserRole.OPERATOR)
    )
    assert response.status_code == 200
    assert "router bgp 65101" in config.read_text()
