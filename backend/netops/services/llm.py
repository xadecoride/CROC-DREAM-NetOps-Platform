"""LLM Risk Analyzer service for network configuration diffs and remediation patches.

Supports OpenAI-compatible APIs (Xiaomi MiMo-V2.6-Flash, DeepSeek, etc.)
with deterministic rule-based fallback when the API is unconfigured or unreachable.
Zero external dependencies (uses standard library urllib.request + asyncio).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any, Literal

from pydantic import BaseModel, Field

from netops.settings import Settings

logger = logging.getLogger(__name__)

RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class RiskExplanation(BaseModel):
    """Structured risk assessment for a configuration change."""

    risk_level: RiskLevel
    summary: str
    key_points: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    is_safe: bool = False
    provider: str = "MiMo-V2.6-Flash"


SYSTEM_PROMPT = (
    "Ты — ведущий сетевой архитектор и эксперт по автоматизации фабрик ЦОД (CLOS / Spine-Leaf).\n"
    "Твоя задача — провести аудит предлагаемого конфигурационного патча (HierConfig remediation) "
    "и выявить потенциальные операционные риски.\n\n"
    "Уровни риска:\n"
    "- LOW: Косметические изменения (описания портов, баннеры, hostname, syslog).\n"
    "- MEDIUM: Изменения MTU, добавление неиспользуемых VLAN/интерфейсов, новые префикс-листы.\n"
    "- HIGH: Перезапуск BGP пиров, изменение ASN/Router-ID, добавление/изменение правил ACL.\n"
    "- CRITICAL: Shutdown магистральных линков, сброс аутентификации BGP.\n\n"
    "Отвечай СТРОГО в формате валидного JSON:\n"
    "{\n"
    '  "risk_level": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",\n'
    '  "summary": "Краткое профессиональное резюме на русском языке",\n'
    '  "key_points": ["Деталь 1", "Деталь 2"],\n'
    '  "recommendations": ["Рекомендация по проверке до/после применения"],\n'
    '  "is_safe": true | false\n'
    "}\n"
    "Никакого лишнего текста вне JSON."
)


def _check_bgp_risk(text: str) -> tuple[RiskLevel | None, list[str], list[str]]:
    if not any(k in text for k in ("router bgp", "neighbor", "bgp ", "peer ")):
        return None, [], []
    if any(k in text for k in ("remote-as", "as-number", "password", "shutdown", "undo peer")):
        return (
            "HIGH",
            ["Модификация параметров BGP пиринга (remote-as / as-number / password / shutdown)."],
            ["Проверьте состояние BGP сессий (show ip bgp summary / display bgp peer) сразу после наката."],
        )
    return (
        "MEDIUM",
        ["Изменение настроек анонсируемых сетей или параметров BGP."],
        ["Убедитесь, что префиксы принимаются соседними маршрутизаторами."],
    )


def _check_acl_risk(text: str) -> tuple[RiskLevel | None, list[str], list[str]]:
    if not any(
        k in text for k in ("access-list", "ip access-group", "acl name", "traffic-filter", "rule ")
    ):
        return None, [], []
    if any(k in text for k in ("deny", "permit ip any any", "undo rule", "no rule", "10.99.0.0")):
        return (
            "HIGH",
            ["Изменение списков контроля доступа (ACL) с правилами фильтрации трафика."],
            ["Проверьте доступность управляющего IP-адреса и BGP-сессий перед подтверждением."],
        )
    return "MEDIUM", ["Добавление или корректировка правил ACL."], []


def _check_interface_risk(text: str) -> tuple[RiskLevel | None, list[str], list[str]]:
    has_standalone_shutdown = any(
        re.search(r"(?<!no\s)(?<!undo\s)\bshutdown\b", ln) for ln in text.splitlines()
    )
    if has_standalone_shutdown:
        return (
            "CRITICAL",
            ["Команда shutdown переводит сетевой интерфейс в состояние down."],
            ["Убедитесь, что отключаемый порт не является единственным аплинком."],
        )
    if "mtu" in text:
        return (
            "MEDIUM",
            ["Изменение размера MTU может вызвать расхождение с соседним оборудованием."],
            ["Согласуйте MTU на обоих концах физического линка."],
        )
    return None, [], []


def analyze_diff_heuristic(
    hostname: str,
    platform: str,
    remediation_patch: str,
    rollback_patch: str,
) -> RiskExplanation:
    """Deterministic fallback analyzer when LLM is offline or unconfigured."""
    if not remediation_patch.strip():
        return RiskExplanation(
            risk_level="LOW",
            summary=f"Конфигурация устройства {hostname} полностью синхронизирована с эталоном.",
            key_points=["Изменения не требуются."],
            recommendations=["Никаких действий предпринимать не требуется."],
            is_safe=True,
            provider="Deterministic Rule-Engine (No Diff)",
        )

    text = remediation_patch.lower()
    points: list[str] = []
    recs: list[str] = []
    level: RiskLevel = "LOW"

    for checker in (_check_bgp_risk, _check_acl_risk, _check_interface_risk):
        c_level, c_points, c_recs = checker(text)
        if c_level is not None:
            points.extend(c_points)
            recs.extend(c_recs)
            if c_level == "CRITICAL" or (c_level == "HIGH" and level != "CRITICAL"):
                level = c_level
            elif c_level == "MEDIUM" and level == "LOW":
                level = "MEDIUM"

    if not points:
        points.append("Косметические изменения параметров интерфейсов или системных настроек.")
        recs.append("Стандартная процедура наката через commit confirmed.")

    summary = (
        f"Анализ устройства {hostname} ({platform}): уровень риска {level}. "
        f"Обнаружено {len(points)} ключевых факторов влияния на сеть."
    )

    return RiskExplanation(
        risk_level=level,
        summary=summary,
        key_points=points,
        recommendations=recs,
        is_safe=(level == "LOW"),
        provider="Deterministic Rule-Engine (Offline Fallback)",
    )


def _sync_http_request(
    url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float = 2.0
) -> str:
    """Execute HTTP POST using standard library with proxy support."""
    data_bytes = json.dumps(payload).encode("utf-8")
    proxy = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    opener = (
        urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        if proxy
        else urllib.request.build_opener()
    )
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
    with opener.open(req, timeout=timeout) as response:
        return response.read().decode("utf-8")


def normalize_llm_cascade_item(item: dict[str, Any], idx: int = 0) -> dict[str, Any]:
    """Auto-resolve base_url, model, api_key, and name for any custom LLM cascade item."""
    raw_name = str(item.get("name") or "").strip()
    raw_url = str(item.get("base_url") or "").strip()
    raw_model = str(item.get("model") or "").strip()
    raw_key = str(item.get("api_key") or item.get("token") or "").strip()
    hint_nu = f"{raw_name} {raw_url}".lower()
    hint_num = f"{hint_nu} {raw_model}".lower()

    if raw_key.startswith("gsk_") or "groq" in hint_nu:
        def_title, def_url, def_model, def_key = (
            "Groq",
            "https://api.groq.com/openai/v1",
            "qwen/qwen3.8-27b",
            raw_key,
        )
    elif raw_key.startswith("AIza") or "gemini" in hint_nu or "google" in hint_nu:
        def_title, def_url, def_model, def_key = (
            "Google-Gemini",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "gemini-2.5-flash",
            raw_key,
        )
    elif raw_key.startswith("sk-or-") or "openrouter" in hint_nu:
        def_title, def_url, def_model, def_key = (
            "OpenRouter",
            "https://openrouter.ai/api/v1",
            "deepseek/deepseek-chat-v3-0324:free",
            raw_key,
        )
    elif "deepseek" in hint_num:
        def_title, def_url, def_model, def_key = (
            "DeepSeek",
            "https://api.deepseek.com/v1",
            "deepseek-chat",
            raw_key,
        )
    elif "ollama" in hint_nu or "11434" in hint_nu or "local" in hint_nu:
        def_title, def_url, def_model, def_key = (
            "Local-Ollama",
            "http://127.0.0.1:11434/v1",
            "qwen2.5:7b",
            raw_key or "ollama",
        )
    elif "openai" in hint_num or "gpt" in hint_num:
        def_title, def_url, def_model, def_key = (
            "OpenAI",
            "https://api.openai.com/v1",
            "gpt-4o-mini",
            raw_key,
        )
    else:
        def_title, def_url, def_model, def_key = (
            f"Provider-{idx + 1}",
            "",
            "",
            raw_key,
        )

    try:
        timeout = float(item.get("timeout_seconds") or 2.5)
    except (TypeError, ValueError):
        timeout = 2.5

    return {
        "name": raw_name or def_title,
        "base_url": raw_url or def_url,
        "model": raw_model or def_model,
        "api_key": def_key,
        "timeout_seconds": timeout,
    }


def _llm_candidates(settings: Settings) -> list[tuple[str, str, str, str, float]]:
    """Configured providers in cascade order: (name, base_url, token, model, timeout)."""
    if "PYTEST_CURRENT_TEST" not in os.environ or "offline_lab_path" in settings.model_fields_set:
        settings.apply_runtime_overlay()
    candidates: list[tuple[str, str, str, str, float]] = []
    seen_urls: set[str] = set()
    for idx, entry in enumerate(getattr(settings, "llm_cascade", None) or []):
        if not isinstance(entry, dict):
            continue
        norm = normalize_llm_cascade_item(entry, idx)
        base_url = norm["base_url"]
        model_name = norm["model"]
        api_key = norm["api_key"] or "ollama"
        name = norm["name"]
        timeout = float(norm["timeout_seconds"])
        if not base_url or not model_name:
            continue
        candidates.append((name, base_url, api_key, model_name, max(timeout, 1.0)))
        seen_urls.add(f"{base_url}:{model_name}")

    if settings.ollama_url and f"{settings.ollama_url}:{settings.ollama_model}" not in seen_urls:
        candidates.append((
            "Local-Ollama",
            settings.ollama_url,
            "ollama",
            settings.ollama_model,
            settings.ollama_timeout_seconds,
        ))
    if settings.groq_api_key and f"{settings.groq_base_url}:{settings.groq_model}" not in seen_urls:
        candidates.append((
            "Groq",
            settings.groq_base_url,
            settings.groq_api_key.get_secret_value(),
            settings.groq_model,
            max(settings.groq_timeout_seconds, 3.0),
        ))
    if settings.gemini_api_key and f"{settings.gemini_base_url}:{settings.gemini_model}" not in seen_urls:
        candidates.append((
            "Google-Gemini",
            settings.gemini_base_url,
            settings.gemini_api_key.get_secret_value(),
            settings.gemini_model,
            max(settings.gemini_timeout_seconds, 10.0),
        ))
    if settings.llm_api_key and f"{settings.llm_base_url}:{settings.llm_model}" not in seen_urls:
        candidates.append((
            "Team-MiMo",
            settings.llm_base_url,
            settings.llm_api_key.get_secret_value(),
            settings.llm_model,
            settings.llm_timeout_seconds,
        ))
    return candidates


_RISK_ORDER: dict[str, int] = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(password(?:\s+cipher|\s+simple|\s+\d+)?|"
            r"(?:enable|username\s+\S+(?:\s+privilege\s+\d+)?)\s+secret(?:\s+\d+)?|"
            r"snmp-server\s+community|snmp-agent\s+community(?:\s+read|\s+write)?|"
            r"(?:tacacs-server|radius-server|ntp\s+authentication-key)\s+key)\s+\S+",
            re.IGNORECASE,
        ),
        r"\1 [REDACTED]",
    ),
    (
        re.compile(
            r"\b(gsk_[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|"
            r"AIza[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{16,}|crk_[A-Za-z0-9_-]{10,}|"
            r"inv_[A-Za-z0-9_-]{10,}|wst_[A-Za-z0-9_-]{10,})\b"
        ),
        "[REDACTED_TOKEN]",
    ),
)

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above)\s+instructions?", re.IGNORECASE),
    re.compile(r"игнорируй(?:те)?\s+(?:все\s+)?(?:предыдущие|системные)\s+инструкции", re.IGNORECASE),
    re.compile(r"(?:^|\n)\s*ACTION\s*:", re.IGNORECASE),
)


def sanitize_for_llm(text: str) -> str:
    """Redact credentials/tokens and neutralize prompt-injection markers before LLM calls."""
    if not text:
        return ""
    cleaned = text
    for pat, repl in _SECRET_PATTERNS:
        cleaned = pat.sub(repl, cleaned)
    for pat in _INJECTION_PATTERNS:
        cleaned = pat.sub("[FILTERED_DIRECTIVE]", cleaned)
    return cleaned


def _max_risk_level(a: str, b: RiskLevel) -> RiskLevel:
    norm_a: RiskLevel = a if a in _RISK_ORDER else "MEDIUM"  # type: ignore[assignment]
    return norm_a if _RISK_ORDER[norm_a] >= _RISK_ORDER[b] else b


async def chat_with_llm(
    settings: Settings, system_prompt: str, user_prompt: str, timeout_floor: float = 2.0
) -> tuple[str, str] | None:
    """Free-form answer from the first provider that responds; None if every provider fails.

    Chat answers are bounded by `timeout_floor` (default 10.0s) to guarantee response.
    """
    safe_user_prompt = sanitize_for_llm(user_prompt)
    for name, base_url, token, model_name, timeout in _llm_candidates(settings):
        try:
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": safe_user_prompt},
                ],
                "temperature": 0.3,
            }
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "CROC-DREAM-NetOps/1.0",
            }
            raw = await asyncio.to_thread(
                _sync_http_request,
                f"{base_url.rstrip('/')}/chat/completions",
                headers,
                payload,
                max(timeout, timeout_floor),
            )
            content = json.loads(raw)["choices"][0]["message"]["content"].strip()
            return content, f"{name} ({model_name})"
        except Exception as exc:
            logger.warning("Copilot provider %s failed: %s", name, exc)
    return None


async def explain_change_with_llm(
    settings: Settings,
    hostname: str,
    platform: str,
    remediation_patch: str,
    rollback_patch: str,
) -> RiskExplanation:
    """Analyze remediation patch using multi-provider cascade with fast failover."""
    heuristic = analyze_diff_heuristic(hostname, platform, remediation_patch, rollback_patch)
    if not remediation_patch.strip():
        logger.info("Patch empty; using deterministic rule-engine.")
        return heuristic

    candidates = _llm_candidates(settings)
    if not candidates:
        logger.info("No LLM API tokens configured; using deterministic rule-engine fallback.")
        return heuristic

    safe_rem = sanitize_for_llm(remediation_patch)
    safe_rb = sanitize_for_llm(rollback_patch)
    user_prompt = f"""Проанализируй следующий конфигурационный патч:
