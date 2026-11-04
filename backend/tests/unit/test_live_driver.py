from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from netops.enums import Platform
from netops.network.base import ChangePlan, Credentials, DeviceTarget
from netops.network.scrapli_driver import (
    HUAWEI_COMMIT_LIST,
    SCRAPLI_PLATFORMS,
    TRANSACTIONAL_PLATFORMS,
    ScrapliNetworkDriver,
    _latest_commit_id,
)

PLAN = ChangePlan(remediation="hostname x\n", rollback="hostname y\n", intended="hostname x\n")


def _target(platform: Platform, hostname: str = "node.croc.lab") -> DeviceTarget:
    return DeviceTarget(
        hostname=hostname,
        platform=platform,
        host="127.0.0.1",
        port=2211,
        credentials=Credentials("admin", "admin"),
    )


def test_every_platform_has_a_scrapli_name() -> None:
    assert set(SCRAPLI_PLATFORMS) == set(Platform)


@pytest.mark.parametrize("platform", sorted(set(Platform) - TRANSACTIONAL_PLATFORMS))
def test_deploy_is_refused_without_transaction_support(platform: Platform) -> None:
    with pytest.raises(RuntimeError, match="не реализовано"):
        ScrapliNetworkDriver().apply(_target(platform), PLAN, confirm_timeout=180)


def test_empty_patch_touches_nothing() -> None:
    empty = ChangePlan(remediation="", rollback="", intended="")
    ScrapliNetworkDriver().apply(_target(Platform.HUAWEI_VRP), empty, confirm_timeout=180)


class FakeConnection:
    """Записывает команды вместо отправки на устройство."""

    def __init__(self, commit_list: str = "", default_output: str = "") -> None:
        self.commands: list[str] = []
        self.configs: list[list[str]] = []
        self._commit_list = commit_list
        self._default_output = default_output

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def send_command(self, command: str) -> object:
        self.commands.append(command)
        text = self._commit_list if command == HUAWEI_COMMIT_LIST else self._default_output
        return type("R", (), {"result": text, "failed": False})()

    def send_configs(self, lines: Sequence[str]) -> object:
        self.configs.append(list(lines))
        return type("R", (), {"result": "", "failed": False})()


COMMIT_LIST = """\
 CommitId  Label     User
 1000000013           admin
 1000000012           admin
"""


def _driver_with(conn: FakeConnection) -> ScrapliNetworkDriver:
    driver = ScrapliNetworkDriver()
    driver._get_connection = lambda target: conn  # type: ignore[method-assign]
    return driver


def test_huawei_apply_remembers_commit_point() -> None:
    conn = FakeConnection(COMMIT_LIST)
    driver = _driver_with(conn)

    driver.apply(_target(Platform.HUAWEI_VRP), PLAN, confirm_timeout=180)

    assert conn.commands == [HUAWEI_COMMIT_LIST, "commit"]
    assert conn.configs == [["hostname x"]]
    assert driver._commit_ids["node.croc.lab"] == "1000000013"


def test_huawei_rollback_returns_to_that_commit() -> None:
    conn = FakeConnection(COMMIT_LIST)
    driver = _driver_with(conn)
    driver.apply(_target(Platform.HUAWEI_VRP), PLAN, confirm_timeout=180)

    driver.rollback(_target(Platform.HUAWEI_VRP), PLAN)

    assert conn.commands[-3:] == [
        "system-view",
        "rollback configuration to commit-id 1000000013",
        "commit",
    ]


def test_huawei_rollback_without_commit_id_undoes_last_change() -> None:
    conn = FakeConnection()
    driver = _driver_with(conn)
    driver.apply(_target(Platform.HUAWEI_VRP), PLAN, confirm_timeout=180)

    driver.rollback(_target(Platform.HUAWEI_VRP), PLAN)

    assert "rollback configuration last 1" in conn.commands


def test_huawei_confirm_only_forgets_the_commit_point() -> None:
    conn = FakeConnection(COMMIT_LIST)
    driver = _driver_with(conn)
    driver.apply(_target(Platform.HUAWEI_VRP), PLAN, confirm_timeout=180)
    before = len(conn.commands)

    driver.confirm(_target(Platform.HUAWEI_VRP))

    assert len(conn.commands) == before
    assert "node.croc.lab" not in driver._commit_ids


def test_latest_commit_id_parsing() -> None:
    assert _latest_commit_id("") is None
    assert _latest_commit_id("No commits recorded") is None
    assert _latest_commit_id(COMMIT_LIST) == "1000000013"
    single = " 1000000042 system user "
    assert _latest_commit_id(single) == "1000000042"


