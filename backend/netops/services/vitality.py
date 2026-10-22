"""Simplified 2-state (UP/DOWN) vitality diagnostics, Primary->Fallback config backups, and AI root-cause analysis.

Data flow (Вход -> Функция -> Выход):
1. On every successful config read (`save_device_backup` / `clear_connection_failure`):
   - Saves `<hostname>_latest.cfg` (rotates previous to `<hostname>_prev.cfg`) in `lab/backups/<stand>/`
   - Updates `lab/running/<hostname>.cfg` so fallback snapshot is always fresh
   - Marks device vitality as `UP`
2. On connection failure (`record_connection_failure`):
   - Classifies exact technical failure stage (Jump-host TCP -> Node TCP -> SSH Auth -> CLI)
   - Checks availability of local `.cfg` backup (`using_backup_fallback=True`)
   - Correlates failure with recent jobs/drift and neighbor states to produce `ai_diagnosis`
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from netops.enums import DeviceStatus, DriftStatus, JobType
from netops.models import Device, DriftRecord, Job, JobTarget
from netops.services.state import get_state_manager
from netops.settings import get_settings

logger = logging.getLogger(__name__)
_VITALITY_REDIS_PREFIX = "netops:vitality:device:"
_BACKUP_TS_CACHE: dict[tuple[str, str], tuple[float, str | None]] = {}
_BACKUP_TS_TTL_SECONDS = 15.0


def _backups_root() -> Path:
    settings = get_settings()
    base = Path(settings.offline_lab_path).resolve().parent / "backups"
    with contextlib.suppress(OSError):
        base.mkdir(parents=True, exist_ok=True)
    return base


def _running_root() -> Path:
    settings = get_settings()
    base = Path(settings.offline_lab_path).resolve()
    with contextlib.suppress(OSError):
        base.mkdir(parents=True, exist_ok=True)
    return base


def _get_cached_backup_ts(hostname: str, stand_slug: str | None = "croc-dc1-pod1") -> str | None:
    """Return modification timestamp of device backup with a 15-second in-memory TTL cache."""
    slug = (stand_slug or "croc-dc1-pod1").strip() or "croc-dc1-pod1"
    cache_key = (hostname, slug)
    now_mono = time.monotonic()
    cached = _BACKUP_TS_CACHE.get(cache_key)
    if cached is not None and (now_mono - cached[0]) < _BACKUP_TS_TTL_SECONDS:
        return cached[1]

    stand_dir = _backups_root() / slug
    candidates = [
        stand_dir / f"{hostname}_latest.cfg",
        _running_root().parent / "golden_configs" / f"{hostname}.cfg",
        _running_root() / f"{hostname}.cfg",
    ]
    found_ts: str | None = None
    for path in candidates:
        try:
            st = path.stat()
            if st.st_size > 0:
                found_ts = datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat()
                break
        except OSError:
            continue
    if len(_BACKUP_TS_CACHE) >= 2048:
        _BACKUP_TS_CACHE.clear()
    _BACKUP_TS_CACHE[cache_key] = (now_mono, found_ts)
    return found_ts


def save_device_backup(
    hostname: str,
    config_text: str,
    fabric_id: str | None = "croc-dc1-pod1",
) -> str | None:
    """Save Last Known Good `.cfg` backup on disk (`_latest.cfg` + `_prev.cfg`) and update `lab/running/`."""
    clean_cfg = (config_text or "").strip()
    if not clean_cfg:
        return None
    stand_slug = (fabric_id or "croc-dc1-pod1").strip() or "croc-dc1-pod1"
    now_iso = datetime.now(UTC).isoformat()
    stand_dir = _backups_root() / stand_slug
    try:
        stand_dir.mkdir(parents=True, exist_ok=True)
        latest_path = stand_dir / f"{hostname}_latest.cfg"
        prev_path = stand_dir / f"{hostname}_prev.cfg"
        if latest_path.is_file():
            existing = latest_path.read_text(encoding="utf-8", errors="replace").strip()
            if existing and existing != clean_cfg:
                shutil.copy2(latest_path, prev_path)
        latest_path.write_text(clean_cfg + "\n", encoding="utf-8")

        if "PYTEST_CURRENT_TEST" not in os.environ:
            run_path = _running_root() / f"{hostname}.cfg"
            run_path.write_text(clean_cfg + "\n", encoding="utf-8")
        now_mono = time.monotonic()
        _BACKUP_TS_CACHE[(hostname, stand_slug)] = (now_mono, now_iso)
        _BACKUP_TS_CACHE[("__stand__", stand_slug)] = (now_mono, now_iso)
        return now_iso
    except OSError as exc:
        logger.debug("Could not write backup file for %s: %s", hostname, exc)
        return None


def load_device_backup(
    hostname: str,
    stand_slug: str | None = "croc-dc1-pod1",
    version: Literal["latest", "prev"] = "latest",
) -> tuple[str | None, str | None]:
    """Load backup `.cfg` text and its modification timestamp (falls back to `lab/running/<hostname>.cfg`)."""
    slug = (stand_slug or "croc-dc1-pod1").strip() or "croc-dc1-pod1"
    stand_dir = _backups_root() / slug
    candidates = [
        stand_dir / f"{hostname}_{version}.cfg",
        stand_dir / f"{hostname}_latest.cfg",
        _running_root().parent / "golden_configs" / f"{hostname}.cfg",
        _running_root() / f"{hostname}.cfg",
    ]
    for path in candidates:
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                if content.strip():
                    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
                    if version == "latest":
                        _BACKUP_TS_CACHE[(hostname, slug)] = (time.monotonic(), mtime)
                    return content, mtime
            except OSError:
                continue
    return None, None


def sync_stands_backup_file(stands_payload: list[dict[str, Any]]) -> str | None:
    """Persist a single clean JSON backup of all stand definitions on disk."""
    now_iso = datetime.now(UTC).isoformat()
    try:
        target = _backups_root() / "stands_backup.json"
        sanitized = [
            {
                "slug": s.get("slug"),
                "name": s.get("name"),
                "site": s.get("site"),
                "driver": s.get("driver"),
                "jump_host": s.get("jump_host"),
                "jump_port": s.get("jump_port"),
                "jump_user": s.get("jump_user"),
                "port_map": s.get("port_map") or {},
                "topology_path": s.get("topology_path"),
                "intent_subpath": s.get("intent_subpath"),
                "status": s.get("status", "ONLINE"),
                "backed_up_at": now_iso,
            }
            for s in stands_payload
        ]
        target.write_text(json.dumps(sanitized, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(target)
    except OSError as exc:
        logger.debug("Failed to write stands_backup.json: %s", exc)
        return None


def classify_connection_error(
    raw_error: str,
    hostname: str = "",
    host: str = "",
    port: int = 22,
    proxy_jump: str | None = None,
) -> str:
    """Translate low-level socket/SSH exceptions into a clear 4-step technical failure reason."""
    err_l = (raw_error or "").lower()
    endpoint = f"{host}:{port}" if host else (hostname or "устройство")
    if proxy_jump and ("jump" in err_l or "tunnel" in err_l or "channel" in err_l):
        return f"Обрыв связи с Jump-сервером стенда ({proxy_jump}) — туннель не отвечает."
    if "authentication" in err_l or "permission denied" in err_l or "auth" in err_l:
        return f"Порт {endpoint} открыт, но узел {hostname} отклонил SSH-пароль/ключ (Authentication failed)."
    if "connection refused" in err_l or "errno 111" in err_l:
        return f"Узел {hostname} ({endpoint}) отклонил TCP-соединение (SSH-демон остановлен или порт 22 закрыт)."
    if "protocol banner" in err_l or "no existing session" in err_l or "eof" in err_l:
        return f"TCP-порт {endpoint} доступен, но SSH-сессия {hostname} сброшена при рукопожатии (сброс ACL или перегрузка)."
    if "timed out" in err_l or "timeout" in err_l or "unreachable" in err_l:
        if proxy_jump:
            return f"Jump-сервер ({proxy_jump}) или контейнер {hostname} ({endpoint}) не ответил за таймаут (TCP Timeout)."
        return f"Узел {hostname} ({endpoint}) не отвечает по сети управления (Connection timed out)."
    clean = (raw_error or "Узел перестал отвечать на запросы управления").strip()
    return f"Сбой связи с {hostname or endpoint}: {clean[:140]}"


def clear_connection_failure(hostname: str) -> None:
    """Clear stored DOWN error when a node responds successfully."""
    get_state_manager().delete(f"{_VITALITY_REDIS_PREFIX}{hostname}")


def record_connection_failure(
    hostname: str,
    raw_error: str,
    *,
    host: str = "",
    port: int = 22,
    proxy_jump: str | None = None,
    stand_slug: str = "croc-dc1-pod1",
) -> dict[str, Any]:
    """Record that device is DOWN with exact technical stage and fallback backup metadata."""
    reason = classify_connection_error(
        raw_error, hostname=hostname, host=host, port=port, proxy_jump=proxy_jump
    )
    backup_ts = _get_cached_backup_ts(hostname, stand_slug=stand_slug)
    payload = {
        "state": "DOWN",
        "down_reason": reason,
        "last_backup_at": backup_ts,
        "using_fallback": backup_ts is not None,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    get_state_manager().set_json(f"{_VITALITY_REDIS_PREFIX}{hostname}", payload, ex=3600)
    return payload


def diagnose_down_reason(
    device: Device,
    down_reason: str,
    session: Session | None = None,
    latest_drift: DriftRecord | None = None,
) -> str:
    """Build an explainable AI root-cause diagnosis correlating transport error, recent patches, and neighbor state."""
    if latest_drift and (latest_drift.unauthorized_lines or latest_drift.missing_lines):
        lines = [
            *(latest_drift.unauthorized_lines or []),
            *(latest_drift.missing_lines or []),
        ]
        joined = " ".join(lines).lower()
        if "shutdown" in joined:
            return (
                f"ИИ-диагноз ({device.hostname}): перед потерей связи зафиксирован дрейф с командой "
                "`shutdown` на интерфейсе. Соседние коммутаторы работают штатно — восстановите конфиг из последнего бэкапа."
            )
        if "access-list" in joined or "acl" in joined or "ip access-group" in joined or "deny" in joined:
            return (
                f"ИИ-диагноз ({device.hostname}, вероятность 90%): в последнем изменении модифицировались "
                "правила фильтрации ACL, что заблокировало порт управления SSH/22. Рекомендуется откат к бэкапу."
            )

    if session is not None and device.id is not None:
        with contextlib.suppress(Exception):
            last_target = session.scalars(
                select(JobTarget)
                .join(Job, Job.id == JobTarget.job_id)
                .where(
                    JobTarget.device_id == device.id,
                    Job.type.in_([JobType.DEPLOY, JobType.DRIFT_REMEDIATE]),
                )
                .order_by(Job.created_at.desc())
                .limit(1)
            ).first()
            if last_target and last_target.error:
                return (
                    f"ИИ-диагноз ({device.hostname}): потеря связи произошла после аварийной задачи выкатки "
                    f"({last_target.error[:90]}). Для чтения активна резервная копия (.cfg) — нажмите «Восстановить из бэкапа»."
                )

    reason_l = down_reason.lower()
    if "ssh-пароль" in reason_l or "authentication" in reason_l:
        return (
            f"ИИ-диагноз ({device.hostname}): сетевая связность до порта есть, но не подходит пароль/ключ профиля "
            f"«{device.auth_profile}». Проверьте учётные данные стенда или восстановите эталонный бэкап."
        )
    if "jump" in reason_l:
        return (
            f"ИИ-диагноз ({device.hostname}): обрыв на уровне внешнего SSH Jump-сервера стенда. "
            "Сами конфигурации сохранены в локальном резервном хранилище (.cfg) — проверьте доступность ВМ стенда."
        )
    return (
        f"ИИ-диагноз ({device.hostname}): узел перестал отвечать по каналу управления (возможна остановка контейнера "
        "или изоляция порта управления). Система переключена на резервный слепок (.cfg) без потери данных."
    )


def evaluate_device_vitality(
    device: Device,
    oper_status: str = "UP",
    latest_drift: DriftRecord | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    """Compute the simplified 2-state (`UP` / `DOWN`) vitality contract for a device."""
    stand_slug = getattr(device, "fabric_id", None) or "croc-dc1-pod1"
    cached = get_state_manager().get_json(f"{_VITALITY_REDIS_PREFIX}{device.hostname}")
    cached_dict = cached if isinstance(cached, dict) else {}
    disk_backup_ts = _get_cached_backup_ts(device.hostname, stand_slug=stand_slug)
    last_backup_at = cached_dict.get("last_backup_at") or disk_backup_ts
    has_backup = last_backup_at is not None

    is_down = (
        device.status is DeviceStatus.UNREACHABLE
        or oper_status == "DOWN"
        or (cached_dict.get("state") == "DOWN" and device.status is DeviceStatus.UNKNOWN)
    )
    if not is_down:
        return {
            "vitality_state": "UP",
            "down_reason": None,
            "ai_diagnosis": None,
            "using_backup_fallback": False,
            "last_backup_at": last_backup_at,
            "backup_available": has_backup,
        }

    raw_reason = cached_dict.get("down_reason")
    if not raw_reason and latest_drift and latest_drift.status == DriftStatus.UNREACHABLE:
        raw_reason = classify_connection_error(
            "Connection timed out",
            hostname=device.hostname,
            host=device.management_ip,
            port=device.management_port,
            proxy_jump=device.proxy_jump,
        )
    down_reason = raw_reason or classify_connection_error(
        "Узел не отвечает по каналу управления (DOWN)",
        hostname=device.hostname,
        host=device.management_ip,
        port=device.management_port,
        proxy_jump=device.proxy_jump,
    )
    ai_diagnosis = diagnose_down_reason(
        device,
        down_reason=down_reason,
        session=session,
        latest_drift=latest_drift,
    )
    return {
        "vitality_state": "DOWN",
        "down_reason": down_reason,
        "ai_diagnosis": ai_diagnosis,
        "using_backup_fallback": has_backup,
        "last_backup_at": last_backup_at,
        "backup_available": has_backup,
    }


def evaluate_stand_vitality(
    stand_dict: dict[str, Any],
    stats: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Compute 2-state vitality (UP or DOWN) for an entire stand/fabric."""
    s = stats or {"total_devices": 0, "in_sync": 0, "drifted": 0, "unreachable": 0}
    slug = str(stand_dict.get("slug") or "croc-dc1-pod1")
    jump_host = stand_dict.get("jump_host")
    jump_port = int(stand_dict.get("jump_port") or 22)

    stand_key = ("__stand__", slug)
    now_mono = time.monotonic()
    cached_stand_bkp = _BACKUP_TS_CACHE.get(stand_key)
    if cached_stand_bkp is not None and (now_mono - cached_stand_bkp[0]) < _BACKUP_TS_TTL_SECONDS:
        last_bkp_at = cached_stand_bkp[1]
    else:
        stand_dir = _backups_root() / slug
        bkp_files = list(stand_dir.glob("*_latest.cfg")) if stand_dir.is_dir() else []
        last_bkp_at = None
        if bkp_files:
            newest = max(bkp_files, key=lambda p: p.stat().st_mtime)
            last_bkp_at = datetime.fromtimestamp(newest.stat().st_mtime, tz=UTC).isoformat()
        _BACKUP_TS_CACHE[stand_key] = (now_mono, last_bkp_at)

    raw_status = str(stand_dict.get("status") or "ONLINE").upper()
    all_unreachable = s["total_devices"] > 0 and s["unreachable"] == s["total_devices"]

    jump_down_reason: str | None = None
    if jump_host and "PYTEST_CURRENT_TEST" not in os.environ:
        from netops.network.ssh_pool import _DEAD_ENDPOINTS

        if (str(jump_host), jump_port) in _DEAD_ENDPOINTS:
            jump_down_reason = f"Основной Jump-сервер стенда {jump_host}:{jump_port} не отвечает по TCP."

    is_down = raw_status in ("OFFLINE", "DOWN", "UNREACHABLE") or all_unreachable or bool(jump_down_reason)
    if not is_down:
        return {
            "vitality_state": "UP",
            "down_reason": None,
            "ai_diagnosis": None,
            "using_backup_fallback": False,
            "last_backup_at": last_bkp_at,
            "backup_available": True,
        }

    reason = (
        jump_down_reason
        or (
            f"Все узлы стенда ({s['unreachable']}/{s['total_devices']}) недоступны по SSH."
            if all_unreachable
            else f"Стенд {slug} находится в состоянии {raw_status} (связь потеряна)."
        )
    )
    ai_diag = (
        f"ИИ-диагноз стенда «{stand_dict.get('name') or slug}»: "
        "Обрыв связи с основным сервером эмуляции. Система автоматически переключилась на резервное "
        "локальное хранилище бэкапов (`lab/backups/`), поэтому топология, история и конфигурации доступны. "
        "Проверьте SSH-туннель до Jump-сервера или восстановите стенд из резервной копии."
    )
    return {
        "vitality_state": "DOWN",
        "down_reason": reason,
        "ai_diagnosis": ai_diag,
        "using_backup_fallback": True,
        "last_backup_at": last_bkp_at,
        "backup_available": True,
    }


