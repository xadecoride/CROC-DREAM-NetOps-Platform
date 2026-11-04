"""Интеграционные тесты мультиуровневой топологии и конвейера коммутации."""

from __future__ import annotations

from fastapi.testclient import TestClient

from netops.enums import UserRole
from tests.conftest import auth


def test_topology_graph_and_commutation_trace_api(client: TestClient, devices: dict) -> None:
    headers = auth(UserRole.OWNER)

    res = client.get("/api/v1/topology/graph", headers=headers)
    assert res.status_code == 200
    graph = res.json()
    assert len(graph["nodes"]) >= 2
    assert len(graph["links"]) >= 1

    node_ids = [n["id"] for n in graph["nodes"]]
    src_id, dst_id = node_ids[0], node_ids[-1]

    trace_res = client.get(
        "/api/v1/topology/trace",
        params={"source_id": src_id, "target_id": dst_id},
        headers=headers,
    )
    assert trace_res.status_code == 200
    trace = trace_res.json()
    assert trace["reachable"] is True
    assert len(trace["hops"]) >= 2
    assert len(trace["ecmp_paths"]) >= 1

    # Проверим создание пользовательской связи, переключение её статуса и удаление
    create_res = client.post(
        "/api/v1/topology/links",
        json={
            "source_id": src_id,
            "target_id": dst_id,
            "source_port": "10GE1/0/48",
            "target_port": "10GE1/0/48",
            "layer": "L2_LLDP",
            "protocol": "M-LAG Peer-Link",
        },
        headers=headers,
    )
    assert create_res.status_code == 201
    custom_link = create_res.json()
    assert custom_link["admin_state"] == "UP"

    toggle_res = client.patch(
        f"/api/v1/topology/links/{custom_link['id']}/toggle",
        json={"admin_state": "SHUTDOWN", "reason": "Тест failover ECMP"},
        headers=headers,
    )
    assert toggle_res.status_code == 200
    assert toggle_res.json()["admin_state"] == "SHUTDOWN"

    del_res = client.delete(
        f"/api/v1/topology/links/{custom_link['id']}",
        headers=headers,
    )
    assert del_res.status_code == 200
    assert del_res.json()["deleted"] is True


def test_spof_cut_edge_guard_and_force_override(client: TestClient, devices: dict) -> None:
    headers = auth(UserRole.OWNER)
    graph = client.get("/api/v1/topology/graph", headers=headers).json()
    nodes = graph["nodes"]
    links = graph["links"]

    # Find a leaf or single-homed node and shut down all but one of its links so the last link becomes a Cut-Edge (SPOF)
    target_node = next((n for n in nodes if n["role"] == "leaf"), nodes[-1])
    incident_links = [
        lnk
        for lnk in links
        if lnk["source_id"] == target_node["id"] or lnk["target_id"] == target_node["id"]
    ]
    assert len(incident_links) >= 1

    # Shut down all except the last incident link
    for lnk in incident_links[:-1]:
        r = client.patch(
            f"/api/v1/topology/links/{lnk['id']}/toggle",
            json={"admin_state": "SHUTDOWN", "reason": "Подготовка SPOF теста"},
            headers=headers,
        )
        assert r.status_code == 200

    last_link = incident_links[-1]

    # With strict_spof_guard=True and force=False, shutting down the cut-edge must return 409 Conflict
    conflict_res = client.patch(
        f"/api/v1/topology/links/{last_link['id']}/toggle",
        json={"admin_state": "SHUTDOWN", "strict_spof_guard": True, "force": False},
        headers=headers,
    )
    assert conflict_res.status_code == 409
    detail = conflict_res.json()["detail"]
    assert detail["spof_warning"] is True
    assert target_node["hostname"] in detail["isolated_nodes"]

    # With force=True, it succeeds and returns spof_warning=True
    force_res = client.patch(
        f"/api/v1/topology/links/{last_link['id']}/toggle",
        json={
            "admin_state": "SHUTDOWN",
            "strict_spof_guard": True,
            "force": True,
            "auto_revert_seconds": 120,
        },
        headers=headers,
    )
    assert force_res.status_code == 200
    body = force_res.json()
    assert body["admin_state"] == "SHUTDOWN"
    assert body["spof_warning"] is True
    assert body["auto_revert_at"] is not None

    # Restore links back to UP
    for lnk in incident_links:
        client.patch(
            f"/api/v1/topology/links/{lnk['id']}/toggle",
            json={"admin_state": "UP"},
            headers=headers,
        )


