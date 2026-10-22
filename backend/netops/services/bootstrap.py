"""Matryoshka cluster initialization and layer configuration facade.

Grouped into dedicated domain modules:
- `bootstrap_exposure`: dynamic internet/public URL resolution, LLM key redaction, SSH reverse tunnels (role ports from config/constants.yaml)
- `bootstrap_owner`: Owner claim, recovery, and role-locked port protection
- `bootstrap_discovery`: autonomous SSH / Containerlab and offline server discovery
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from netops.enums import DeviceStatus
from netops.models import Device, User
from netops.services.bootstrap_discovery import _probe_tcp_node, discover_server
from netops.services.bootstrap_exposure import (
    build_exposure_payload,
    redact_cascade_for_caller,
)
from netops.services.bootstrap_owner import (
    _reject_non_owner_bearer,
    claim_owner,
    recover_owner,
)
from netops.services.devices import DeviceService
from netops.services.git_sot import git_connect_remote, git_repo_metadata
from netops.services.llm import normalize_llm_cascade_item
from netops.services.notifier import load_user_tokens, resolve_telegram_targets
from netops.services.telemetry import check_prometheus_status

__all__ = [
    "_probe_tcp_node",
    "_reject_non_owner_bearer",
    "build_bootstrap_status",
    "claim_owner",
    "configure_layers",
    "discover_server",
    "recover_owner",
]


def build_bootstrap_status(request: Request, session: Session, container: Any) -> dict[str, Any]:
    from netops.services.sessions import session_is_owner

    settings = container.settings
    rt = settings.apply_runtime_overlay()

    owner = session.scalar(select(User).where(User.username == "owner"))
    owner_bound_cid = owner.bound_client_id if owner else None
    bootstrap_done = bool(rt.get("bootstrap_completed"))
    owner_claimed = bool(owner and owner_bound_cid and bootstrap_done)
    caller_is_bound_owner = session_is_owner(request, session, settings)

    git_meta = git_repo_metadata(settings.intent_repo_path, settings.github_repo_url)
    devices_dir = settings.intent_repo_path / "devices"
    yaml_count = len(list(devices_dir.glob("*.yaml"))) if devices_dir.is_dir() else 0

    tg_targets = resolve_telegram_targets(settings, discover=False)
    bot_username = str(
        rt.get("telegram_bot_username")
        or getattr(settings, "telegram_bot_username", "")
        or ""
    ).lstrip("@")
    user_tokens = load_user_tokens(settings)
    owner_tok = user_tokens.get("owner") if caller_is_bound_owner else None
    owner_tok_for_link = owner_tok or "claim_owner"

    devices = list(session.scalars(select(Device).order_by(Device.hostname)).all())
    in_sync = sum(1 for d in devices if d.status is DeviceStatus.IN_SYNC)
    has_populated_db = len(devices) > 0

    c_host = settings.clab_host or ""
    owner_tg_linked = bool(owner and owner.telegram_chat_id)
    exposure = build_exposure_payload(request, settings, rt)

    raw_cascade = [
        normalize_llm_cascade_item(c, idx)
        for idx, c in enumerate(getattr(settings, "llm_cascade", None) or [])
        if isinstance(c, dict)
    ]

    return {
        "initialized": owner_claimed,
        "bootstrap_completed": bootstrap_done,
        "caller_is_bound_owner": caller_is_bound_owner,
        "has_populated_db": has_populated_db,
        "can_transfer_owner": bool(owner_claimed and has_populated_db),
        "cluster_name": str(rt.get("cluster_name") or ""),
        "exposure": exposure,
        "owner": {
            "exists": owner is not None,
            "username": owner.username if owner else "owner",
            "bound_client_id": "bound" if owner_bound_cid else None,
            "bound_ip": owner.bound_ip if (owner and caller_is_bound_owner) else None,
            "recovery_key": owner_tok,
            "telegram_linked": owner_tg_linked,
            "telegram_username": (owner.telegram_username if owner else None)
            or (rt.get("telegram_verified_username") if owner_tg_linked else None),
            "telegram_chat_id": (
                owner.telegram_chat_id if (owner_tg_linked and caller_is_bound_owner) else None
            ),
        },
        "github": {
            **git_meta,
            "repo_connected": bool(rt.get("github_repo_connected", False)),
            "auto_commit_on_diff": bool(rt.get("auto_commit_on_diff", True)),
            "yaml_devices_count": yaml_count,
            "webhook_endpoint": "/api/v1/git/webhook",
            "webhook_registered": bool(rt.get("github_webhook_registered", False)),
            "sync_mode": "SSH Git SoT (auto-commit & push on diff)",
        },
        "telegram": {
            "bot_token_set": bool(settings.telegram_bot_token),
            "configured": bool(settings.telegram_bot_token and owner_tg_linked),
            "bot_username": f"@{bot_username}" if bot_username else "",
            "chat_ids": (
                [int(c) for c in tg_targets if str(c).lstrip("-").isdigit()]
                if caller_is_bound_owner
                else []
            ),
            "deep_link_claim": f"https://t.me/{bot_username}?start={owner_tok_for_link}"
            if bot_username
            else "",
            "deep_link_recover": f"https://t.me/{bot_username}?start=recover_owner"
            if bot_username
            else "",
        },
        "server": {
            "network_driver": settings.network_driver,
            "clab_host": c_host,
            "clab_port": settings.clab_port or 221,
            "clab_user": settings.clab_user or "",
            "clab_topology_path": settings.clab_topology_path,
            "devices_count": len(devices),
            "in_sync_count": in_sync,
            "devices": [
                {
                    "hostname": d.hostname,
                    "platform": d.platform.value,
                    "role": d.role.value,
                    "ip": f"{d.management_ip}:{d.management_port}",
                    "status": d.status.value,
                }
                for d in devices
            ],
        },
        "ai": {
            "groq_configured": bool(settings.groq_api_key),
            "gemini_configured": bool(settings.gemini_api_key),
            "ollama_url": settings.ollama_url or "",
            "cascade": redact_cascade_for_caller(
                raw_cascade, caller_is_bound_owner=caller_is_bound_owner
            ),
        },
        "metrics_config": {
            "metrics_interval_seconds": settings.metrics_interval_seconds,
            "drift_scan_interval_seconds": settings.drift_scan_interval_seconds,
            "commit_confirm_timeout_seconds": settings.commit_confirm_timeout_seconds,
            "max_ping_loss_percent": settings.max_ping_loss_percent,
        },
        "prometheus": (
            check_prometheus_status(settings.prometheus_url)
            if not os.environ.get("PYTEST_CURRENT_TEST")
            else {
                "configured": bool(settings.prometheus_url),
                "url": settings.prometheus_url or "",
                "reachable": False,
                "active_targets": 0,
                "up_targets": 0,
                "jobs": [],
            }
        ),
    }


def configure_layers(req: Any, container: Any, session: Session | None = None) -> dict[str, Any]:
    settings = container.settings
    rt = settings.apply_runtime_overlay()
    updates: dict[str, object] = {
        "auto_commit_on_diff": bool(rt.get("auto_commit_on_diff", True))
    }
    if req.cluster_name:
        updates["cluster_name"] = req.cluster_name.strip()
    if req.exposure_mode in ("local_only", "public_tunnel"):
        updates["exposure_mode"] = req.exposure_mode
    if getattr(req, "public_web_url", None) is not None:
        clean_pub = str(req.public_web_url).strip()
        updates["public_web_url"] = clean_pub
        settings.public_web_url = clean_pub
    for opt_str in (
        "telegram_bot_username",
        "default_fabric_id",
        "default_domain",
        "clab_topology_path",
    ):
        val = getattr(req, opt_str, None)
        if val is not None and str(val).strip():
            updates[opt_str] = str(val).strip()
    if getattr(req, "frontend_port", None) is not None:
        updates["frontend_port"] = int(req.frontend_port)
    raw_pat = getattr(req, "github_pat", None) or getattr(req, "pat", None)
    effective_pat = (
        str(raw_pat).strip()
        if raw_pat and str(raw_pat).strip()
        else str(rt.get("github_pat") or "").strip()
    )
    if raw_pat and str(raw_pat).strip():
        updates["github_pat"] = str(raw_pat).strip()
    git_connect_info: dict[str, Any] | None = None
    raw_repo = getattr(req, "github_repo_url", None)
    if raw_repo is None:
        raw_repo = getattr(req, "repo_url", None)
    if raw_repo and str(raw_repo).strip():
        clean_repo_url = str(raw_repo).strip()
        target_branch = (
            getattr(req, "github_branch", None) or getattr(req, "branch", None) or "main"
        ).strip()
        git_connect_info = git_connect_remote(
            settings.intent_repo_path,
            clean_repo_url,
            target_branch,
            github_pat=effective_pat or None,
        )
        updates["github_repo_url"] = git_connect_info.get("remote_url") or clean_repo_url
        updates["github_branch"] = git_connect_info.get("branch") or target_branch
        updates["github_repo_connected"] = bool(git_connect_info.get("connected", True))
    elif raw_repo is not None:
        updates["github_repo_url"] = ""
        updates["github_repo_connected"] = False
        settings.github_repo_url = ""
    if getattr(req, "auto_commit_on_diff", None) is not None:
        updates["auto_commit_on_diff"] = bool(req.auto_commit_on_diff)
    if req.github_webhook_secret is not None:
        updates["github_webhook_secret"] = req.github_webhook_secret.strip()
    if req.telegram_bot_token:
        clean_tok = req.telegram_bot_token.strip()
        updates["telegram_bot_token"] = clean_tok
        if ":" in clean_tok and not os.environ.get("PYTEST_CURRENT_TEST"):
            try:
                with urllib.request.urlopen(
                    f"https://api.telegram.org/bot{clean_tok}/getMe", timeout=3.0
                ) as resp:
                    body = json.loads(resp.read())
                    uname = str((body.get("result") or {}).get("username") or "").strip().lstrip("@")
                    if body.get("ok") and uname:
                        updates["telegram_bot_username"] = uname
            except Exception:
                _ = None
    if req.telegram_chat_id:
        updates["telegram_chat_id"] = req.telegram_chat_id.strip()
    if req.groq_api_key:
        updates["groq_api_key"] = req.groq_api_key.strip()
    if req.gemini_api_key:
        updates["gemini_api_key"] = req.gemini_api_key.strip()
    if req.ollama_url is not None:
        updates["ollama_url"] = req.ollama_url.strip()
    if getattr(req, "prometheus_url", None) is not None:
        updates["prometheus_url"] = req.prometheus_url.strip()
    if getattr(req, "llm_cascade", None) is not None:
        normalized_cascade = [
            normalize_llm_cascade_item(item, idx)
            for idx, item in enumerate(req.llm_cascade)
            if isinstance(item, dict)
        ]
        updates["llm_cascade"] = normalized_cascade
        for item in normalized_cascade:
            if item.get("api_key"):
                burl = str(item.get("base_url") or "").lower()
                if "groq.com" in burl and not req.groq_api_key:
                    updates["groq_api_key"] = str(item["api_key"]).strip()
                elif "googleapis.com" in burl and not req.gemini_api_key:
                    updates["gemini_api_key"] = str(item["api_key"]).strip()
    for metric_field in (
        "metrics_interval_seconds",
        "drift_scan_interval_seconds",
        "commit_confirm_timeout_seconds",
        "max_ping_loss_percent",
    ):
        val = getattr(req, metric_field, None)
        if val is not None:
            updates[metric_field] = val
    if req.bootstrap_completed is not None:
        updates["bootstrap_completed"] = req.bootstrap_completed

    webhook_status = "ssh_connected" if (git_connect_info and git_connect_info.get("connected")) else "ready"
    bot_username = str(
        updates.get("telegram_bot_username")
        or rt.get("telegram_bot_username")
        or getattr(settings, "telegram_bot_username", "")
        or ""
    ).lstrip("@")
    bot_verified = bool(settings.telegram_bot_token or req.telegram_bot_token)
    owner_tok_for_link = load_user_tokens(settings).get("owner") or "claim_owner"

    saved = settings.save_runtime_overlay(updates)
    if (
        git_connect_info
        and git_connect_info.get("connected")
        and session is not None
        and bool(saved.get("bootstrap_completed"))
        and session.scalar(select(Device.id).limit(1)) is not None
    ):
        try:
            if hasattr(container.intents, "_inventory_file"):
                container.intents._inventory_file = settings.inventory_file
            inventory = container.intents.load_inventory()
            DeviceService(session).sync_inventory(inventory)
        except Exception:
            _ = None

    git_meta = git_repo_metadata(settings.intent_repo_path, settings.github_repo_url)
    if git_connect_info:
        git_meta.update(git_connect_info)
    git_meta["repo_connected"] = bool(saved.get("github_repo_connected", False))
    git_meta["auto_commit_on_diff"] = bool(saved.get("auto_commit_on_diff", True))

    exposure = build_exposure_payload(None, settings, saved)

    return {
        "status": "ok",
        "exposure": exposure,
        "websocket_enabled": False,
        "webhook_status": webhook_status,
        "bot_verified": bot_verified,
        "bot_username": f"@{bot_username}" if bot_username else "",
        "deep_link_claim": f"https://t.me/{bot_username}?start={owner_tok_for_link}" if bot_username else "",
        "git": git_meta,
        "runtime_keys": list(saved.keys()),
    }
