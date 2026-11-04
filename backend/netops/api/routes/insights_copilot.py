"""AI Copilot chat, AI Guard runtime anomaly endpoints, and fallback rule-engine."""

from __future__ import annotations

import asyncio
import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from netops.api.deps import ContainerDep, DeviceServiceDep, Operator, Viewer
from netops.enums import UserRole
from netops.services.llm import chat_with_llm

router = APIRouter(tags=["insights"])

CopilotActionType = Literal["dry_run", "remediate", "open_device", "scan_drift", "open_diff"]


class CopilotAction(BaseModel):
    type: CopilotActionType = Field(..., description="Action type for 1-click action or navigation")
    label: str = Field(..., description="Action button display label")
    device_id: int | None = Field(default=None, description="Target device ID if applicable")
    device_ids: list[int] = Field(default_factory=list, description="Target device IDs if multiple")
    payload: dict[str, Any] = Field(default_factory=dict, description="Parameters for execution")


class CopilotRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    device_id: int | None = None
    screen: str | None = Field(default=None)


class CopilotReply(BaseModel):
    answer: str
    provider: str
    action: CopilotAction | None = None


COPILOT_SYSTEM = (
    "Ты — AI Copilot платформы автоматизации сетей CLOS (Spine-Leaf). "
    "Отвечай по-русски, кратко и по делу, опираясь только на переданный контекст сети. "
    "Не предлагай применять изменения без dry-run и проверки diff.\n\n"
    "Если ситуация требует действия оператора, в конце ответа на отдельной строке укажи:\n"
    "ACTION: <type>|<label>|<device_id>\n"
    "Допустимые типы: remediate, dry_run, open_device, scan_drift, open_diff."
)


def extract_copilot_action(
    text: str,
    default_device_id: int | None = None,
    devices: list[Any] | None = None,
) -> tuple[str, CopilotAction | None]:
    """Parse trailing ACTION: type|label|device_id_or_hostname tag from LLM output."""
    match = re.search(
        r"(?:^|\n)ACTION:\s*(dry_run|remediate|open_device|scan_drift|open_diff)\s*\|\s*([^|\n]+?)(?:\s*\|\s*([^|\n]+?))?\s*$",
        text.strip(),
        re.MULTILINE,
    )
    if not match:
        return text.strip(), None

    act_type, label, raw_target = (
        match.group(1).strip(),
        match.group(2).strip(),
        (match.group(3) or "").strip(),
    )
    dev_id = default_device_id
    if raw_target:
        if raw_target.isdigit():
            dev_id = int(raw_target)
        elif devices:
            raw_low = raw_target.lower()
            matched_dev = next(
                (
                    d
                    for d in devices
                    if str(getattr(d, "hostname", "")).lower() in (raw_low, f"{raw_low}.croc.lab")
                    or str(getattr(d, "hostname", "")).lower().split(".")[0] == raw_low
                ),
                None,
            )
            if matched_dev is not None:
                dev_id = getattr(matched_dev, "id", default_device_id)
    clean_text = text[: match.start()].rstrip()
    try:
        return clean_text, CopilotAction(type=act_type, label=label, device_id=dev_id)  # type: ignore[arg-type]
    except Exception:
        return clean_text, None


def _sanitize_action_for_role(
    action: CopilotAction | None, role: UserRole
) -> CopilotAction | None:
    """Ensure read-only (Viewer) users only receive non-mutating navigation actions."""
    if action is None or role.grants(UserRole.OPERATOR):
        return action
    if action.type in ("remediate", "dry_run", "scan_drift"):
        target_id = action.device_id or (action.device_ids[0] if action.device_ids else None)
        if target_id is not None:
            return CopilotAction(
                type="open_diff", label="Посмотреть Diff (только чтение)", device_id=target_id
            )
        return None
    return action


