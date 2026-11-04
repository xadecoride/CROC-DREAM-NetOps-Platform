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


def test_chaos_port_down_and_custom_overrides(client: TestClient, lab_path: Path) -> None:
    client.post(
        "/api/v1/system/chaos", json={"scenario": "reset_lab"}, headers=auth(UserRole.OPERATOR)
    )
    res = client.post(
        "/api/v1/system/chaos",
        json={
            "scenario": "port_down",
            "target_hostname": "leaf-1.croc.lab",
            "interface_name": "GigabitEthernet2",
        },
        headers=auth(UserRole.OPERATOR),
    )
    assert res.status_code == 200
    assert "GigabitEthernet2" in res.json()["message"]
    cfg_text = (lab_path / "leaf-1.croc.lab.cfg").read_text(encoding="utf-8")
    assert "interface GigabitEthernet2\n" in cfg_text
    assert "\n shutdown\n" in cfg_text


def test_brownfield_discovery_extracts_v5_intent_triggers() -> None:
    from netops.enums import Platform
    from netops.services.discovery import _parse_brownfield_intent_from_config

    raw_cfg = """!
hostname leaf-99.custom.corp
!
interface Loopback0
 description Router ID
 ip address 10.255.0.99 255.255.255.255
 ip ospf 1 area 0
 no shutdown
!
interface GigabitEthernet2
 description Uplink to spine-1
 mtu 9000
 ip address 10.0.1.99 255.255.255.254
 ip access-group CORP-ACL-IN in
 ip ospf 1 area 0
 no shutdown
!
interface GigabitEthernet4
 description Server VLAN 250
 switchport
 switchport mode access
 switchport access vlan 250
 no shutdown
!
router bgp 65199
 bgp router-id 10.255.0.99
 neighbor 10.0.1.98 remote-as 65000
 !
 address-family ipv4
  network 10.255.0.99 mask 255.255.255.255
  neighbor 10.0.1.98 activate
 exit-address-family
 !
 address-family l2vpn evpn
  neighbor 10.0.1.98 activate
  vni 20250
 exit-address-family
!
ip access-list extended CORP-ACL-IN
 10 permit ospf any any
 20 permit tcp any any eq bgp
 30 deny ip any any
!
"""
    intent = _parse_brownfield_intent_from_config(
        hostname="leaf-99.custom.corp",
        cfg=raw_cfg,
        platform=Platform.CISCO_IOSXE,
    )
    assert intent is not None
    ifaces = {i["name"]: i for i in intent["interfaces"]}
    assert str(ifaces["GigabitEthernet2"]["ospf_area"]) == "0"
    assert ifaces["GigabitEthernet2"]["acl_in"] == "CORP-ACL-IN"
    assert ifaces["GigabitEthernet4"]["mode"] == "l2"
    assert ifaces["GigabitEthernet4"]["access_vlan"] == 250
    assert intent["bgp"]["asn"] == 65199
    assert intent["bgp"]["evpn"] is True
    assert intent["bgp"]["vxlan_vni"] == 20250
    assert "10.255.0.99/32" in intent["bgp"]["neighbors"][0]["announced_prefixes"]
    assert len(intent["acls"]) == 1
    assert intent["acls"][0]["name"] == "CORP-ACL-IN"
    assert intent["acls"][0]["rules"][0]["protocol"] == "ospf"


def test_emergency_offline_cli_uses_device_intent(client: TestClient) -> None:
    res_bgp = client.post(
        "/api/v1/emergency/command",
        json={"device": "leaf-1.croc.lab", "command": "show ip bgp summary"},
        headers=auth(UserRole.OPERATOR),
    )
    assert res_bgp.status_code == 200
    out_bgp = res_bgp.json()["output"]
    assert "local AS number 65101" in out_bgp
    assert "10.0.1.0" in out_bgp

    res_if = client.post(
        "/api/v1/emergency/command",
        json={"device": "leaf-1.croc.lab", "command": "show ip interface brief"},
        headers=auth(UserRole.OPERATOR),
    )
    assert res_if.status_code == 200
    out_if = res_if.json()["output"]
    assert "GigabitEthernet2" in out_if
    assert "10.0.1.1" in out_if

    # Non-diagnostic, abbreviated, pipe-redirect, or config-mode commands must be blocked by CLI allowlist
    for forbidden_cmd in (
        "reload",
        "rel",
        "config t",
        "clear ip bgp *",
        "debug all",
        "undo bgp 65001",
        "show run | redirect flash:x",
        "show version; reload",
    ):
        res_bad = client.post(
            "/api/v1/emergency/command",
            json={"device": "leaf-1.croc.lab", "command": forbidden_cmd},
            headers=auth(UserRole.OPERATOR),
        )
        assert res_bad.status_code == 400

    # Viewer cannot trigger AI Guard anomaly injection (requires Operator+)
    res_sim_viewer = client.post(
        "/api/v1/ai-guard/simulate",
        json={"hostname": "leaf-1.croc.lab", "anomaly_type": "blackhole"},
        headers=auth(UserRole.VIEWER),
    )
    assert res_sim_viewer.status_code == 403