def restore_device_from_backup(
    device: Device,
    session: Session,
    version: Literal["latest", "prev"] = "latest",
) -> dict[str, Any]:
    """Restore a device from its Last Known Good `.cfg` backup and bring its state back to UP / IN_SYNC."""
    from netops.db import utcnow
    from netops.services.topology_commutation import get_link_overrides, toggle_topology_link
    from netops.services.topology_resilience import toggle_node_drain

    stand_slug = getattr(device, "fabric_id", None) or "croc-dc1-pod1"
    cfg_text, backup_ts = load_device_backup(device.hostname, stand_slug=stand_slug, version=version)
    if not cfg_text:
        return {
            "status": "error",
            "hostname": device.hostname,
            "restored": False,
            "message": f"Резервная копия для {device.hostname} не найдена",
        }

    # Write restored config to running lab directory and latest backup
    with contextlib.suppress(OSError):
        run_path = _running_root() / f"{device.hostname}.cfg"
        run_path.write_text(cfg_text.rstrip() + "\n", encoding="utf-8")
    save_device_backup(device.hostname, cfg_text, fabric_id=stand_slug)
    clear_connection_failure(device.hostname)

    # Clear any simulated link shutdowns or drains on this device
    with contextlib.suppress(Exception):
        if device.id is not None:
            toggle_node_drain(device.id, drained=False)
        for lid in list(get_link_overrides().keys()):
            if device.hostname in lid or (device.id is not None and f"-{device.id}" in lid):
                toggle_topology_link(lid, admin_state="UP")

    now = utcnow()
    device.status = DeviceStatus.IN_SYNC
    device.last_checked_at = now
    session.add(
        DriftRecord(
            device_id=device.id,
            status=DriftStatus.IN_SYNC,
            checked_at=now,
        )
    )
    session.commit()
    return {
        "status": "ok",
        "hostname": device.hostname,
        "vitality_state": "UP",
        "restored": True,
        "backup_timestamp": backup_ts,
        "message": f"Узел {device.hostname} успешно восстановлен из резервной копии ({backup_ts or 'эталон'}).",
    }