def _device_fallback_action(
    selected: Any,
    ai_anomalies: dict[str, str] | None = None,
) -> tuple[str, CopilotAction | None]:
    if str(selected.status) == "DRIFT_DETECTED":
        return (
            f"Устройство {selected.hostname}: обнаружен дрейф конфигурации относительно эталона Git SoT. Рекомендуется применить исправление HierConfig.",
            CopilotAction(type="remediate", label=f"Устранить дрейф на {selected.hostname}", device_id=selected.id),
        )
    if ai_anomalies and selected.hostname in ai_anomalies:
        return (
            f"КРИТИЧЕСКАЯ АНОМАЛИЯ ТЕЛЕМЕТРИИ (AI Guard) на {selected.hostname}: {ai_anomalies[selected.hostname]} Проверьте состояние интерфейсов и BGP-сессий.",
            CopilotAction(type="open_device", label=f"Проверить телеметрию {selected.hostname}", device_id=selected.id),
        )
    return (
        f"Устройство {selected.hostname} ({selected.role}, {selected.platform}): статус {selected.status}. Конфигурация синхронизирована с эталоном.",
        CopilotAction(type="open_diff", label=f"Посмотреть Diff {selected.hostname}", device_id=selected.id),
    )


def _screen_fallback_action(screen: str | None) -> tuple[str, CopilotAction | None] | None:
    if screen == "drift":
        return (
            "Все устройства в синхронизации с Git SoT. Доступно внеочередное сканирование фабрики.",
            CopilotAction(type="scan_drift", label="Запустить сканирование дрейфа"),
        )
    if screen == "diff":
        return (
            "Экран верификации изменений (Diff/Dry-Run). Расхождений нет, доступен префлайт-прогон.",
            CopilotAction(type="dry_run", label="Запустить префлайт Dry-Run"),
        )
    return None


def _nominal_fallback_action(total_devices: int) -> tuple[str, CopilotAction | None]:
    return (
        f"Сеть в штатном режиме. Всего устройств: {total_devices}, дрейф отсутствует, аномалий телеметрии нет.",
        CopilotAction(type="dry_run", label="Запустить префлайт Dry-Run"),
    )


