from __future__ import annotations

from datetime import UTC, datetime

from netops.telemetry.metrics import COLLECTED_AT, DEVICES_TOTAL, Sample, render


def test_help_and_type_go_once_per_metric() -> None:
    text = render(
        [
            Sample(DEVICES_TOTAL, 2, {"status": "IN_SYNC"}),
            Sample(DEVICES_TOTAL, 1, {"status": "DRIFT_DETECTED"}),
        ],
        None,
    )
    assert text.splitlines() == [
        f"# HELP {DEVICES_TOTAL} Количество устройств по статусам",
        f"# TYPE {DEVICES_TOTAL} gauge",
        f'{DEVICES_TOTAL}{{status="IN_SYNC"}} 2',
        f'{DEVICES_TOTAL}{{status="DRIFT_DETECTED"}} 1',
    ]


def test_collection_time_is_added() -> None:
    collected_at = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
    assert f"{COLLECTED_AT} {int(collected_at.timestamp())}" in render([], collected_at)


def test_quotes_and_newlines_in_labels_are_escaped() -> None:
    text = render([Sample("netops_test", 1, {"hostname": 'a"b\nc'})], None)
    assert 'netops_test{hostname="a\\"b\\nc"} 1' in text


def test_fractional_values_keep_the_dot() -> None:
    assert "netops_test 20.5" in render([Sample("netops_test", 20.5)], None)


def test_empty_result() -> None:
    assert render([], None) == ""


