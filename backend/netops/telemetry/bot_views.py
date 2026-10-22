"""Telegram bot read-only status views, metrics formatting, and alert aggregation."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from netops.enums import DeviceStatus, DriftStatus, JobStatus
from netops.models import Device, DriftRecord, Job
from netops.settings import Settings, get_settings
from netops.telemetry import collector, metrics
from netops.telemetry.bot_auth import _public_host, _web_urls
from netops.telemetry.metrics import Sample

MAX_ROWS = 10

_BOT_HELP_TEXT = (
    "Метрики, AI-Копилот, реестр и безопасность NetOps:\n"
    "/ai [вопрос] — ИИ-аудит фабрики, прогноз нагрузки и ответ на любой вопрос\n"
    "/fabrics — список подключенных кластеров/стендов и политика подключения новых\n"
    "/collect — принудительно снять свежие метрики с роутеров по SSH\n"
    "/scan — запустить внеочередное сканирование дрейфа (Drift Scan)\n"
    "/freeze [on|off] — аварийный Kill Switch (блокировка деплоев на фабрике)\n"
    "/verify <код> — привязать Telegram к системе по 6-значному коду из фронтенда\n"
    "/overview — наглядная сводка: что и где сейчас происходит на стенде\n"
    "/status — устройства, IP:порт, роли и статусы синхронизации\n"
    "/drift — где найден дрейф и какие именно строки расходятся\n"
    "/jobs — последние задачи пайплайна и целевые узлы\n"
    "/metrics — живая телеметрия CPU, RAM, портов, BGP и пингов\n"
    "/users — единый реестр участников (ID, TG ID, системы, сроки токенов)\n"
    "/token <имя> — открыть персональную карточку и пульт управления участником\n"
    "/ttl <имя> <минуты|forever> — задать время жизни токена или навсегда\n"
    "/security — журнал событий безопасности и заблокированных атак\n"
    "/block <имя> / /unblock <имя> — ограничить или разблокировать доступ\n"
    "/role <имя> <viewer|operator|admin> — сменить роль\n"
    "/unbind <имя> — отвязать устройство от ссылки"
)


def _max_drift_id(session: Session) -> int:
    record = session.scalars(select(DriftRecord).order_by(DriftRecord.id.desc()).limit(1)).first()
    return record.id if record is not None else 0


def _failed_jobs(session: Session) -> Sequence[Job]:
    return session.scalars(
        select(Job)
        .where(Job.status == JobStatus.FAILED)
        .order_by(Job.finished_at.desc())
        .limit(MAX_ROWS)
    ).all()


def _drift_details(record: DriftRecord) -> str:
    if record.error:
        return record.error
    return (
        f"лишних строк: {len(record.unauthorized_lines)}, не хватает: {len(record.missing_lines)}"
    )


def _overview_text(session: Session, settings: Settings | None = None) -> str:
    from netops.models import User

    driver = settings.network_driver if settings is not None else "scrapli"
    mode_str = "ONLINE SSH (Scrapli)" if driver == "scrapli" else "OFFLINE (эмуляция)"
    devices = session.scalars(select(Device).order_by(Device.hostname)).all()
    samples, collected_at = collector.latest()

    cpu_by_host: dict[str, float] = {}
    mem_by_host: dict[str, float] = {}
    uplink_by_host: dict[str, float] = {}
    bgp_up_by_host: dict[str, list[str]] = {}
    for s in samples:
        h = str(s.labels.get("hostname", ""))
        if s.name in (metrics.DEVICE_CPU_UTIL, metrics.SERVER_CPU_UTIL, metrics.CPU_USAGE):
            cpu_by_host[h] = s.value
        elif s.name in (metrics.DEVICE_MEMORY_UTIL, metrics.SERVER_MEMORY_UTIL, metrics.MEMORY_USAGE):
            mem_by_host[h] = s.value
        elif s.name in (metrics.DEVICE_UPLINK_UTIL, metrics.UPLINK_UTIL):
            uplink_by_host[h] = s.value
        elif s.name == metrics.BGP_SESSION_UP:
            state_lbl = "UP" if s.value else "DOWN"
            bgp_up_by_host.setdefault(h, []).append(f"{s.labels.get('peer')}:{state_lbl}")

    lines = [
        f"Состояние стенда ({mode_str}) — узлов: {len(devices)}"
        + (f", срез {collected_at:%H:%M:%S} UTC" if collected_at else ""),
    ]
    for d in devices:
        cpu = f"{cpu_by_host[d.hostname]:g}%" if d.hostname in cpu_by_host else "—"
        mem = f"{mem_by_host[d.hostname]:g}%" if d.hostname in mem_by_host else "—"
        upl = f"{uplink_by_host[d.hostname]:g}%" if d.hostname in uplink_by_host else "—"
        bgp_peers = ", ".join(bgp_up_by_host.get(d.hostname, [])) or "нет соседей"
        lines.append(
            f"• {d.hostname} ({d.platform.value}, {d.management_ip}:{d.management_port})\n"
            f"  Статус: {d.status.value} | CPU: {cpu} | RAM: {mem} | Uplink: {upl}\n"
            f"  BGP: {bgp_peers}"
        )

    jobs = session.scalars(select(Job).order_by(Job.created_at.desc()).limit(3)).all()
    if jobs:
        lines.append("\nПоследние операции:")
        for j in jobs:
            lines.append(
                f"• {j.created_at:%H:%M:%S} {j.type.value} -> {j.status.value} "
                f"({j.progress}%, автор: {j.created_by})"
            )

    users = session.scalars(select(User).order_by(User.username)).all()
    if users:
        active_cnt = sum(1 for u in users if u.is_active)
        bound_cnt = sum(1 for u in users if u.bound_client_id)
        lines.append(
            f"\nУчастников в реестре: {len(users)} (активных: {active_cnt}, сессий: {bound_cnt})"
        )

    return "\n".join(lines)


def _status_text(session: Session) -> str:
    devices = session.scalars(select(Device).order_by(Device.hostname)).all()
    if not devices:
        return "Устройств в инвентаре нет"
    by_status: dict[DeviceStatus, list[str]] = {}
    for device in devices:
        by_status.setdefault(device.status, []).append(device.hostname)
    lines = [f"Устройств: {len(devices)}"]
    lines.extend(
        f"{status.value}: {len(hostnames)} ({', '.join(hostnames)})"
        for status, hostnames in sorted(by_status.items())
    )
    lines.append("\nДетализация по узлам:")
    for d in devices:
        checked = f"{d.last_checked_at:%H:%M:%S}" if d.last_checked_at else "не проверялся"
        lines.append(
            f"• {d.hostname} [{d.role.value}/{d.platform.value}] "
            f"{d.management_ip}:{d.management_port} — {d.status.value} (проверен: {checked})"
        )
    return "\n".join(lines)


def _drift_text(session: Session) -> str:
    records = session.scalars(
        select(DriftRecord)
        .where(DriftRecord.status != DriftStatus.IN_SYNC)
        .order_by(DriftRecord.checked_at.desc())
        .limit(MAX_ROWS)
    ).all()
    if not records:
        return "Дрейфа нет: все устройства совпадают с эталоном"
    lines: list[str] = []
    for record in records:
        head = (
            f"{record.device.hostname} — {record.status.value}, "
            f"{record.checked_at:%d.%m %H:%M}: {_drift_details(record)}"
        )
        lines.append(head)
        for u_line in (record.unauthorized_lines or [])[:3]:
            lines.append(f"  + {u_line}")
        for m_line in (record.missing_lines or [])[:3]:
            lines.append(f"  - {m_line}")
    return "\n".join(lines)


def _jobs_text(session: Session) -> str:
    jobs = session.scalars(select(Job).order_by(Job.created_at.desc()).limit(5)).all()
    if not jobs:
        return "Задач пока не было"
    lines: list[str] = []
    for job in jobs:
        target_names = ", ".join(t.hostname for t in job.targets) if job.targets else "все узлы"
        lines.append(
            f"{job.created_at:%d.%m %H:%M} {job.type.value} — {job.status.value}"
            f" ({job.progress}%), автор {job.created_by} [{target_names}]"
        )
    return "\n".join(lines)


def _metrics_text() -> str:
    samples, collected_at = collector.latest()
    if collected_at is None:
        return "Метрики ещё не собирались"
    lines = [f"Сбор: {collected_at:%d.%m %H:%M:%S} UTC"]
    lines.extend(_summary(samples))
    return "\n".join(lines)


def _summary(samples: Iterable[Sample]) -> list[str]:
    devices = drift = 0.0
    unreachable: list[str] = []
    bgp: list[tuple[str, float]] = []
    ports: list[tuple[str, float]] = []
    losses: list[tuple[str, float]] = []
    cpus: list[str] = []
    mems: list[str] = []
    uplinks: list[str] = []
    for sample in samples:
        host = str(sample.labels.get("hostname", ""))
        if sample.name == metrics.DEVICES_TOTAL:
            devices += sample.value
        elif sample.name == metrics.DRIFT_DEVICES:
            drift = sample.value
        elif sample.name == metrics.DEVICE_REACHABLE and not sample.value:
            unreachable.append(host)
        elif sample.name == metrics.BGP_SESSION_UP:
            bgp.append((f"{host} → {sample.labels.get('peer')}", sample.value))
        elif sample.name == metrics.INTERFACE_UP:
            ports.append((f"{host} {sample.labels.get('interface')}", sample.value))
        elif sample.name == metrics.PING_LOSS:
            losses.append((f"{host} → {sample.labels.get('peer')}", sample.value))
        elif sample.name in (metrics.DEVICE_CPU_UTIL, metrics.CPU_USAGE):
            cpus.append(f"{host}: {sample.value:g}%")
        elif sample.name in (metrics.DEVICE_MEMORY_UTIL, getattr(metrics, "MEMORY_USAGE", "netops_memory_usage_percent")):
            mems.append(f"{host}: {sample.value:g}%")
        elif sample.name in (metrics.DEVICE_UPLINK_UTIL, metrics.UPLINK_UTIL):
            uplinks.append(f"{host}: {sample.value:g}%")

    lines = [
        f"Устройств: {devices:g}"
        + (f", не отвечают: {', '.join(unreachable)}" if unreachable else ", все отвечают")
    ]
    if cpus:
        lines.append(f"CPU: {', '.join(cpus)}")
    if mems:
        lines.append(f"RAM: {', '.join(mems)}")
    if uplinks:
        lines.append(f"Аплинки: {', '.join(uplinks)}")
    lines.append(_ratio("BGP Established", bgp))
    lines.append(_ratio("Порты up/up", ports))
    if losses:
        worst, loss = max(losses, key=lambda item: item[1])
        lines.append(f"Потери ping: до {loss:g}%" + (f" ({worst})" if loss else ""))
    lines.append(f"Дрейф: {drift:g} устройств" if drift else "Дрейф: нет")
    return lines


def _ratio(title: str, items: list[tuple[str, float]]) -> str:
    if not items:
        return f"{title}: данных нет"
    down = [name for name, value in items if not value]
    line = f"{title}: {len(items) - len(down)} из {len(items)}"
    if not down:
        return line
    shown = ", ".join(down[:20])
    extra = f" (+{len(down) - 20})" if len(down) > 20 else ""
    return f"{line}, проблемы: {shown}{extra}"


def _users_text(session: Session, settings: Settings | None = None) -> str:
    from netops.models import User
    from netops.services.notifier import load_telegram_bindings

    users = session.scalars(select(User).order_by(User.username)).all()
    if not users:
        return "Персональных ссылок в БД пока нет."
    tg_bindings = load_telegram_bindings(settings)
    lines = [f"Единый реестр участников, токенов и систем ({len(users)}):"]
    for u in users:
        state_label = "[ACTIVE]" if u.is_active else "[BLOCKED]"
        scope = u.allowed_system or "Вся фабрика"
        if u.bound_client_id:
            dev_info = f"привязана: {u.bound_client_id}" + (
                f" ({u.bound_ip})" if u.bound_ip else ""
            )
        else:
            dev_info = "свободна"
        tg_match = next(
            (b for b in tg_bindings.values() if b.get("username") == u.username),
            None,
        )
        extra = f" · ID #{u.id}"
        ttl_str = (
            f"до {u.expires_at.strftime('%d.%m %H:%M UTC')}"
            if getattr(u, "expires_at", None)
            else "НАВСЕГДА"
        )
        extra += f" · срок: {ttl_str}"
        tg_cid = getattr(u, "telegram_chat_id", None) or (
            tg_match.get("chat_id") if (tg_match and settings is not None) else None
        )
        tg_handle = getattr(u, "telegram_username", None) or (
            tg_match.get("tg_username") if (tg_match and settings is not None) else None
        )
        if tg_cid and settings is not None:
            extra += f" · TG: @{tg_handle} [{tg_cid}]" if tg_handle else f" · TG: [{tg_cid}]"
        if getattr(u, "verification_code", None):
            extra += f" · КОД TG: {u.verification_code}"
        lines.append(
            f"{state_label} {u.username} [{u.role.value}] · система: {scope} · {dev_info}{extra}"
        )
    return "\n".join(lines)


def _security_text(settings: Settings | None = None) -> str:
    from netops.services.notifier import load_access_registry

    reg = load_access_registry(settings)
    events = list(reg.get("security_events") or [])
    if not events:
        return "Журнал безопасности чист: подозрительных событий не зафиксировано."
    lines = [f"Журнал событий безопасности (последние {min(len(events), 12)}):"]
    for ev in events[:12]:
        ts = str(ev.get("timestamp") or "")[11:19]
        sev = str(ev.get("severity") or "info").upper()
        etype = ev.get("type") or "EVENT"
        msg = ev.get("message") or ""
        lines.append(f"• {ts} [{sev}] {etype}: {msg}")
    return "\n".join(lines)


def _fabrics_text(session: Session, settings: Settings | None = None) -> str:
    eff = settings or get_settings()
    devices = list(session.scalars(select(Device).order_by(Device.hostname)).all())
    by_site: dict[str, list[Device]] = {}
    for d in devices:
        site_key = (getattr(d, "site", None) or "dc1").strip() or "dc1"
        by_site.setdefault(site_key, []).append(d)

    host = _public_host(eff)
    driver = "ONLINE SSH (Scrapli)" if eff.network_driver == "scrapli" else "OFFLINE (эмуляция)"
    pub_base, _ = _web_urls(eff)
    lines = [
        f"🏗 Подключенные кластеры и стенды ({driver} -> {host}):",
    ]
    if not by_site:
        lines.append("• Нет подключенных стендов (инвентарь пуст).")
    else:
        for site_name, site_devs in sorted(by_site.items()):
            sync_cnt = sum(1 for d in site_devs if d.status is DeviceStatus.IN_SYNC)
            drift_cnt = sum(1 for d in site_devs if d.status is DeviceStatus.DRIFT_DETECTED)
            unreach_cnt = sum(1 for d in site_devs if d.status is DeviceStatus.UNREACHABLE)
            state_badge = "🟢 OK" if (drift_cnt == 0 and unreach_cnt == 0) else "⚠️ ALERT"
            lines.append(
                f"• Стенд «{site_name}» [{state_badge}]: узлов {len(site_devs)} "
                f"(Sync: {sync_cnt}, Drift: {drift_cnt}, Unreachable: {unreach_cnt})"
            )

    lines.extend(
        [
            "",
            "🔐 Политика безопасности (Zero-Secret Chat):",
            "Подключение нового кластера/сервера по SSH и ввод паролей/ключей через чат Telegram "
            "отключены намеренно — переписка мессенджера хранит историю в открытом виде.",
            f"Для безопасного добавления нового кластера или авто-обнаружения топологии Containerlab "
            f"откройте мастер «Матрёшка» в веб-панели ({pub_base}/).",
        ]
    )
    return "\n".join(lines)


def _new_alerts(session: Session, state: Any, settings: Settings | None = None) -> list[str]:
    from netops.services.ai_guard import AiTelemetryGuard

    alerts: list[str] = []
    records = session.scalars(
        select(DriftRecord)
        .where(DriftRecord.id > state.last_drift_id)
        .order_by(DriftRecord.id)
    ).all()
    # Скан идёт каждые 15 минут и каждый раз пишет запись по каждому устройству.
    # Поэтому сообщаем только о смене состояния, иначе бот спамит одним и тем же.
    for record in records:
        state.last_drift_id = max(state.last_drift_id, record.id)
        hostname = record.device.hostname
        if state.device_status.get(hostname) is record.status:
            continue
        previous = state.device_status.get(hostname)
        state.device_status[hostname] = record.status
        if record.status is DriftStatus.IN_SYNC:
            if previous is not None:
                alerts.append(f"[OK] {hostname}: снова совпадает с эталоном")
            continue
        alerts.append(f"[ALERT] {hostname}: {record.status.value}\n{_drift_details(record)}")
    # Плановая задача падает так же регулярно, поэтому один и тот же текст не повторяем.
    for job in _failed_jobs(session):
        if str(job.id) in state.reported_jobs:
            continue
        state.reported_jobs.add(str(job.id))
        text = f"[ERROR] Задача {job.type.value} упала: {job.error or 'без описания'}"
        if text in state.reported_errors:
            continue
        state.reported_errors.add(text)
        alerts.append(text)
    # Проактивные оповещения о «тихих» авариях трафика (AI Telemetry Guard Blackhole / Storm)
    guard = AiTelemetryGuard(settings or get_settings())
    for dev in session.scalars(select(Device).order_by(Device.hostname)).all():
        injected = guard.get_injected_anomaly(dev.hostname)
        if injected is not None:
            if state.reported_anomalies.get(dev.hostname) != injected:
                state.reported_anomalies[dev.hostname] = injected
                verdict = guard.verify_execution(dev)
                alerts.append(
                    f"[AI GUARD ALERT] {dev.hostname}: {verdict.problem or injected}"
                )
        elif dev.hostname in state.reported_anomalies:
            state.reported_anomalies.pop(dev.hostname, None)
            alerts.append(f"[OK] {dev.hostname}: телеметрия вернулась в норму (AI Guard)")
    return alerts
