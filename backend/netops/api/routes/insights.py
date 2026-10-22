"""AI-assisted insights: hybrid RRF search, AI Guard, and fabric summary."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from netops.api.deps import ContainerDep, DeviceServiceDep, Viewer
from netops.api.routes.devices import list_allowed_devices
from netops.api.routes.insights_copilot import (
    COPILOT_SYSTEM,
    AiGuardResponse,
    AiGuardSimulateRequest,
    CopilotAction,
    CopilotActionType,
    CopilotReply,
    CopilotRequest,
    _sanitize_action_for_role,
    extract_copilot_action,
    generate_fallback_action,
)
from netops.api.routes.insights_copilot import (
    router as insights_copilot_router,
)
from netops.services.llm import chat_with_llm

__all__ = [
    "COPILOT_SYSTEM",
    "AiGuardResponse",
    "AiGuardSimulateRequest",
    "CopilotAction",
    "CopilotActionType",
    "CopilotReply",
    "CopilotRequest",
    "HybridSearchHit",
    "RankedDevice",
    "_sanitize_action_for_role",
    "extract_copilot_action",
    "generate_fallback_action",
    "hybrid_rank_devices",
    "router",
    "select_rag_devices",
]

_SYNONYMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("huawei_vrp", ("huawei", "vrp", "хуавей", "хуавее", "хуавея", "cloudengine")),
    ("cisco_iosxe", ("cisco", "ios", "iosxe", "nxos", "циско", "циске", "nexus")),
    ("arista_eos", ("arista", "eos", "ариста", "аристе", "ceos")),
    ("juniper_junos", ("juniper", "junos", "джунипер", "vqfx", "vmx")),
    ("eltex_mes", ("eltex", "mes", "esr", "элтекс")),
    ("yadro_kornfe", ("yadro", "kornfe", "ядро", "корнфе")),
    ("linux_server", ("linux", "server", "ubuntu", "линукс", "сервер")),
    ("role_spine", ("spine", "спайн", "спайне", "спайны", "ядро", "магистрал", "core")),
    ("role_leaf", ("leaf", "лиф", "лифе", "лифы", "tor", "тор", "доступ")),
    ("role_border", ("border", "бордер", "границ", "fw", "firewall", "межсетев")),
    ("state_drift", ("drift", "дрейф", "рассинхрон", "расхожд", "изменен")),
    ("state_down", ("unreachable", "down", "недоступ", "упал", "лежит", "ssh")),
    ("state_anomaly", ("anomaly", "alert", "degraded", "blackhole", "storm", "bgp", "аномал", "авария", "проблем", "блэкхол", "шторм", "бгп", "риск")),
)


@dataclass(frozen=True, slots=True)
class RankedDevice:
    device: Any
    rrf_score: float
    lexical_score: float
    semantic_score: float
    lexical_rank: int
    semantic_rank: int
    match_reason: str


def _concepts(text: str) -> set[str]:
    low = text.lower()
    return {tag for tag, roots in _SYNONYMS if any(r in low for r in roots)}


def hybrid_rank_devices(
    query: str,
    devices: list[Any],
    *,
    ai_anomalies: dict[str, str] | None = None,
    k: int = 60,
) -> list[RankedDevice]:
    """Rank devices using lexical token matching and concept synonyms."""
    if not devices:
        return []
    q = query.strip().lower()
    q_tokens = [t for t in re.split(r"[^a-zа-яё0-9._-]+", q) if len(t) >= 2]
    q_conc = _concepts(q)
    anomalies = ai_anomalies if isinstance(ai_anomalies, dict) else {}

    lex_scores: list[float] = []
    sem_scores: list[float] = []
    for d in devices:
        host = str(getattr(d, "hostname", "")).lower()
        short = host.split(".")[0]
        ip = str(getattr(d, "management_ip", "")).lower()
        plat = str(getattr(d, "platform", "")).lower()
        role = str(getattr(d, "role", "")).lower()
        status = str(getattr(d, "status", "")).lower()
        site = str(getattr(d, "site", "") or "").lower()
        anom = anomalies.get(getattr(d, "hostname", ""), "").lower()
        doc = f"{host} {ip} {plat} {role} {status} {site} {anom}"

        ls = 12.0 if (host and (host in q or q in (short, ip))) else (9.0 if short in q_tokens else 0.0)
        for tok in q_tokens:
            ls += 8.0 if tok in (host, short, ip) else (4.5 if (tok in host or tok in ip) else (3.5 if tok in (plat, role, status, site) else (1.5 if tok in doc else 0.0)))
        lex_scores.append(ls)

        d_conc = _concepts(doc)
        if "drift" in status:
            d_conc |= {"state_drift", "state_anomaly"}
        if "unreachable" in status:
            d_conc |= {"state_down", "state_anomaly"}
        if getattr(d, "hostname", "") in anomalies:
            d_conc.add("state_anomaly")
        ss = len(q_conc & d_conc) * 4.0 + (3.5 if (q_conc & {"state_anomaly", "state_drift", "state_down"} and getattr(d, "hostname", "") in anomalies) else 0.0)
        sem_scores.append(ss)

    lex_rank = {idx: r for r, idx in enumerate(sorted(range(len(devices)), key=lambda i: (-lex_scores[i], i)))}
    sem_rank = {idx: r for r, idx in enumerate(sorted(range(len(devices)), key=lambda i: (-sem_scores[i], i)))}
    ranked = [
        RankedDevice(
            device=d,
            rrf_score=round((1.0 / (k + lex_rank[i] + 1) if lex_scores[i] > 0 else 0.0) + (1.0 / (k + sem_rank[i] + 1) if sem_scores[i] > 0 else 0.0), 6),
            lexical_score=round(lex_scores[i], 3),
            semantic_score=round(sem_scores[i], 3),
            lexical_rank=lex_rank[i] + 1,
            semantic_rank=sem_rank[i] + 1,
            match_reason="hybrid" if (lex_scores[i] > 0 and sem_scores[i] > 0) else ("lexical" if lex_scores[i] > 0 else ("semantic" if sem_scores[i] > 0 else "baseline")),
        )
        for i, d in enumerate(devices)
    ]
    ranked.sort(key=lambda r: (-r.rrf_score, -r.lexical_score, -r.semantic_score, str(getattr(r.device, "hostname", ""))))
    return ranked


def select_rag_devices(
    query: str,
    devices: list[Any],
    *,
    selected_device_id: int | None = None,
    ai_anomalies: dict[str, str] | None = None,
    top_k: int = 12,
) -> list[Any]:
    """Select Top-K relevant devices + unhealthy/selected nodes for LLM context."""
    if len(devices) <= top_k:
        return list(devices)
    ranked = hybrid_rank_devices(query, devices, ai_anomalies=ai_anomalies)
    anom_hosts = set((ai_anomalies if isinstance(ai_anomalies, dict) else {}).keys())
    chosen: list[Any] = []
    seen: set[Any] = set()

    def _add(dev: Any) -> None:
        key = getattr(dev, "id", None) or getattr(dev, "hostname", id(dev))
        if key not in seen:
            seen.add(key)
            chosen.append(dev)

    for d in devices:
        st = str(getattr(d, "status", "")).upper()
        if getattr(d, "id", None) == selected_device_id or "DRIFT" in st or "UNREACHABLE" in st or getattr(d, "hostname", "") in anom_hosts:
            _add(d)
    for r in ranked:
        if len(chosen) >= top_k and r.lexical_score == 0 and r.semantic_score < 2.0:
            break
        _add(r.device)
        if len(chosen) >= max(top_k, len(seen)):
            break
    return chosen


router = APIRouter(tags=["insights"])



_RATE_LIMIT_BUCKETS: dict[str, list[float]] = {}


def _check_rate_limit(key: str, limit: int = 30, window_seconds: float = 60.0) -> None:
    """Sliding-window rate limiter for expensive AI/broadcast endpoints."""
    import time

    now = time.monotonic()
    if len(_RATE_LIMIT_BUCKETS) > 1024:
        stale_keys = [
            k
            for k, ts_list in _RATE_LIMIT_BUCKETS.items()
            if not ts_list or (now - ts_list[-1]) >= window_seconds
        ]
        for k in stale_keys:
            _RATE_LIMIT_BUCKETS.pop(k, None)
        if len(_RATE_LIMIT_BUCKETS) > 1024:
            _RATE_LIMIT_BUCKETS.clear()
    bucket = [ts for ts in _RATE_LIMIT_BUCKETS.get(key, []) if now - ts < window_seconds]
    if len(bucket) >= limit:
        _RATE_LIMIT_BUCKETS[key] = bucket
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({limit} requests per {int(window_seconds)}s). Please wait.",
        )
    bucket.append(now)
    _RATE_LIMIT_BUCKETS[key] = bucket


class FabricSummaryResponse(BaseModel):
    total_devices: int
    in_sync_count: int
    drift_count: int
    unreachable_count: int
    active_alarms: list[str] = Field(default_factory=list)
    ai_executive_summary: str
    ai_provider: str
    telegram_configured: bool


@router.get(
    "/insights/fabric-summary",
    response_model=FabricSummaryResponse,
    summary="Get aggregated network fabric health and AI executive summary",
)
async def get_fabric_summary(
    service: DeviceServiceDep,
    container: ContainerDep,
    user: Viewer,
) -> FabricSummaryResponse:
    from netops.enums import DeviceStatus
    from netops.services.ai_guard import AiTelemetryGuard

    devices = list_allowed_devices(service, user.allowed_system)
    total = len(devices)
    in_sync = sum(1 for d in devices if d.status == DeviceStatus.IN_SYNC)
    drift = sum(1 for d in devices if d.status == DeviceStatus.DRIFT_DETECTED)
    unreachable = sum(1 for d in devices if d.status == DeviceStatus.UNREACHABLE)

    guard = AiTelemetryGuard(container.settings)
    verdicts = await asyncio.to_thread(
        lambda: [guard.verify_execution(d) for d in devices]
    )

    alarms = []
    if drift > 0:
        drift_devs = [d.hostname for d in devices if d.status == DeviceStatus.DRIFT_DETECTED]
        alarms.append(f"Дрейф конфигурации на узлах: {', '.join(drift_devs)}")
    if unreachable > 0:
        unreach_devs = [d.hostname for d in devices if d.status == DeviceStatus.UNREACHABLE]
        alarms.append(f"Недоступны по SSH: {', '.join(unreach_devs)}")
    for d, v in zip(devices, verdicts, strict=True):
        if not v.healthy and (v.problem or v.message):
            alarms.append(f"AI Guard ({d.hostname}): {v.problem or v.message}")

    prompt = (
        f"Текущее состояние фабрики ЦОД:\n"
        f"- Всего устройств: {total}\n"
        f"- В синхронизации (IN_SYNC): {in_sync}\n"
        f"- Обнаружен дрейф (DRIFT): {drift}\n"
        f"- Недоступны (UNREACHABLE): {unreachable}\n"
        f"Алерты: {'; '.join(alarms) if alarms else 'Нет'}\n\n"
        f"Составь краткую сводку для дежурного сетевого инженера на русском языке (2-3 предложения), "
        f"оцени операционный статус и дай рекомендацию."
    )
    res = await chat_with_llm(
        container.settings,
        system_prompt="Ты — главный сетевой архитектор платформы NetOps. Отвечай кратко, чётко и профессионально.",
        user_prompt=prompt,
        timeout_floor=3.0,
    )

    if res:
        ai_summary, provider = res
    else:
        if drift == 0 and unreachable == 0 and not alarms:
            ai_summary = "Фабрика полностью синхронизирована с Git-эталоном. Аномалий и расхождений не зафиксировано."
        elif drift == 0 and unreachable == 0 and alarms:
            ai_summary = f"Конфигурации синхронизированы, но зафиксирована аномалия телеметрии: {'; '.join(alarms)}."
        elif drift > 0:
            ai_summary = f"Внимание: обнаружено {drift} устройств с несанкционированными правками. Рекомендуется запустить ремедиацию."
        else:
            ai_summary = f"Внимание: {unreachable} узлов не отвечают по SSH. Проверьте сетевую связность или перезапустите лабу."
        provider = "Deterministic Rule-Engine"

    is_tg = bool(container.settings.telegram_bot_token and container.settings.telegram_chat_id)
    return FabricSummaryResponse(
        total_devices=total,
        in_sync_count=in_sync,
        drift_count=drift,
        unreachable_count=unreachable,
        active_alarms=alarms,
        ai_executive_summary=ai_summary,
        ai_provider=provider,
        telegram_configured=is_tg,
    )


class HybridSearchHit(BaseModel):
    device_id: int
    hostname: str
    management_ip: str
    platform: str
    role: str
    status: str
    rrf_score: float
    lexical_score: float
    semantic_score: float
    lexical_rank: int
    semantic_rank: int
    match_reason: str


@router.get(
    "/insights/search",
    response_model=list[HybridSearchHit],
    summary="Hybrid Search (Lexical + Semantic/Synonym RRF) across network devices",
)
@router.get("/search", response_model=list[HybridSearchHit], include_in_schema=False)
async def hybrid_search_endpoint(
    service: DeviceServiceDep,
    user: Viewer,
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=10, ge=1, le=50),
) -> list[HybridSearchHit]:
    devices = list_allowed_devices(service, user.allowed_system)
    ranked = hybrid_rank_devices(q, devices)
    if q.strip():
        ranked = [r for r in ranked if r.rrf_score > 0]
    return [
        HybridSearchHit(
            device_id=r.device.id,
            hostname=r.device.hostname,
            management_ip=str(r.device.management_ip),
            platform=str(r.device.platform),
            role=str(r.device.role),
            status=str(r.device.status),
            rrf_score=r.rrf_score,
            lexical_score=r.lexical_score,
            semantic_score=r.semantic_score,
            lexical_rank=r.lexical_rank,
            semantic_rank=r.semantic_rank,
            match_reason=r.match_reason,
        )
        for r in ranked[:limit]
    ]


router.include_router(insights_copilot_router)