def test_node_bgp_drain_and_ecmp_avoidance(client: TestClient, devices: dict) -> None:
    headers = auth(UserRole.OWNER)
    graph = client.get("/api/v1/topology/graph", headers=headers).json()
    spines = [n for n in graph["nodes"] if n["role"] == "spine"]
    leaves = [n for n in graph["nodes"] if n["role"] == "leaf"]
    assert len(spines) >= 1 and len(leaves) >= 2

    # Ensure second spine link exists between leaf_a and leaf_b if only 1 spine in fixture
    leaf_a, leaf_b = leaves[0], leaves[1]
    spine_1 = spines[0]

    # Drain spine_1
    drain_res = client.post(
        f"/api/v1/topology/nodes/{spine_1['id']}/drain",
        json={"drained": True, "reason": "Плановое обновление ОС коммутатора"},
        headers=headers,
    )
    assert drain_res.status_code == 200
    assert drain_res.json()["drained"] is True

    # Create an alternative direct custom link or check second spine so non-drained path exists
    alt_link_res = client.post(
        "/api/v1/topology/links",
        json={
            "id": "link-alt-bypass",
            "source_id": leaf_a["id"],
            "target_id": leaf_b["id"],
            "source_port": "eth10",
            "target_port": "eth10",
            "layer": "L3_SUBNET",
        },
        headers=headers,
    )
    assert alt_link_res.status_code == 201

    trace = client.get(
        "/api/v1/topology/trace",
        params={"source_id": leaf_a["id"], "target_id": leaf_b["id"]},
        headers=headers,
    ).json()
    assert trace["reachable"] is True
    hop_ids = [h["device_id"] for h in trace["hops"]]
    assert spine_1["id"] not in hop_ids

    # Remove bypass link: if spine_1 is the ONLY transit node, fallback uses spine_1 with GSHUT stage
    client.delete("/api/v1/topology/links/link-alt-bypass", headers=headers)
    for sp in spines[1:]:
        client.post(f"/api/v1/topology/nodes/{sp['id']}/drain", json={"drained": True}, headers=headers)

    fallback_trace = client.get(
        "/api/v1/topology/trace",
        params={"source_id": leaf_a["id"], "target_id": leaf_b["id"]},
        headers=headers,
    ).json()
    assert fallback_trace["reachable"] is True
    transit_stages = [
        stage
        for h in fallback_trace["hops"]
        if h["device_id"] == spine_1["id"]
        for stage in h["pipeline_stages"]
    ]
    assert any("GSHUT" in s for s in transit_stages)

    # Undrain all spines
    for sp in spines:
        client.post(f"/api/v1/topology/nodes/{sp['id']}/drain", json={"drained": False}, headers=headers)


def test_topology_checkpoints_create_restore_delete(client: TestClient, devices: dict) -> None:
    headers = auth(UserRole.OWNER)
    graph = client.get("/api/v1/topology/graph", headers=headers).json()
    first_link_id = graph["links"][0]["id"]
    first_node_id = graph["nodes"][0]["id"]

    # Create Golden Baseline checkpoint when all links are UP and no nodes are drained
    cp_res = client.post(
        "/api/v1/topology/checkpoints",
        json={"name": "Перед учениями DRP"},
        headers=headers,
    )
    assert cp_res.status_code == 201
    cp = cp_res.json()
    assert cp["name"] == "Перед учениями DRP"

    # List checkpoints
    list_res = client.get("/api/v1/topology/checkpoints", headers=headers)
    assert list_res.status_code == 200
    assert any(item["id"] == cp["id"] for item in list_res.json())

    # Mutate topology: shutdown link and drain node
    client.patch(
        f"/api/v1/topology/links/{first_link_id}/toggle",
        json={"admin_state": "SHUTDOWN", "force": True},
        headers=headers,
    )
    client.post(
        f"/api/v1/topology/nodes/{first_node_id}/drain",
        json={"drained": True},
        headers=headers,
    )

    mutated_graph = client.get("/api/v1/topology/graph", headers=headers).json()
    mut_link = next(lnk for lnk in mutated_graph["links"] if lnk["id"] == first_link_id)
    mut_node = next(n for n in mutated_graph["nodes"] if n["id"] == first_node_id)
    assert mut_link["admin_state"] == "SHUTDOWN"
    assert mut_node["drained"] is True

    # Restore checkpoint
    restore_res = client.post(
        f"/api/v1/topology/checkpoints/{cp['id']}/restore",
        headers=headers,
    )
    assert restore_res.status_code == 200
    assert restore_res.json()["restored"] is True

    restored_graph = client.get("/api/v1/topology/graph", headers=headers).json()
    rest_link = next(lnk for lnk in restored_graph["links"] if lnk["id"] == first_link_id)
    rest_node = next(n for n in restored_graph["nodes"] if n["id"] == first_node_id)
    assert rest_link["admin_state"] == "UP"
    assert rest_node["drained"] is False

    # Delete checkpoint
    del_res = client.delete(f"/api/v1/topology/checkpoints/{cp['id']}", headers=headers)
    assert del_res.status_code == 200
    assert del_res.json()["deleted"] is True