def generate_fallback_action(
    data: CopilotRequest,
    devices: list[Any],
    ai_anomalies: dict[str, str] | None = None,
    sys_ctx: dict[str, Any] | None = None,
) -> tuple[str, CopilotAction | None]:
    """Deterministic rule-based reply and actionable button generator when LLM is offline."""
    from netops.api.routes.insights import hybrid_rank_devices

    ctx = sys_ctx or {}
    anomalies = ai_anomalies if isinstance(ai_anomalies, dict) else {}
    drifting = [d for d in devices if str(d.status) == "DRIFT_DETECTED"]
    selected = next((d for d in devices if d.id == data.device_id), None) if data.device_id is not None else None
    msg = data.message.lower().strip()
    if any(msg.startswith(g) for g in ["ghbdtn", "привет", "здравствуй", "hello", "hi", "добрый"]):
        return (
            "Привет! Я AI Copilot платформы NetOps. Я в режиме только для чтения (read-only) "
            "инспектирую топологию L2/L3/BGP, логи пайплайнов CI/CD, Git SoT, дрейф конфигураций, "
            "VictoriaMetrics/Prometheus и живую телеметрию устройств. Задайте вопрос или выберите действие.",
            CopilotAction(type="dry_run", label="Запустить префлайт Dry-Run"),
        )
    if any(w in msg for w in ["замороз", "freeze", "блокиров", "lock"]):
        if ctx.get("is_frozen"):
            return (
                f"Фабрика сейчас заморожена (Kill Switch активен: {ctx.get('freeze_reason')}). "
                "Любые изменения заблокированы до снятия заморозки в Emergency Hub.",
                CopilotAction(type="open_diff", label="Проверить состояние Diff"),
            )
        if ctx.get("locked_hosts"):
            hosts_s = ", ".join(ctx["locked_hosts"])
            return (
                f"На узлах {hosts_s} действует защитная блокировка sync_lock после отката/аварийного патча.",
                CopilotAction(type="open_diff", label="Посмотреть Diff"),
            )
        return (
            "Заморозка фабрики (Kill Switch) выключена, активных блокировок sync_lock нет.",
            CopilotAction(type="dry_run", label="Запустить префлайт Dry-Run"),
        )
    if any(w in msg for w in ["тополог", "линк", "связ", "spof", "drain", "коммутац"]):
        topo = ctx.get("topo_summary") or {}
        down = topo.get("down_links") or []
        drained = topo.get("drained_nodes") or []
        if down or drained:
            parts = [f"Всего каналов в топологии: {topo.get('total_links', 0)}."]
            if down:
                parts.append(f"Отключенные/аварийные линки ({len(down)}): {', '.join(down[:3])}.")
            if drained:
                parts.append(f"Узлы в режиме Graceful Drain: {', '.join(drained)}.")
            return (" ".join(parts), CopilotAction(type="scan_drift", label="Проверить дрейф фабрики"))
        return (
            f"Топология CLOS здорова: узлов {len(devices)}, активных каналов {topo.get('total_links', 0)}, "
            "отключенных линков и изолированных сегментов нет.",
            CopilotAction(type="dry_run", label="Запустить префлайт Dry-Run"),
        )
    if any(w in msg for w in ["ошибк", "почему упал", "джоб", "job", "задач", "откат", "rollback"]):
        errs = ctx.get("recent_errors") or []
        jobs_l = ctx.get("job_lines") or []
        if errs:
            return (
                f"Причина сбоя в последних задачах: {'; '.join(errs[:2])}",
                CopilotAction(type="open_diff", label="Открыть Diff и логи задачи"),
            )
        if jobs_l:
            return (
                "Последние задачи пайплайна завершились без критических ошибок:\n" + "\n".join(jobs_l[:3]),
                CopilotAction(type="open_diff", label="Открыть DiffViewer"),
            )
    if any(w in msg for w in ["готовност", "деплой", "deploy", "проверк"]):
        if ctx.get("is_frozen"):
            return (
                f"Деплой сейчас невозможен: активна заморозка фабрики ({ctx.get('freeze_reason')}).",
                CopilotAction(type="open_diff", label="Открыть Diff"),
            )
        if anomalies:
            first_anom_host, first_anom_prob = next(iter(anomalies.items()))
            anom_dev = next((d for d in devices if d.hostname == first_anom_host), None)
            return (
                f"Внимание: деплой заблокирован AI Telemetry Guard — на {first_anom_host} зафиксирована аномалия ({first_anom_prob}).",
                CopilotAction(type="open_device", label=f"Проверить {first_anom_host}", device_id=anom_dev.id if anom_dev else None),
            )
        return (
            "Проверка готовности к деплою: фабрика находится в стабильном состоянии. Рекомендуется выполнить предварительную верификацию через Dry-Run перед наездом конфигурации.",
            CopilotAction(type="dry_run", label="Запустить префлайт Dry-Run"),
        )
    ranked = hybrid_rank_devices(msg, devices, ai_anomalies=anomalies)
    top_match = ranked[0].device if ranked and (ranked[0].lexical_score >= 3.5 or ranked[0].semantic_score >= 4.0) else None

    if any(w in msg for w in ["dry-run", "dry_run", "dry run", "префлайт", "холостой"]):
        target_dev = selected or top_match
        if target_dev:
            return (
                f"Подготовлен запуск префлайт-проверки Dry-Run для {target_dev.hostname}.",
                CopilotAction(type="dry_run", label=f"Запустить Dry-Run для {target_dev.hostname}", device_id=target_dev.id),
            )
        return (
            "Подготовлен запуск префлайт-проверки Dry-Run для сетевой фабрики.",
            CopilotAction(type="dry_run", label="Запустить префлайт Dry-Run"),
        )

    if selected is None and ranked and ranked[0].lexical_score >= 4.5:
        selected = ranked[0].device
    if selected is not None:
        return _device_fallback_action(selected, ai_anomalies=anomalies)

    if drifting:
        drift_names = ", ".join(d.hostname for d in drifting)
        if len(drifting) == 1:
            return (
                f"В фабрике обнаружен дрейф конфигурации на устройстве {drift_names}. Рекомендуется устранить расхождение.",
                CopilotAction(type="remediate", label=f"Устранить дрейф на {drifting[0].hostname}", device_id=drifting[0].id),
            )
        return (
            f"В фабрике обнаружен дрейф конфигурации на {len(drifting)} устройствах ({drift_names}).",
            CopilotAction(type="remediate", label=f"Устранить дрейф ({len(drifting)} устр.)", device_ids=[d.id for d in drifting]),
        )

    if anomalies:
        anom_host, anom_problem = next(iter(anomalies.items()))
        anom_dev = next((d for d in devices if d.hostname == anom_host), None)
        return (
            f"Внимание (AI Telemetry Guard): зафиксирована операционная аномалия на {anom_host} — {anom_problem}",
            CopilotAction(type="open_device", label=f"Открыть {anom_host}", device_id=anom_dev.id if anom_dev else None),
        )

    screen_action = _screen_fallback_action(data.screen)
    if screen_action is not None:
        return screen_action

    return _nominal_fallback_action(len(devices))