class _ChunkedShell:
    def __init__(self, chunks: list[tuple[int, bytes]]) -> None:
        self._chunks = list(chunks)
        self._polls = 0
        self.sent: list[str] = []

    def send(self, data: str) -> None:
        self.sent.append(data)

    def recv_ready(self) -> bool:
        self._polls += 1
        return bool(self._chunks and self._polls >= self._chunks[0][0])

    def recv(self, _nbytes: int) -> bytes:
        _, payload = self._chunks.pop(0)
        return payload


def test_send_command_does_not_truncate_on_huawei_hash_separator() -> None:
    from netops.network.scrapli_driver import _ParamikoConnAdapter

    adapter = _ParamikoConnAdapter(_target(Platform.HUAWEI_VRP, "leaf-1.croc.lab"), timeout=5)
    adapter.shell = _ChunkedShell(
        [
            (2, b"display current-configuration\r\n#\r\nsysname leaf-1.croc.lab\r\n#\r\n"),
            (5, b"interface GE1/0/1\r\n#\r\n<leaf-1.croc.lab>"),
        ]
    )
    res = adapter.send_command("display current-configuration")
    assert "interface GE1/0/1" in res.result
    assert res.result.rstrip().endswith("<leaf-1.croc.lab>")


def test_send_command_waits_past_echo_pause_for_arista_top() -> None:
    from netops.network.scrapli_driver import _ParamikoConnAdapter

    adapter = _ParamikoConnAdapter(_target(Platform.ARISTA_EOS, "spine-2.croc.lab"), timeout=5)
    adapter.shell = _ChunkedShell(
        [
            (2, b"show processes top once\r\n"),
            (6, b"%Cpu(s): 10.0 us, 90.0 id\r\nspine-2.croc.lab#"),
        ]
    )
    res = adapter.send_command("show processes top once")
    assert "90.0 id" in res.result
    assert res.result.rstrip().endswith("spine-2.croc.lab#")


def test_paramiko_adapter_reuses_pooled_session_and_skips_hidden_jump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from netops.network import scrapli_driver
    from netops.network.scrapli_driver import _ParamikoConnAdapter

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    scrapli_driver._SESSION_POOL.clear()
    connect_calls = 0

    class FakeTransport:
        def is_active(self) -> bool:
            return True

    class FakeShell:
        closed = False

        def close(self) -> None:
            self.closed = True

    class FakeClient:
        def get_transport(self) -> FakeTransport:
            return FakeTransport()

        def close(self) -> None:
            pass

    def fake_establish(self: _ParamikoConnAdapter) -> None:
        nonlocal connect_calls
        assert self.proxy_jump is None
        connect_calls += 1
        self.client = FakeClient()
        self.shell = FakeShell()
        self.authenticated_password = "admin"

    monkeypatch.setattr(_ParamikoConnAdapter, "_establish_fresh_session", fake_establish)
    tgt = _target(Platform.HUAWEI_VRP, "leaf-1.croc.lab")

    with _ParamikoConnAdapter(tgt, timeout=5) as c1:
        s1 = c1.shell
    with _ParamikoConnAdapter(tgt, timeout=5) as c2:
        s2 = c2.shell

    assert connect_calls == 1
    assert s1 is s2
    scrapli_driver._SESSION_POOL.clear()


def test_parse_ping_loss_and_cisco_interface_table() -> None:
    from netops.network.table_parsers import parse_interface_table, parse_ping_loss

    assert parse_ping_loss("5 packets transmitted, 5 received, 0% packet loss, time 4ms") == 0.0
    assert parse_ping_loss("5 packets transmitted, 0 received, 100% packet loss, time 4000ms") == 100.0
    assert parse_ping_loss("Success rate is 100 percent (5/5)") == 0.0
    assert parse_ping_loss("Success rate is 80% (4/5)") == 20.0

    cisco_out = (
        "Interface              IP-Address      OK? Method Status                Protocol\n"
        "GigabitEthernet1       10.0.0.1        YES NVRAM  up                    up\n"
        "GigabitEthernet2       10.0.0.2        YES manual administratively down down\n"
    )
    ifaces = parse_interface_table(Platform.CISCO_IOSXE, cisco_out)
    assert ifaces["GigabitEthernet1"].status == "up"
    assert ifaces["GigabitEthernet1"].protocol == "up"
    assert ifaces["GigabitEthernet2"].status == "down"
    assert ifaces["GigabitEthernet2"].protocol == "down"


