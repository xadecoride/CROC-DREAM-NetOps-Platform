"""Telegram bot mutating actions, user access controls, Kill Switch, and AI copilot handler."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from netops.db import utcnow
from netops.enums import DeviceStatus, IntentSource, JobType, UserRole
from netops.models import Device, Job, JobTarget, User
from netops.settings import Settings, get_settings
from netops.telemetry import collector
from netops.telemetry.bot_auth import _bot_username, _web_urls, ensure_user_token
from netops.telemetry.bot_views import _summary, _users_text


def _cleanup_duplicates_text(session: Session, settings: Settings | None = None) -> str:
    users = list(session.scalars(select(User).order_by(User.id)).all())
    removed: list[str] = []
    for u in users:
        if not u.telegram_chat_id and u.username.startswith(("owner-", "viewer-")):
            removed.append(u.username)
            session.delete(u)
    session.commit()
    if not removed:
        return "Дублирующихся гостевых сессий не обнаружено.\n\n" + _users_text(session, settings)
    return f"Очищено временных дублей ({len(removed)}): {', '.join(removed)}\n\n" + _users_text(
        session, settings
    )


def _token_text(session: Session, args: Sequence[str], *, settings: Settings | None = None) -> str:
    if not args:
        return "Укажите имя пользователя: /token <username>"
    username = args[0].strip()
    user = session.scalar(select(User).where(User.username == username))
    if user is None:
        return f"Пользователь {username} не найден"
    tok = ensure_user_token(session, user, settings)
    state_icon = "🟢 АКТИВЕН" if user.is_active else "🚫 ЗАБЛОКИРОВАН"
    pub_base, loc_base = _web_urls(settings)
    bot_name = _bot_username(settings)
    return (
        f"Участник #{user.id}: {user.username} ({state_icon}, {user.role.value.upper()})\n"
        f"Токен: {tok}\n"
        f"Публичный вход: {pub_base}/?token={tok}\n"
        f"Локальный вход: {loc_base}/?token={tok}\n"
        f"Привязка TG: https://t.me/{bot_name}?start={tok}"
    )


def _set_user_ttl_text(session: Session, args: Sequence[str]) -> str:
    if len(args) < 2:
        return "Укажите участника и время: /ttl <username> <минуты|forever>"
    username, ttl_arg = args[0].strip(), args[1].strip().lower()
    user = session.scalar(select(User).where(User.username == username))
    if user is None:
        return f"Пользователь {username} не найден"
    if ttl_arg in ("forever", "0", "none", "inf", "навсегда"):
        user.expires_at = None
        label = "НАВСЕГДА (бессрочный)"
    elif ttl_arg.isdigit() and int(ttl_arg) > 0:
        mins = int(ttl_arg)
        user.expires_at = utcnow() + timedelta(minutes=mins)
        label = f"{mins} мин (до {user.expires_at.strftime('%Y-%m-%d %H:%M UTC')})"
    else:
        return "Укажите число минут (например, 60) или forever (навсегда)"
    session.commit()
    return f"Срок действия токена для {username} изменён: {label}"


def _set_user_active_text(session: Session, args: Sequence[str], *, active: bool) -> str:
    if not args:
        return f"Укажите имя пользователя: /{'unblock' if active else 'block'} <username>"
    username = args[0].strip()
    if username == "owner" and not active:
        return "Нельзя заблокировать владельца стенда (owner)"
    user = session.scalar(select(User).where(User.username == username))
    if user is None:
        return f"Пользователь {username} не найден"
    user.is_active = active
    session.commit()
    return (
        f"Доступ для {username} возобновлён"
        if active
        else f"Доступ для {username} ограничен (заблокирован)"
    )


def _unbind_user_text(session: Session, args: Sequence[str]) -> str:
    from netops.telemetry.bot_auth import _clear_bound_client_session

    if not args:
        return "Укажите имя пользователя: /unbind <username>"
    username = args[0].strip()
    user = session.scalar(select(User).where(User.username == username))
    if user is None:
        return f"Пользователь {username} не найден"
    _clear_bound_client_session(session, user)
    return f"Привязка устройства для {username} сброшена. Ссылка снова свободна."


def _set_user_role_text(session: Session, args: Sequence[str]) -> str:
    if len(args) < 2:
        return "Укажите имя и роль: /role <username> <viewer|operator|admin>"
    username, role_str = args[0].strip(), args[1].strip().lower()
    if username == "owner":
        return "Роль владельца стенда (owner) изменить нельзя"
    if role_str not in ("viewer", "operator", "admin"):
        return "Допустимые роли: viewer, operator, admin"
    user = session.scalar(select(User).where(User.username == username))
    if user is None:
        return f"Пользователь {username} не найден"
    user.role = UserRole(role_str)
    session.commit()
    return f"Роль для {username} изменена на {role_str}"


def _collect_now_text(
    session: Session, settings: Settings | None = None, args: Sequence[str] = ()
) -> str:
    from netops.toolchain import build_toolchain

    eff = settings or get_settings()
    try:
        toolchain = build_toolchain(eff)
        ttl = eff.metrics_interval_seconds * 3
        stand = args[0].strip() if args else None
        if stand:
            dev_samples = collector.device_samples(
                session,
                toolchain,
                max_workers=16,
                include_fabric_label=True,
                fabric_id=stand,
            )
            ts = collector.publish(dev_samples, ttl_seconds=ttl, merge=True, merge_fabric=stand)
            samples = collector.platform_samples(session) + dev_samples
        else:
            samples = collector.collect(
                session, toolchain, include_fabric_label=True, max_workers=16
            )
            ts = collector.publish(samples, ttl_seconds=ttl)
        lines = [f"✅ Сбор живой телеметрии завершён ({ts:%H:%M:%S} UTC):"]
        lines.extend(_summary(samples))
        return "\n".join(lines)
    except Exception as exc:
        return f"⚠️ Ошибка при сборе телеметрии: {exc}"


def _scan_now_text(
    session: Session,
    session_factory: sessionmaker[Session],
    settings: Settings | None = None,
) -> str:
    from netops.pipeline.runner import JobRunner
    from netops.toolchain import build_toolchain

    eff = settings or get_settings()
    devices = list(session.scalars(select(Device).order_by(Device.hostname)).all())
    if not devices:
        return "В инвентаре нет устройств для сканирования."
    job = Job(
        type=JobType.DRIFT_SCAN,
        intent_source=IntentSource.GIT_MAIN.value,
        created_by="telegram-operator",
    )
    job.targets = [JobTarget(device_id=d.id, hostname=d.hostname) for d in devices]
    session.add(job)
    session.commit()
    job_id = job.id

    def _bg() -> None:
        runner = JobRunner(session_factory, lambda: build_toolchain(eff))
        runner.run(job_id)

    threading.Thread(target=_bg, daemon=True, name=f"tg-scan-{job_id}").start()
    return (
        f"🔍 Запущено внеочередное сканирование дрейфа (Job {str(job_id)[:8]}).\n"
        f"Целевые узлы ({len(devices)}): {', '.join(d.hostname for d in devices)}.\n"
        "По завершении опроса по SSH бот пришлёт отчёт."
    )


def _freeze_text(args: Sequence[str], settings: Settings | None = None) -> str:
    from netops.services.freeze import get_freeze_state, set_factory_freeze
    from netops.services.notifier import record_security_event

    if args:
        act = args[0].strip().lower()
        if act in ("on", "1", "true", "freeze"):
            reason = " ".join(args[1:]) or "Активирован через Telegram Kill Switch"
            set_factory_freeze(frozen=True, reason=reason, user="telegram-admin")
            record_security_event(
                "KILL_SWITCH_ENGAGED",
                f"Фабрика заморожена из Telegram: {reason}",
                severity="critical",
                settings=settings,
            )
        elif act in ("off", "0", "false", "unfreeze"):
            set_factory_freeze(frozen=False, reason="", user="telegram-admin")
            record_security_event(
                "KILL_SWITCH_RELEASED",
                "Заморозка фабрики снята из Telegram",
                severity="info",
                settings=settings,
            )
    state = get_freeze_state()
    if state.get("frozen"):
        return (
            f"❄️ KILL SWITCH АКТИВЕН (ФАБРИКА ЗАМОРОЖЕНА)\n"
            f"Причина: {state.get('reason')}\n"
            f"Кем включён: {state.get('updated_by')}\n"
            "Любые операции DEPLOY и DRIFT_REMEDIATE заблокированы.\n"
            "Для снятия: /freeze off"
        )
    return (
        "🟢 Kill Switch отключён: фабрика в штатном режиме (деплой разрешён).\n"
        "Для экстренной блокировки изменений: /freeze on <причина>"
    )


def _ai_copilot_text(
    session: Session, args: Sequence[str], *, settings: Settings | None = None
) -> str:
    from netops.api.routes.insights import select_rag_devices
    from netops.services.ai_guard import AiTelemetryGuard
    from netops.services.freeze import is_factory_frozen
    from netops.services.llm import chat_with_llm

    eff = settings or get_settings()
    question = " ".join(args).strip()[:500]
    devices = list(session.scalars(select(Device).order_by(Device.hostname)).all())
    samples, collected_at = collector.latest()
    guard = AiTelemetryGuard(eff)

    verdict_by_host: dict[str, str] = {}
    anomalies: list[str] = []
    ai_anom_map: dict[str, str] = {}
    for d in devices:
        v_cpu = guard.verify_execution(d, metric="cpu_pct", horizon=12)
        v_upl = guard.verify_execution(d, metric="uplink_util_pct", horizon=12)
        ok_pre, pre_msg = guard.check_preflight(d)
        status_tag = "OK" if (v_cpu.healthy and v_upl.healthy and ok_pre) else "ALERT"
        verdict_by_host[d.hostname] = (
            f"• {d.hostname} [{d.status.value}]: Guard={status_tag} | "
            f"CPU={v_cpu.observed_value:g}% (коридор [{v_cpu.expected_range[0]:g}..{v_cpu.expected_range[1]:g}%]) | "
            f"{pre_msg}"
        )
        if not v_cpu.healthy and v_cpu.problem:
            anomalies.append(f"{d.hostname}: {v_cpu.problem}")
            ai_anom_map[d.hostname] = v_cpu.problem
        if not v_upl.healthy and v_upl.problem:
            anomalies.append(f"{d.hostname}: {v_upl.problem}")
            ai_anom_map[d.hostname] = v_upl.problem

    rag_devs = select_rag_devices(question, devices, ai_anomalies=ai_anom_map, top_k=12)
    verdicts = [verdict_by_host[d.hostname] for d in rag_devs if d.hostname in verdict_by_host]

    summary_lines = _summary(samples) if collected_at else ["Метрики ещё не собирались"]
    freeze_note = "ЗАМОРОЖЕНА (Kill Switch)" if is_factory_frozen() else "Штатный режим"
    context_block = (
        f"Режим фабрики: {freeze_note} (всего узлов: {len(devices)})\n"
        f"Сводка метрик:\n" + "\n".join(summary_lines) + "\n\n"
        "Вердикты мониторинга телеметрии:\n" + "\n".join(verdicts)
    )

    sys_prompt = (
        "Ты — сетевой ассистент платформы CROC DREAM NetOps (CLOS Spine-Leaf).\n"
        "Опирайся на переданные метрики, не раскрывай пароли/токены и название своей LLM-модели.\n"
        "Отвечай естественно, кратко и по делу (2–5 предложений) на русском языке, без лишних заголовков."
    )
    user_prompt = (
        f"Контекст сети:\n{context_block}\n\n"
        f"Сообщение: {question or 'Дай краткую сводку по состоянию сети и прогнозу нагрузки.'}"
    )
    try:
        res = asyncio.run(chat_with_llm(eff, sys_prompt, user_prompt, timeout_floor=4.0))
        if res is not None:
            return res[0].strip()
    except Exception:
        _ = None

    drifting = [d.hostname for d in devices if d.status is DeviceStatus.DRIFT_DETECTED]
    unreach = [d.hostname for d in devices if d.status is DeviceStatus.UNREACHABLE]
    problem_parts: list[str] = []
    if drifting:
        problem_parts.append(f"⚠️ Обнаружен дрейф конфигурации на узлах: {', '.join(drifting)}")
    if unreach:
        problem_parts.append(f"🚨 Недоступны по SSH: {', '.join(unreach)}")
    if anomalies:
        problem_parts.append("Обнаружены отклонения по прогнозу нагрузки:\n" + "\n".join(anomalies))
    if problem_parts:
        return "\n".join(problem_parts)
    if not devices:
        return "В фабрике пока нет подключенных устройств."
    in_sync = sum(1 for d in devices if d.status is DeviceStatus.IN_SYNC)
    return (
        f"Фабрика работает штатно: устройств в инвентаре — {len(devices)} (IN_SYNC: {in_sync}), "
        "прогноз нагрузки в пределах нормы.\n\n" + "\n".join(summary_lines)
    )