@router.post("/copilot/chat", response_model=CopilotReply, summary="Ask the AI Copilot")
async def copilot_chat(
    data: CopilotRequest, service: DeviceServiceDep, container: ContainerDep, user: Viewer
) -> CopilotReply:
    from netops.api.routes.devices import list_allowed_devices
    from netops.api.routes.insights import (
        _check_rate_limit,
        select_rag_devices,
    )
    from netops.services.ai_guard import AiTelemetryGuard
    from netops.services.copilot_context import build_copilot_system_context
    from netops.telemetry import targets

    _check_rate_limit(f"copilot:{user.username}", limit=30, window_seconds=60.0)
    devices = list_allowed_devices(service, user.allowed_system)
    guard = AiTelemetryGuard(container.settings)
    verdicts = await asyncio.to_thread(lambda: [guard.verify_execution(d) for d in devices])
    ai_anomalies: dict[str, str] = {
        d.hostname: (v.problem or v.message)
        for d, v in zip(devices, verdicts, strict=True)
        if not v.healthy
    }

    rag_devices = select_rag_devices(
        data.message,
        devices,
        selected_device_id=data.device_id,
        ai_anomalies=ai_anomalies,
        top_k=12,
    )
    plan_devices = (targets.last_collector_plan() or {}).get("devices") or {}
    sys_ctx = build_copilot_system_context(
        service._session, container.settings, devices, rag_devices, plan_devices
    )
    lines: list[str] = [sys_ctx["context_text"]]
    if len(rag_devices) < len(devices):
        lines.insert(
            0,
            f"Всего устройств в фабрике: {len(devices)} (показаны {len(rag_devices)} наиболее релевантных по гибридному RRF-поиску):",
        )
    lines += [f"КРИТИЧЕСКАЯ АНОМАЛИЯ ТЕЛЕМЕТРИИ (AI Guard): {h} — {p}" for h, p in ai_anomalies.items()]
    if data.screen:
        lines.insert(0, f"Текущий экран оператора: {data.screen}.")
    if data.device_id is not None:
        selected = next((d for d in devices if d.id == data.device_id), None)
        if selected is None:
            raise HTTPException(status_code=404, detail="Device not found")
        lines.insert(0, f"Пользователь смотрит устройство {selected.hostname} ({selected.management_ip}).")

    context = "\n".join(lines)
    llm_prompt = f"Контекст:\n{context}\n\nВопрос: {data.message}"
    reply = await chat_with_llm(container.settings, COPILOT_SYSTEM, llm_prompt)
    if reply is not None:
        answer_text, action = extract_copilot_action(
            reply[0], default_device_id=data.device_id, devices=devices
        )
        msg_lower = data.message.lower()
        if any(w in msg_lower for w in ["dry-run", "dry_run", "dry run", "префлайт", "холостой"]) or (
            action is None and any(w in msg_lower for w in ["дрейф", "drift", "remediat", "устрани", "исправ"])
        ):
            _, action = generate_fallback_action(
                data, devices, ai_anomalies=ai_anomalies, sys_ctx=sys_ctx
            )
        return CopilotReply(
            answer=answer_text,
            provider=reply[1],
            action=_sanitize_action_for_role(action, user.role),
        )

    summary, fallback_action = generate_fallback_action(
        data, devices, ai_anomalies=ai_anomalies, sys_ctx=sys_ctx
    )
    return CopilotReply(
        answer=f"{summary}\n\n(LLM недоступна — автоматическая рекомендация Rule-Engine.)",
        provider="Rule-Engine (offline)",
        action=_sanitize_action_for_role(fallback_action, user.role),
    )


class AiGuardSimulateRequest(BaseModel):
    hostname: str
    anomaly_type: Literal["blackhole", "storm", "clear"]


class AiGuardResponse(BaseModel):
    hostname: str
    healthy: bool
    metric: str
    observed_value: float
    expected_range: tuple[float, float]
    deviation_pct: float
    problem: str | None = None
    message: str = ""
    provider: str = ""
    injected_simulation: str | None = None