def test_block2_services_regressions(tmp_path: Path) -> None:
    from netops.intent.models import TelemetryMetricSpec
    from netops.services.git_sot import _git_auth_cmd_and_env, _scrub_secret
    from netops.services.llm import _check_interface_risk

    # Mixed 'no shutdown' and 'shutdown' on separate lines must still flag CRITICAL
    lvl, pts, _ = _check_interface_risk("interface Gi1\n no shutdown\ninterface Gi2\n shutdown")
    assert lvl == "CRITICAL"
    assert pts

    # Only 'no shutdown' should not flag CRITICAL
    lvl_safe, _, _ = _check_interface_risk("interface Gi1\n no shutdown")
    assert lvl_safe is None

    # Git auth env uses GIT_CONFIG_* instead of command-line -c args
    prefix, env, clean = _git_auth_cmd_and_env(
        "https://gitlab.example.com/org/repo.git", "secret_pat_123", tmp_path
    )
    assert prefix == ["git"]
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "http.https://gitlab.example.com/.extraheader"
    assert "Authorization: Basic " in env["GIT_CONFIG_VALUE_0"]
    assert clean == "https://gitlab.example.com/org/repo.git"

    scrubbed = _scrub_secret(f"Header {env['GIT_CONFIG_VALUE_0']} failed", "secret_pat_123")
    assert "secret_pat_123" not in scrubbed
    assert "***" in scrubbed

    # IntentModel subclasses support .get() and []
    spec = TelemetryMetricSpec(command="show cpu", parser="parse_cisco_cpu")
    assert spec.get("command") == "show cpu"
    assert spec["parser"] == "parse_cisco_cpu"
    assert spec.get("nonexistent", "fallback") == "fallback"


def test_block3_and_block4_regressions(tmp_path: Path) -> None:
    from netops.enums import DeviceRole, Platform
    from netops.intent.models import BgpIntent, BgpNeighborIntent, DeviceIntent, InventoryDevice
    from netops.intent.netbox import load_netbox_inventory
    from netops.network.rendering import JinjaConfigRenderer

    # 1. InventoryDevice._normalize_stand does not mutate caller's dict in place
    raw_input = {"hostname": "spine-1.croc.lab", "management_ip": "10.0.0.1", "platform": "cisco_iosxe", "role": "spine", "auth_profile": "lab", "site": "dc1"}
    dev = InventoryDevice.model_validate(raw_input)
    assert dev.fabric_id == "dc1"
    assert "fabric_id" not in raw_input

    # 2. JinjaConfigRenderer invalidates cache when BGP neighbor password or template file changes
    tpl_dir = tmp_path / "templates" / "cisco_iosxe"
    tpl_dir.mkdir(parents=True)
    bgp_j2 = tpl_dir / "bgp.j2"
    bgp_j2.write_text(
        "router bgp {{ intent.bgp.asn }}\n"
        "{% for n in intent.bgp.neighbors %}"
        " neighbor {{ n.peer_ip }} password {{ n.password or 'NONE' }}\n"
        "{% endfor %}",
        encoding="utf-8",
    )
    renderer = JinjaConfigRenderer(tmp_path / "templates")
    intent1 = DeviceIntent(
        hostname="spine-1.croc.lab",
        bgp=BgpIntent(
            asn=65001,
            router_id="10.0.0.1",
            neighbors=[BgpNeighborIntent(peer_ip="10.0.0.2", remote_asn=65002, password="passA")],
        ),
    )
    intent2 = DeviceIntent(
        hostname="spine-1.croc.lab",
        bgp=BgpIntent(
            asn=65001,
            router_id="10.0.0.1",
            neighbors=[BgpNeighborIntent(peer_ip="10.0.0.2", remote_asn=65002, password="passB")],
        ),
    )
    out1 = renderer.render(dev, intent1)
    out2 = renderer.render(dev, intent2)
    assert "password passA" in out1
    assert "password passB" in out2

    # 3. NetBox loader skips malformed non-dict / None fields without crashing
    def fake_fetch(_url: str, _token: str) -> dict:
        return {
            "results": [
                {"name": "bad-dev-1", "primary_ip4": {"address": None}, "platform": None},
                {
                    "name": "good-dev.croc.lab",
                    "primary_ip4": {"address": "10.0.0.99/24"},
                    "platform": {"slug": "arista_eos"},
                    "role": {"slug": "leaf"},
                    "site": {"slug": "dc1"},
                },
            ],
            "next": None,
        }

    inv = load_netbox_inventory("http://netbox.local", "tok", fetch=fake_fetch)
    assert len(inv.devices) == 1
    assert inv.devices[0].hostname == "good-dev.croc.lab"
    assert inv.devices[0].platform == Platform.ARISTA_EOS
    assert inv.devices[0].role == DeviceRole.LEAF


