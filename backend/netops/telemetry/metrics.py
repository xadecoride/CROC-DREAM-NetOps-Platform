from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any

# Каталог метрик: имена и описания попадают в вывод /metrics (строки HELP и TYPE).
DEVICE_STATUS = "netops_device_status"
DEVICES_TOTAL = "netops_devices_total"
DEVICE_REACHABLE = "netops_device_reachable"
JOBS_TOTAL = "netops_jobs_total"
JOB_LAST_FINISHED = "netops_job_last_finished_timestamp_seconds"
DRIFT_DEVICES = "netops_drift_devices"
BGP_SESSION_UP = "netops_bgp_session_up"
BGP_PREFIXES_ACCEPTED = "netops_bgp_prefixes_accepted"
INTERFACE_UP = "netops_interface_up"
PING_LOSS = "netops_ping_loss_percent"
CPU_USAGE = "netops_cpu_usage_percent"
MEMORY_USAGE = "netops_memory_usage_percent"
UPLINK_UTIL = "netops_uplink_util_percent"
COLLECTED_AT = "netops_metrics_collected_timestamp_seconds"

# Метрики серверов и приложений стендов (R4)
SERVER_CPU_UTIL = "netops_server_cpu_utilization_percent"
SERVER_MEMORY_UTIL = "netops_server_memory_utilization_percent"
SERVER_DISK_UTIL = "netops_server_disk_utilization_percent"
SERVER_NETWORK_RATE = "netops_server_network_rate_mbps"
SERVER_LOAD_1M = "netops_server_load_1m"
SERVER_TCP_RETRANSMITS = "netops_server_tcp_retransmits_total"
SERVER_CONNTRACK_UTIL = "netops_server_conntrack_utilization_percent"

# Алиасы и именованные метрики сетевых коммутаторов и L2-хабов для Grafana NOC (R4)
DEVICE_CPU_UTIL = "netops_device_cpu_utilization_percent"
DEVICE_MEMORY_UTIL = "netops_device_memory_utilization_percent"
DEVICE_UPLINK_UTIL = "netops_device_uplink_utilization_percent"
INTERFACE_ERRORS = "netops_interface_errors_total"
FDB_MAC_COUNT = "netops_fdb_mac_entries_total"
DRIFT_DEVICES_TOTAL = "netops_drift_devices_total"

HELP: Mapping[str, str] = MappingProxyType(
    {
        DEVICE_STATUS: "Текущий статус устройства: 1 у текущего статуса, 0 у остальных",
        DEVICES_TOTAL: "Количество устройств по статусам",
        DEVICE_REACHABLE: "Устройство ответило на опрос телеметрии",
        JOBS_TOTAL: "Количество задач по типу и статусу",
        JOB_LAST_FINISHED: "Время завершения последней задачи этого типа",
        DRIFT_DEVICES: "Количество устройств с обнаруженным дрейфом",
        DRIFT_DEVICES_TOTAL: "Количество устройств с обнаруженным дрейфом конфигурации",
        BGP_SESSION_UP: "BGP-сессия в состоянии Established",
        BGP_PREFIXES_ACCEPTED: "Принятых префиксов от соседа",
        INTERFACE_UP: "Интерфейс в состоянии up/up",
        PING_LOSS: "Потери ping до соседа, проценты",
        CPU_USAGE: "Загрузка CPU устройства, проценты",
        MEMORY_USAGE: "Загрузка RAM устройства, проценты",
        UPLINK_UTIL: "Загрузка аплинка устройства, проценты",
        DEVICE_CPU_UTIL: "Загрузка CPU сетевого коммутатора, проценты",
        DEVICE_MEMORY_UTIL: "Загрузка RAM сетевого коммутатора, проценты",
        DEVICE_UPLINK_UTIL: "Загрузка аплинка сетевого коммутатора, проценты",
        INTERFACE_ERRORS: "Суммарное число ошибок CRC/drop на интерфейсах коммутатора или хаба",
        FDB_MAC_COUNT: "Число изученных MAC-адресов в таблице коммутации (FDB)",
        SERVER_CPU_UTIL: "Загрузка CPU серверов и стендовых приложений, проценты",
        SERVER_MEMORY_UTIL: "Загрузка RAM серверов и стендовых приложений, проценты",
        SERVER_DISK_UTIL: "Загрузка дискового пространства серверов, проценты",
        SERVER_NETWORK_RATE: "Сетевой трафик серверов, Мбит/с",
        SERVER_LOAD_1M: "Средняя загрузка сервера за 1 минуту (Load Average 1m)",
        SERVER_TCP_RETRANSMITS: "Количество повторных передач TCP-сегментов на сервере",
        SERVER_CONNTRACK_UTIL: "Заполнение таблицы отслеживания соединений ядра (conntrack), проценты",
        COLLECTED_AT: "Время последнего сбора метрик",
        "netops_telemetry_active_protocol": "Активный канал телеметрии устройства (1 у текущего протокола)",
        "netops_telemetry_fallback_events_total": "Число зафиксированных смен протокола телеметрии (фоллбэков)",
    }
)


@dataclass(frozen=True, slots=True)
class Sample:
    name: str
    value: float
    labels: Mapping[str, str] = field(default_factory=dict)


def render(samples: list[Sample], collected_at: datetime | None) -> str:
    from collections import defaultdict

    lines: list[str] = []
    if collected_at is not None:
        samples = [*samples, Sample(COLLECTED_AT, collected_at.timestamp())]
    by_name: dict[str, list[Sample]] = defaultdict(list)
    for sample in samples:
        by_name[sample.name].append(sample)
    for name in sorted(by_name):
        lines.append(f"# HELP {name} {HELP.get(name, name)}")
        lines.append(f"# TYPE {name} gauge")
        lines.extend(_line(sample) for sample in by_name[name])
    return "\n".join(lines) + "\n" if lines else ""


def _line(sample: Sample) -> str:
    pairs = ",".join(f'{key}="{_escape(value)}"' for key, value in sorted(sample.labels.items()))
    labels = f"{{{pairs}}}" if pairs else ""
    return f"{sample.name}{labels} {_number(sample.value)}"


def _number(value: float) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else repr(number)


def _escape(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
