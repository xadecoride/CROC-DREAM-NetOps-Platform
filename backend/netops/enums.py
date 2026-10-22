from __future__ import annotations

from enum import StrEnum


class Platform(StrEnum):
    CISCO_IOSXE = "cisco_iosxe"
    ARISTA_EOS = "arista_eos"
    HUAWEI_VRP = "huawei_vrp"
    JUNIPER_JUNOS = "juniper_junos"
    ELTEX_ESR = "eltex_esr"
    ELTEX_MES = "eltex_mes"
    YADRO_KORNFE = "yadro_kornfe"
    LINUX_SERVER = "linux_server"


class DeviceRole(StrEnum):
    SPINE = "spine"
    LEAF = "leaf"
    BORDER = "border"
    BORDER_FIREWALL = "border_firewall"
    SERVER = "server"


class DeviceStatus(StrEnum):
    # UNKNOWN нет в ТЗ: устройство ещё не проверялось или состояние неизвестно после
    # неудачного отката.
    UNKNOWN = "UNKNOWN"
    IN_SYNC = "IN_SYNC"
    DRIFT_DETECTED = "DRIFT_DETECTED"
    UNREACHABLE = "UNREACHABLE"
    IN_PROGRESS = "IN_PROGRESS"


class DriftStatus(StrEnum):
    IN_SYNC = "IN_SYNC"
    DRIFT_DETECTED = "DRIFT_DETECTED"
    UNREACHABLE = "UNREACHABLE"


class JobType(StrEnum):
    DRY_RUN = "DRY_RUN"
    DEPLOY = "DEPLOY"
    DRIFT_SCAN = "DRIFT_SCAN"
    DRIFT_REMEDIATE = "DRIFT_REMEDIATE"

    @property
    def changes_devices(self) -> bool:
        return self in {JobType.DEPLOY, JobType.DRIFT_REMEDIATE}


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in {JobStatus.SUCCESS, JobStatus.FAILED}


class TargetStatus(StrEnum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ROLLED_BACK = "ROLLED_BACK"

    @property
    def is_failure(self) -> bool:
        return self in {TargetStatus.FAILED, TargetStatus.ROLLED_BACK}


class SnapshotKind(StrEnum):
    RUNNING = "RUNNING"
    INTENDED = "INTENDED"


class LogLevel(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class IntentSource(StrEnum):
    GIT_MAIN = "git_main"


class UserRole(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"
    OWNER = "owner"

    @property
    def rank(self) -> int:
        return _ROLE_RANK[self]

    def grants(self, required: UserRole) -> bool:
        return self.rank >= required.rank


_ROLE_RANK = {UserRole.VIEWER: 0, UserRole.OPERATOR: 1, UserRole.ADMIN: 2, UserRole.OWNER: 3}


class TelemetryProtocol(StrEnum):
    GNMI = "gnmi"
    PROMETHEUS_EXPORTER = "node_exporter"
    NODE_EXPORTER = "node_exporter"
    SNMP = "snmp"
    SSH_CLI = "ssh_cli"

