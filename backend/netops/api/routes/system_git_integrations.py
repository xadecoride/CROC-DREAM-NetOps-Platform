"""Git SoT synchronization, GitHub webhooks, and system integrations status routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select

from netops.api.deps import ContainerDep, Operator, SessionDep, Viewer
from netops.enums import DeviceStatus, IntentSource, UserRole
from netops.intent.netbox import load_netbox_inventory
from netops.models import ConfigSnapshot, Device, DriftRecord, Job, User
from netops.schemas.users import UserRead
from netops.services import DeviceService, JobService
from netops.services.git_sot import git_commit_and_push_diff, git_pull_repo, git_repo_metadata
from netops.services.notifier import (
    format_executive_summary,
    load_telegram_bindings,
    load_user_tokens,
    resolve_telegram_targets,
    send_telegram_message,
    sync_access_registry,
)
from netops.services.telemetry import check_prometheus_status
from netops.telemetry import collector
from netops.telemetry.bot import bind_telegram_chat_user, ensure_user_token

router = APIRouter(tags=["system"])


class GitSyncRequest(BaseModel):
    trigger_dry_run: bool = False


class TelegramTestRequest(BaseModel):
    custom_note: str | None = None


class GitCommitDiffRequest(BaseModel):
    job_id: str


@router.get(
    "/git/status",
    tags=["intent"],
    summary="Статус репозитория GitHub / Git SoT и валидации YAML-интентов",
)
def get_git_status(container: ContainerDep, _: Viewer) -> dict[str, Any]:
    git_meta = git_repo_metadata(
        container.settings.intent_repo_path, container.settings.github_repo_url
    )
    issues = container.intents.lint()
    devices_dir = container.settings.intent_repo_path / "devices"
    yaml_count = len(list(devices_dir.glob("*.yaml"))) if devices_dir.is_dir() else 0
    return {
        **git_meta,
        "intent_path": str(container.settings.intent_repo_path),
        "inventory_file": container.settings.inventory_file,
        "yaml_devices_count": yaml_count,
        "valid": not issues,
        "issues_count": len(issues),
    }


@router.post(
    "/git/sync",
    tags=["intent"],
    summary="Синхронизировать Git SoT / NetBox с базой данных и опционально запустить Dry-Run",
)
def sync_git_sot(
    session: SessionDep,
    container: ContainerDep,
    user: Operator,
    req: GitSyncRequest | None = None,
) -> dict[str, Any]:
    if user.allowed_system is not None and user.allowed_system not in ("*", "all"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Global inventory/Git SoT sync requires platform admin or owner",
        )
    settings = container.settings
    pull_note = git_pull_repo(settings.intent_repo_path)
    if settings.netbox_url and settings.netbox_token:
        inventory = load_netbox_inventory(
            settings.netbox_url, settings.netbox_token.get_secret_value()
        )
        source_name = "netbox"
    else:
        inventory = container.intents.load_inventory()
        source_name = "git_main"

    sync_res = DeviceService(session).sync_inventory(inventory)
    dry_run_job_id: str | None = None
    if req and req.trigger_dry_run:
        from netops.api.routes.devices import filter_devices_by_system

        allowed_devs = filter_devices_by_system(
            DeviceService(session).list_devices(limit=5000), user.allowed_system
        )
        all_ids = [d.id for d in allowed_devs]
        if all_ids:
            job = JobService(session, container.dispatcher).create_dry_run(
                all_ids, IntentSource.GIT_MAIN, requested_by=user.username
            )
            dry_run_job_id = str(job.id)

    git_meta = git_repo_metadata(settings.intent_repo_path, settings.github_repo_url)
    sync_dict = {
        "created": sync_res.created,
        "updated": sync_res.updated,
        "unchanged": sync_res.unchanged,
    }
    return {
        "status": "ok",
        "source": source_name,
        "pull_output": f"{source_name} ({git_meta['branch']}@{git_meta['commit_short']}) [{pull_note}]",
        "sync": sync_dict,
        "git": git_meta,
        **sync_dict,
        "dry_run_job_id": dry_run_job_id,
    }


@router.post(
    "/git/commit-diff",
    tags=["intent"],
    summary="Сформировать снапшот Diff задачи, сделать git commit и отправить по SSH (git push)",
)
def commit_diff_to_git(
    req: GitCommitDiffRequest,
    session: SessionDep,
    container: ContainerDep,
    user: Operator,
) -> dict[str, Any]:
    import uuid as _uuid

    from netops.api.routes.jobs import _assert_job_allowed
    from netops.pipeline.pipelines import serialize_job_target_diffs

    settings = container.settings
    rt = settings.apply_runtime_overlay()
    job_svc = JobService(session, container.dispatcher)
    job = job_svc.get_with_diff(_uuid.UUID(req.job_id))
    _assert_job_allowed(job, user.allowed_system)
    device_diffs = serialize_job_target_diffs(job)
    res = git_commit_and_push_diff(
        settings.intent_repo_path,
        job_id=str(job.id),
        job_type=str(getattr(job.type, "value", job.type)),
        device_diffs=device_diffs,
        branch=str(rt.get("github_branch") or "main"),
        remote_url=str(rt.get("github_repo_url") or ""),
    )
    return res


@router.post(
    "/git/webhook",
    tags=["intent"],
    summary="GitHub Webhook: авто-синхронизация инвентаря БД и префлайт Dry-Run при push в Git",
)
def github_webhook(
    payload: dict[str, Any],
    session: SessionDep,
    container: ContainerDep,
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    rt = container.settings.apply_runtime_overlay()
    secret = str(rt.get("github_webhook_secret") or "").strip()
    sig_header = (
        (request.headers.get("X-Hub-Signature-256") or "").strip()
        if request is not None
        else ""
    )
    if secret:
        import hashlib
        import hmac
        import json as _json

        raw_body = getattr(request, "_body", None) or _json.dumps(payload).encode("utf-8")
        expected = (
            "sha256="
            + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        )
        if not hmac.compare_digest(expected, sig_header):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid X-Hub-Signature-256 webhook signature",
            )

    ref = str(payload.get("ref") or "refs/heads/main")
    pusher = str((payload.get("pusher") or {}).get("name") or "github-webhook")
    pull_note = git_pull_repo(container.settings.intent_repo_path)
    inventory = container.intents.load_inventory()
    sync_res = DeviceService(session).sync_inventory(inventory)

    all_ids = [d.id for d in DeviceService(session).list_devices(limit=5000)]
    job_id: str | None = None
    if all_ids:
        try:
            job = JobService(session, container.dispatcher).create_dry_run(
                all_ids, IntentSource.GIT_MAIN, requested_by=f"github:{pusher}"
            )
            job_id = str(job.id)
        except Exception:
            _ = None

    send_telegram_message(
        container.settings,
        f"<b>[GitHub SoT Webhook]</b> Обновление <code>{ref}</code> от <b>{pusher}</b>\n"
        f"Git pull: <code>{pull_note}</code>\n"
        f"Синхронизация БД: создано {len(sync_res.created)}, обновлено {len(sync_res.updated)}.\n"
        + (f"Запущен префлайт Dry-Run: <code>{job_id[:8]}</code>" if job_id else ""),
    )
    return {
        "status": "accepted",
        "ref": ref,
        "pusher": pusher,
        "git_pull": pull_note,
        "synced_devices": len(sync_res.created) + len(sync_res.updated) + len(sync_res.unchanged),
        "dry_run_job_id": job_id,
    }


@router.get(
    "/system/integrations",
    tags=["system"],
    summary="Сводный статус баз данных, GitHub SoT, NetBox, Prometheus и Telegram-бота",
)
def get_integrations_status(
    session: SessionDep,
    container: ContainerDep,
    user: Viewer,
) -> dict[str, Any]:
    settings = container.settings
    db_url = settings.database_url
    db_engine = "postgresql" if "postgresql" in db_url else "sqlite"

    devices_count = session.scalar(select(func.count(Device.id))) or 0
    jobs_count = session.scalar(select(func.count(Job.id))) or 0
    drift_count = session.scalar(select(func.count(DriftRecord.id))) or 0
    snapshots_count = session.scalar(select(func.count(ConfigSnapshot.id))) or 0

    git_meta = git_repo_metadata(settings.intent_repo_path, settings.github_repo_url)
    issues = container.intents.lint()
    tg_targets = resolve_telegram_targets(settings, discover=False)
    tg_configured = bool(settings.telegram_bot_token and tg_targets)
    git_info = {
        **git_meta,
        "valid": not issues,
        "issues_count": len(issues),
    }

    rt_overlay = settings.apply_runtime_overlay()
    owner_cid = str(rt_overlay.get("telegram_chat_id") or settings.telegram_chat_id or "").strip()
    if rt_overlay.get("telegram_owner_linked") and owner_cid.lstrip("-").isdigit():
        bind_telegram_chat_user(session, int(owner_cid), settings=settings)

    db_users = list(session.scalars(select(User).order_by(User.username)).all())

    bound_count = sum(1 for u in db_users if u.bound_client_id is not None)
    counts = {
        "devices": devices_count,
        "jobs": jobs_count,
        "drift_records": drift_count,
        "config_snapshots": snapshots_count,
        "users": len(db_users),
        "bound_sessions": bound_count,
        "bound_device_sessions": bound_count,
    }

    is_admin = user.role.grants(UserRole.ADMIN)
    registry = sync_access_registry(session, settings) if is_admin else {}
    raw_tokens = load_user_tokens(settings) if is_admin else {}
    if user.role == UserRole.OWNER:
        user_tokens = {
            u.username: raw_tokens.get(u.username) or ensure_user_token(session, u, settings)
            for u in db_users
        }
    elif is_admin:
        if user.allowed_system is not None and user.allowed_system not in ("*", "all"):
            from netops.api.routes.devices import _iter_allowed_tokens

            caller_targets = {target for _, target in _iter_allowed_tokens(user.allowed_system)}
            user_tokens = {
                u.username: raw_tokens.get(u.username) or ensure_user_token(session, u, settings)
                for u in db_users
                if u.username != "owner"
                and u.allowed_system is not None
                and any(t in caller_targets for _, t in _iter_allowed_tokens(u.allowed_system))
            }
        else:
            user_tokens = {
                u.username: raw_tokens.get(u.username) or ensure_user_token(session, u, settings)
                for u in db_users
                if u.username != "owner"
            }
    else:
        user_tokens = {}
    tg_bindings = load_telegram_bindings(settings) if is_admin else {}
    all_users = (
        [UserRead.model_validate(u).model_dump(mode="json") for u in db_users]
        if is_admin
        else []
    )

    return {
        "sessions": all_users,
        "user_tokens": user_tokens,
        "access_registry": registry,
        "security_events": registry.get("security_events", []),
        "database": {
            "engine": db_engine,
            "status": "connected",
            "counts": counts,
            "tables": counts,
        },
        "github_sot": git_info,
        "git_sot": git_info,
        "netbox": {
            "configured": bool(settings.netbox_url and settings.netbox_token),
            "url": settings.netbox_url or None,
            "mode": "NetBox DCIM API"
            if (settings.netbox_url and settings.netbox_token)
            else f"Git SoT + {db_engine.upper()}",
        },
        "prometheus": {
            **check_prometheus_status(settings.prometheus_url),
            "configured": bool(settings.prometheus_url),
            "url": settings.prometheus_url or None,
            "scrape_endpoint": "/metrics",
            "collector_interval_sec": settings.metrics_interval_seconds,
            "stored_samples": len(collector.latest()[0]),
        },
        "telegram": {
            "configured": tg_configured,
            "bot_token_set": bool(settings.telegram_bot_token),
            "bot_username": (
                f"@{settings.telegram_bot_username.lstrip('@')}"
                if settings.telegram_bot_username
                else ""
            ),
            "chat_ids": [int(c) for c in tg_targets if str(c).lstrip("-").isdigit()] if is_admin else [],
            "chat_ids_count": len(tg_targets) if is_admin else 0,
            "bindings": list(tg_bindings.values()) if is_admin else [],
            "commands": [
                "/verify",
                "/start",
                "/overview",
                "/status",
                "/drift",
                "/jobs",
                "/metrics",
                "/users",
                "/token",
                "/ttl",
                "/security",
                "/block",
                "/unblock",
                "/role",
                "/unbind",
                "/recover",
            ],
        },
    }


@router.post(
    "/system/telegram/test",
    tags=["system"],
    summary="Отправить оперативную сводку состояния фабрики в Telegram и вернуть превью",
)
def trigger_telegram_summary(
    session: SessionDep,
    container: ContainerDep,
    _: Operator,
    req: TelegramTestRequest | None = None,
) -> dict[str, Any]:
    devices = list(session.scalars(select(Device).order_by(Device.hostname)).all())
    in_sync = sum(1 for d in devices if d.status is DeviceStatus.IN_SYNC)
    drifted = [d.hostname for d in devices if d.status is DeviceStatus.DRIFT_DETECTED]
    unreachable = [d.hostname for d in devices if d.status is DeviceStatus.UNREACHABLE]
    active_sessions = (
        session.scalar(select(func.count(User.id)).where(User.bound_client_id.is_not(None))) or 0
    )
    git_meta = git_repo_metadata(
        container.settings.intent_repo_path, container.settings.github_repo_url
    )

    summary_html = format_executive_summary(
        total_devices=len(devices),
        in_sync=in_sync,
        drifted=drifted,
        unreachable=unreachable,
        active_sessions=active_sessions,
        git_branch=git_meta["branch"],
        git_sha=git_meta["commit_sha"],
    )
    if req and req.custom_note:
        import html

        summary_html += f"\n\n<b>Комментарий оператора:</b> {html.escape(req.custom_note)}"

    delivered = send_telegram_message(container.settings, summary_html)
    tg_targets = resolve_telegram_targets(container.settings, discover=False)
    configured = bool(container.settings.telegram_bot_token and tg_targets)
    bot_handle = f"@{(container.settings.telegram_bot_username or '').lstrip('@')}"
    return {
        "status": "delivered" if delivered else "preview_only",
        "configured": configured,
        "delivered": delivered,
        "sent": delivered,
        "chat_ids": [int(c) for c in tg_targets if str(c).lstrip("-").isdigit()],
        "preview": summary_html,
        "message_preview": summary_html,
        "hint": None
        if delivered
        else f"Токен {bot_handle} подключён! Напишите /start боту {bot_handle} в Telegram и нажмите кнопку ещё раз для автопривязки чата.",
    }