Устройство: {hostname}
Платформа: {platform}

=== HierConfig Remediation Patch (что будет применено) ===
{safe_rem}

=== Rollback Patch (обратный откат в случае сбоя) ===
{safe_rb}
"""

    for prov_name, base_url, token, model_name, timeout in candidates:
        logger.info(
            "Querying LLM provider %s (%s, timeout=%.1fs)...",
            prov_name,
            model_name,
            timeout,
        )
        try:
            req_headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "CROC-DREAM-NetOps/1.0",
            }
            payload: dict[str, Any] = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
            }
            url = f"{base_url.rstrip('/')}/chat/completions"
            raw_text = await asyncio.to_thread(
                _sync_http_request, url, req_headers, payload, timeout
            )
            data = json.loads(raw_text)
            raw_content = data["choices"][0]["message"]["content"]
            match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw_content, re.DOTALL)
            clean_json = match.group(1) if match else raw_content.strip()
            parsed = json.loads(clean_json)
            raw_level = str(parsed.get("risk_level", "MEDIUM")).upper()
            final_level = _max_risk_level(raw_level, heuristic.risk_level)
            final_safe = (
                bool(parsed.get("is_safe", False))
                and heuristic.is_safe
                and final_level == "LOW"
            )
            llm_points = list(parsed.get("key_points") or [])
            for hp in heuristic.key_points:
                if hp not in llm_points:
                    llm_points.append(hp)
            return RiskExplanation(
                risk_level=final_level,
                summary=parsed.get("summary") or heuristic.summary,
                key_points=llm_points,
                recommendations=list(parsed.get("recommendations") or heuristic.recommendations),
                is_safe=final_safe,
                provider=f"{prov_name} ({model_name})",
            )
        except Exception as exc:
            logger.warning(
                "LLM provider %s (%s) failed or timed out (%.1fs): %s. "
                "Falling back to next in cascade...",
                prov_name,
                model_name,
                timeout,
                exc,
            )

    logger.warning("All LLM providers in cascade failed; using deterministic rule-engine.")
    heuristic.provider = "Deterministic Rule-Engine (Cascade Failover)"
    return heuristic

