"""AI Telemetry Guard: Runtime error and anomaly detection via statistical quantile envelopes.

Evaluates post-change telemetry during the transactional commit-confirmed trial window.
Detects silent routing blackholes, traffic collapses, and control-plane storms before
they become production incidents, triggering automated rollbacks when anomalies exceed
predictive Q10/Q90 quantiles.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from typing import Any, Literal

from netops.models import Device
from netops.services.telemetry import POINTS_PER_DAY, metric_series
from netops.settings import Settings

logger = logging.getLogger(__name__)

# Active simulated anomalies for test injection & live demonstrations (mirrored to Redis via state_manager)
_active_injections: dict[str, Literal["blackhole", "storm"]] = {}
_REDIS_INJECTION_PREFIX = "netops:ai_guard:injection:"
_HISTORY_SHOWN = 144
_PROVIDER_STAT = "Статистический тренд"
_PROVIDER_NO_DATA = "Нет данных телеметрии"


@dataclass(frozen=True, slots=True)
class _Envelope:
    """Statistical Q10/Q90 corridor for the immediate commit-confirm trial window."""

    history: list[float]
    median: list[float]
    lower: list[float]
    upper: list[float]
    provider: str


def _build_envelope(series: list[float], horizon: int) -> _Envelope:
    """Linear trend over the last 8h plus residual quantile band (Q10-Q90)."""
    history = series[-_HISTORY_SHOWN:]
    if not series:
        return _Envelope([], [], [], [], _PROVIDER_NO_DATA)
    tail = series[-96:]
    if len(tail) == 1:
        tail = [tail[0]] * 3
    elif len(tail) == 2:
        tail = [tail[0], (tail[0] + tail[1]) / 2.0, tail[1]]
    xs = list(range(len(tail)))
    fit = statistics.linear_regression(xs, tail)
    resid = max(
        0.5,
        statistics.pstdev([y - (fit.intercept + fit.slope * x) for x, y in zip(xs, tail, strict=True)]),
    )
    median = [fit.intercept + fit.slope * (len(tail) + i) for i in range(horizon)]
    spread = [resid * (1 + i / horizon) * 1.28 for i in range(horizon)]
    return _Envelope(
        history,
        [round(m, 2) for m in median],
        [round(m - d, 2) for m, d in zip(median, spread, strict=True)],
        [round(m + d, 2) for m, d in zip(median, spread, strict=True)],
        _PROVIDER_STAT,
    )


@dataclass(frozen=True, slots=True)
class AiGuardVerdict:
    """Verdict returned by AI Telemetry Guard during pre-check or post-check."""

    healthy: bool
    metric: str
    observed_value: float
    expected_range: tuple[float, float]
    deviation_pct: float
    problem: str | None = None
    message: str = ""
    provider: str = "Telemetry Guard"


class AiTelemetryGuard:
    """Inspects post-change telemetry using predictive quantile envelopes."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @staticmethod
    def inject_anomaly(hostname: str, anomaly_type: Literal["blackhole", "storm", "clear"]) -> None:
        """Inject or clear a simulated execution anomaly for live demo / chaos testing."""
        from netops.services.state import get_state_manager

        state = get_state_manager()
        if anomaly_type == "clear":
            _active_injections.pop(hostname, None)
            state.delete(f"{_REDIS_INJECTION_PREFIX}{hostname}")
            logger.info("Cleared simulated anomaly on %s", hostname)
        else:
            _active_injections[hostname] = anomaly_type
            state.set_json(
                f"{_REDIS_INJECTION_PREFIX}{hostname}",
                {"anomaly": anomaly_type},
                ex=3600,
            )
            logger.warning("Injected %s anomaly on %s for AI Guard detection", anomaly_type, hostname)

    @staticmethod
    def clear_all_injections() -> None:
        """Clear all active simulated anomalies from memory and state_manager."""
        from netops.services.state import get_state_manager

        state = get_state_manager()
        for host in list(_active_injections):
            state.delete(f"{_REDIS_INJECTION_PREFIX}{host}")
        _active_injections.clear()

    @staticmethod
    def get_injected_anomaly(hostname: str) -> str | None:
        from netops.services.state import get_state_manager

        data = get_state_manager().get_json(f"{_REDIS_INJECTION_PREFIX}{hostname}")
        if isinstance(data, dict) and data.get("anomaly") in ("blackhole", "storm"):
            anom: Literal["blackhole", "storm"] = data["anomaly"]
            _active_injections[hostname] = anom
            return anom
        return _active_injections.get(hostname)

    def verify_execution(
        self,
        device: Device | Any,
        metric: str = "uplink_util_pct",
        horizon: int = 12,
    ) -> AiGuardVerdict:
        """Verify that observed telemetry during commit trial falls within expected bounds."""
        hostname = device.hostname
        role = str(getattr(device, "role", "leaf"))

        # 1. Obtain statistical telemetry envelope
        env = _build_envelope(metric_series(hostname, role, metric, POINTS_PER_DAY), horizon)

        # Immediate trial window expected bounds (first 3-6 steps, i.e. 15-30 min)
        is_offline = self._settings.network_driver == "offline"
        injected = self.get_injected_anomaly(hostname)
        if not env.history and not is_offline and not injected:
            return AiGuardVerdict(
                healthy=True,
                metric=metric,
                observed_value=0.0,
                expected_range=(0.0, 0.0),
                deviation_pct=0.0,
                message="Нет данных телеметрии: ожидание опроса устройства по SSH",
                provider=env.provider,
            )

        if env.lower:
            window_lower = min(env.lower[:3])
            window_upper = max(env.upper[:3])
            window_median = env.median[0]
        elif env.history:
            window_lower = max(0.0, min(env.history) * 0.5)
            window_upper = max(50.0, max(env.history) * 1.5)
            window_median = env.history[-1]
        else:
            window_lower = 10.0
            window_upper = 50.0
            window_median = 30.0

        # 2. Determine observed telemetry value during the trial window
        if injected == "blackhole":
            observed = 0.0 if window_lower <= 0.0 else max(1.5, round(window_lower * 0.1, 2))
            window_lower = max(window_lower, 10.0)
        elif injected == "storm":
            observed = max(92.0, min(99.5, round(max(window_upper, 30.0) * 1.8, 2)))
        else:
            observed = round(env.history[-1] if env.history else window_median, 2)

        # 3. Check for Anomalies against Quantiles
        # Mode A: Critical Traffic Drop (Silent Blackhole)
        if window_lower >= 10.0 and observed < (window_lower * 0.4):
            dev_pct = round(((observed - window_median) / max(window_median, 1.0)) * 100, 1)
            problem = (
                f"Критическое падение трафика: факт {observed:g}% "
                f"(ожидалось [{window_lower:g}% - {window_upper:g}%], Q10={window_lower:g}%). "
                f"Отклонение {dev_pct}%. Возможно возникновение 'тихой аварии' (Blackhole / ACL misconfiguration)."
            )
            return AiGuardVerdict(
                healthy=False,
                metric=metric,
                observed_value=observed,
                expected_range=(window_lower, window_upper),
                deviation_pct=dev_pct,
                problem=problem,
                provider=env.provider,
            )

        # Mode B: Micro-loop or Control Plane Surge
        if observed > (window_upper * 1.5) or observed >= 90.0:
            dev_pct = round(((observed - window_median) / max(window_median, 1.0)) * 100, 1)
            problem = (
                f"Аномальный всплеск нагрузки: факт {observed:g}% "
                f"(ожидалось [{window_lower:g}% - {window_upper:g}%], Q90={window_upper:g}%). "
                f"Отклонение +{dev_pct}%. Зафиксирован риск петли маршрутизации или шторма обновлений."
            )
            return AiGuardVerdict(
                healthy=False,
                metric=metric,
                observed_value=observed,
                expected_range=(window_lower, window_upper),
                deviation_pct=dev_pct,
                problem=problem,
                provider=env.provider,
            )

        # Nominal execution: Observed metric falls within predictive corridor
        dev_pct = round(((observed - window_median) / max(window_median, 1.0)) * 100, 1)
        msg = (
            f"Телеметрия в норме: факт {observed:g}% укладывается в доверительный интервал "
            f"[{window_lower:g}% .. {window_upper:g}%]"
        )
        return AiGuardVerdict(
            healthy=True,
            metric=metric,
            observed_value=observed,
            expected_range=(window_lower, window_upper),
            deviation_pct=dev_pct,
            message=msg,
            provider=env.provider,
        )

    def check_preflight(self, device: Device | Any) -> tuple[bool, str]:
        """Pre-flight check: ensures maintenance window doesn't collide with active anomalies."""
        verdict = self.verify_execution(device)
        if not verdict.healthy and verdict.problem:
            return (
                False,
                f"AI Telemetry Guard: {verdict.problem} Проведение деплоя заблокировано.",
            )
        return True, "Pre-flight проверка успешна: аномалий телеметрии не обнаружено."