def _to_guard_response(device: Any, verdict: Any, guard: Any) -> AiGuardResponse:
    return AiGuardResponse(
        hostname=device.hostname,
        healthy=verdict.healthy,
        metric=verdict.metric,
        observed_value=verdict.observed_value,
        expected_range=verdict.expected_range,
        deviation_pct=verdict.deviation_pct,
        problem=verdict.problem,
        message=verdict.message,
        provider=verdict.provider,
        injected_simulation=guard.get_injected_anomaly(device.hostname),
    )


@router.get("/ai-guard/check/{device_id}", response_model=AiGuardResponse, summary="Runtime error & anomaly detection via statistical quantile guard")
@router.get("/insights/ai-guard/check/{device_id}", response_model=AiGuardResponse, include_in_schema=False)
async def ai_guard_check_device(
    device_id: int, service: DeviceServiceDep, container: ContainerDep, user: Viewer
) -> AiGuardResponse:
    from netops.api.routes.devices import assert_device_allowed
    from netops.services.ai_guard import AiTelemetryGuard

    device = service.get(device_id)
    assert_device_allowed(device, user.allowed_system)
    guard = AiTelemetryGuard(container.settings)
    verdict = await asyncio.to_thread(guard.verify_execution, device)
    return _to_guard_response(device, verdict, guard)


@router.post("/ai-guard/simulate", summary="Inject simulated operational anomaly (blackhole/storm) for demo (Operator/Admin)")
@router.post("/insights/ai-guard/simulate", include_in_schema=False)
async def ai_guard_simulate_anomaly(
    data: AiGuardSimulateRequest, service: DeviceServiceDep, user: Operator
) -> dict[str, str]:
    from netops.api.routes.devices import assert_device_allowed
    from netops.services.ai_guard import AiTelemetryGuard

    device = service.get_by_hostname(data.hostname)
    if device is not None:
        assert_device_allowed(device, user.allowed_system)
    AiTelemetryGuard.inject_anomaly(data.hostname, data.anomaly_type)
    return {
        "status": "ok",
        "hostname": data.hostname,
        "anomaly": data.anomaly_type,
        "message": f"Simulated {data.anomaly_type} set on {data.hostname}",
    }


@router.get("/ai-guard/overview", response_model=list[AiGuardResponse], summary="AI Guard health overview across all network devices")
@router.get("/insights/ai-guard/overview", response_model=list[AiGuardResponse], include_in_schema=False)
async def ai_guard_overview(
    service: DeviceServiceDep, container: ContainerDep, user: Viewer
) -> list[AiGuardResponse]:
    from netops.api.routes.devices import _iter_allowed_tokens, device_allowed
    from netops.services.ai_guard import AiTelemetryGuard

    guard = AiTelemetryGuard(container.settings)
    tokens = _iter_allowed_tokens(user.allowed_system)
    devs = [d for d in service.list_devices(limit=5000, allowed_tokens=tokens) if device_allowed(d, user.allowed_system)]
    verdicts = await asyncio.to_thread(lambda: [guard.verify_execution(d) for d in devs])
    return [_to_guard_response(d, v, guard) for d, v in zip(devs, verdicts, strict=True)]


@router.post("/insights/telegram/broadcast", summary="Broadcast AI network executive summary to Telegram chat (Operator/Admin)")
async def broadcast_to_telegram(
    service: DeviceServiceDep, container: ContainerDep, _: Operator
) -> dict[str, Any]:
    from netops.api.routes.insights import _check_rate_limit, get_fabric_summary
    from netops.services.notifier import send_telegram_message

    _check_rate_limit(f"tg_broadcast:{_.username}", limit=10, window_seconds=60.0)
    summary_data = await get_fabric_summary(service, container, _)
    tg_text = (
        "<b>[NetOps Platform] Оперативная сводка фабрики</b>\n\n"
        f"<b>Статус:</b> Всего {summary_data.total_devices} | "
        f"Sync: {summary_data.in_sync_count} | "
        f"Drift: {summary_data.drift_count} | "
        f"Unreachable: {summary_data.unreachable_count}\n\n"
        f"<b>AI Резюме:</b>\n{summary_data.ai_executive_summary}\n\n"
        f"<i>Провайдер: {summary_data.ai_provider}</i>"
    )
    sent = await asyncio.to_thread(send_telegram_message, container.settings, tg_text)
    return {
        "status": "ok" if sent else "skipped",
        "delivered": sent,
        "configured": summary_data.telegram_configured,
        "message": "Broadcast sent to Telegram" if sent else "Telegram unconfigured or send failed",
    }
