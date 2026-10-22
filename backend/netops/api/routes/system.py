from __future__ import annotations

from typing import Annotated, Any

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import SQLAlchemyError

from netops.api.deps import (
    ContainerDep,
    DeviceServiceDep,
    Operator,
    Owner,
    SessionDep,
    Viewer,
)
from netops.api.routes.devices import assert_device_allowed
from netops.api.routes.system_git_integrations import (
    GitCommitDiffRequest,
    GitSyncRequest,
    TelegramTestRequest,
)
from netops.api.routes.system_git_integrations import (
    router as system_git_integrations_router,
)
from netops.db import Base
from netops.enums import UserRole
from netops.models import Device, DriftRecord, Job
from netops.schemas.intent import IntentIssueRead, IntentLintReport
from netops.services.bootstrap import (
    build_bootstrap_status,
    claim_owner,
    configure_layers,
    discover_server,
    recover_owner,
)
from netops.services.chaos import execute_chaos_scenario
from netops.services.freeze import get_freeze_state, set_factory_freeze
from netops.services.git_sot import git_pull_repo, git_repo_metadata
from netops.services.sessions import create_session
from netops.settings import ApiPrincipal
from netops.telemetry import collector, metrics

# Keep aliases for backwards-compatible unit/integration imports if any
_git_repo_metadata = git_repo_metadata
_git_pull_repo = git_pull_repo

__all__ = [
    "BootstrapConfigureRequest",
    "ChaosRequest",
    "ClaimOwnerRequest",
    "FreezeRequest",
    "GitCommitDiffRequest",
    "GitSyncRequest",
    "RecoverOwnerRequest",
    "ServerDiscoverRequest",
    "TelegramTestRequest",
    "_git_pull_repo",
    "_git_repo_metadata",
    "api_router",
    "discover_server",
    "router",
]

router = APIRouter()
api_router = APIRouter()

_verified_dbs: set[str] = set()


def _unavailable(reason: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "unavailable", "database": reason},
    )


@router.get("/healthz", tags=["system"], summary="Сервис жив")
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", tags=["system"], summary="Готовность: база доступна и миграции применены")
def readiness(session: SessionDep) -> JSONResponse:
    try:
        db_key = str(session.get_bind().url)
        if db_key in _verified_dbs:
            session.execute(select(1))
        else:
            existing = set(inspect(session.connection()).get_table_names())
            if not set(Base.metadata.tables) <= existing:
                return _unavailable("migrations not applied")
            _verified_dbs.add(db_key)
    except SQLAlchemyError:
        return _unavailable("unreachable")
    return JSONResponse(content={"status": "ok", "database": "ok"})


def _render_prometheus_metrics(session: SessionDep) -> PlainTextResponse:
    from netops.services.state import get_state_manager

    lines = [
        "# HELP netops_up Whether NetOps Platform API is operational",
        "# TYPE netops_up gauge",
    ]
    try:
        sm = get_state_manager()
        cached_block = sm.get("netops:metrics:db_counts") if sm.is_redis_connected else None
        if cached_block:
            lines.append(cached_block)
        else:
            db_lines: list[str] = [
                "netops_up 1",
                "",
                "# HELP netops_devices_total Total network devices by operational sync status",
                "# TYPE netops_devices_total gauge",
            ]
            dev_counts = session.execute(
                select(Device.status, func.count(Device.id)).group_by(Device.status)
            ).all()
            status_map = {
                (row[0].value if hasattr(row[0], "value") else str(row[0])): row[1]
                for row in dev_counts
            }
            for st in ("IN_SYNC", "DRIFT_DETECTED", "UNREACHABLE", "UNKNOWN"):
                db_lines.append(
                    f'netops_devices_total{{status="{st.lower()}"}} {status_map.get(st, 0)}'
                )

            db_lines.append("")
            db_lines.append(
                "# HELP netops_jobs_total Total executed orchestration jobs by type and status"
            )
            db_lines.append("# TYPE netops_jobs_total counter")
            job_counts = session.execute(
                select(Job.type, Job.status, func.count(Job.id)).group_by(Job.type, Job.status)
            ).all()
            for row in job_counts:
                j_type = row[0].value if hasattr(row[0], "value") else str(row[0])
                j_status = row[1].value if hasattr(row[1], "value") else str(row[1])
                db_lines.append(
                    f'netops_jobs_total{{type="{j_type.lower()}",status="{j_status.lower()}"}} {row[2]}'
                )

            db_lines.append("")
            db_lines.append(
                "# HELP netops_drift_records_total Total configuration drift records recorded"
            )
            db_lines.append("# TYPE netops_drift_records_total counter")
            drift_count = session.scalar(select(func.count(DriftRecord.id))) or 0
            db_lines.append(f"netops_drift_records_total {drift_count}")

            rendered_db = "\n".join(db_lines)
            if sm.is_redis_connected:
                sm.set("netops:metrics:db_counts", rendered_db, ex=10)
            lines.append(rendered_db)

        samples, collected_at = collector.latest()
        filtered_samples = [
            s for s in samples if s.name not in (metrics.DEVICES_TOTAL, metrics.JOBS_TOTAL)
        ]
        if filtered_samples:
            lines.append("")
            lines.append(metrics.render(filtered_samples, collected_at).rstrip())
    except Exception:
        lines.append("netops_up 0")

    lines.append("")
    return PlainTextResponse(
        "\n".join(lines) + "\n", media_type="text/plain; version=0.0.4; charset=utf-8"
    )


@router.get(
    "/metrics",
    tags=["system"],
    summary="Метрики для Prometheus",
    response_class=PlainTextResponse,
)
def prometheus_metrics() -> PlainTextResponse:
    samples, collected_at = collector.latest()
    return PlainTextResponse(
        metrics.render(samples, collected_at),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@api_router.get(
    "/metrics",
    response_class=PlainTextResponse,
    tags=["system"],
    summary="Prometheus exposition metrics",
)
def metrics_api(session: SessionDep) -> PlainTextResponse:
    return _render_prometheus_metrics(session)


class ChaosRequest(BaseModel):
    scenario: str
    target_hostname: str | None = None
    interface_name: str | None = None
    acl_name: str | None = None


class FreezeRequest(BaseModel):
    frozen: bool
    reason: str | None = None
    fabric_id: str | None = None
    site: str | None = None


class ClaimOwnerRequest(BaseModel):
    cluster_name: str = ""
    owner_username: str = "owner"
    recovery_key: str | None = None


class RecoverOwnerRequest(BaseModel):
    action: str = "verify"
    recovery_key: str | None = None
    telegram_code: str | None = None


class BootstrapConfigureRequest(BaseModel):
    cluster_name: str | None = None
    exposure_mode: str | None = None
    websocket_enabled: bool | None = None
    github_repo_url: str | None = None
    github_branch: str | None = None
    repo_url: str | None = None
    branch: str | None = None
    pat: str | None = None
    auto_commit_on_diff: bool | None = None
    github_pat: str | None = None
    github_webhook_secret: str | None = None
    webhook_public_url: str | None = None
    public_web_url: str | None = None
    frontend_port: int | None = None
    telegram_bot_token: str | None = None
    telegram_bot_username: str | None = None
    telegram_chat_id: str | None = None
    default_fabric_id: str | None = None
    default_domain: str | None = None
    clab_topology_path: str | None = None
    groq_api_key: str | None = None
    gemini_api_key: str | None = None
    ollama_url: str | None = None
    prometheus_url: str | None = None
    llm_cascade: list[dict[str, Any]] | None = None
    metrics_interval_seconds: int | None = None
    drift_scan_interval_seconds: int | None = None
    commit_confirm_timeout_seconds: int | None = None
    max_ping_loss_percent: float | None = None
    bootstrap_completed: bool | None = None


class ServerDiscoverRequest(BaseModel):
    mode: str = "ssh"
    host: str = ""
    port: int = 221
    username: str = ""
    password: str | None = None
    switch_password_huawei: str = ""
    switch_password_arista: str = ""
    switch_password_cisco: str = ""
    fabric_id: str | None = None


@api_router.get(
    "/auth/me", response_model=ApiPrincipal, tags=["auth"], summary="Текущий пользователь"
)
def current_user(user: Viewer) -> ApiPrincipal:
    return user


@api_router.get(
    "/intent/lint",
    response_model=IntentLintReport,
    tags=["intent"],
    summary="Проверка репозитория intent (pre-flight lint)",
)
def lint_intent(
    container: ContainerDep,
    _: Viewer,
    fabric_id: Annotated[str | None, Query(description="Фильтр по стенду/фабрике")] = None,
    site: Annotated[str | None, Query(description="Фильтр по площадке/стенду")] = None,
) -> IntentLintReport:
    stand = fabric_id or site
    issues = container.intents.lint(fabric_id=stand)
    return IntentLintReport(
        valid=not issues, issues=[IntentIssueRead.model_validate(issue) for issue in issues]
    )


def _enforce_stand_scope(user: ApiPrincipal, stand: str | None) -> str | None:
    allowed_sys = getattr(user, "allowed_system", None)
    if user.role == UserRole.OWNER or not allowed_sys or allowed_sys in ("*", "all"):
        return stand
    tokens = [
        t.strip().lower().removeprefix("fabric:").removeprefix("site:")
        for t in allowed_sys.split(",")
        if t.strip()
    ]
    if not tokens or "*" in tokens or "all" in tokens:
        return stand
    if stand:
        if stand.strip().lower() not in tokens:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Нет прав на управление стендом '{stand}'",
            )
        return stand
    if len(tokens) == 1:
        return tokens[0]
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Необходимо указать разрешённый стенд (fabric_id/site)",
    )


@api_router.post("/system/chaos", tags=["system"], summary="Simulate network incident")
def inject_chaos(
    req: ChaosRequest,
    container: ContainerDep,
    service: DeviceServiceDep,
    user: Operator,
) -> dict[str, str]:
    allowed_sys = getattr(user, "allowed_system", None)
    if req.target_hostname:
        dev = service.get_by_hostname(req.target_hostname)
        if dev is not None:
            assert_device_allowed(dev, allowed_sys)
    elif user.role != UserRole.OWNER and allowed_sys and allowed_sys not in ("*", "all"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Операторам с ограниченным контуром необходимо указать конкретный target_hostname",
        )
    return execute_chaos_scenario(
        req.scenario,
        container,
        target_hostname=req.target_hostname,
        interface_name=req.interface_name,
        acl_name=req.acl_name,
    )


@api_router.get(
    "/system/freeze",
    tags=["system"],
    summary="Статус экстренной заморозки фабрики (Kill Switch)",
)
@api_router.get("/emergency/freeze", tags=["emergency"], include_in_schema=False)
def get_freeze_status(
    _: Viewer,
    fabric_id: Annotated[str | None, Query(description="Фильтр по стенду/фабрике")] = None,
    site: Annotated[str | None, Query(description="Фильтр по площадке/стенду")] = None,
) -> dict[str, Any]:
    stand = fabric_id or site
    return get_freeze_state(fabric_id=stand)


@api_router.post(
    "/system/freeze",
    tags=["system"],
    summary="Включение / отключение экстренной заморозки фабрики",
)
@api_router.post("/emergency/freeze", tags=["emergency"], include_in_schema=False)
def toggle_freeze(req: FreezeRequest, user: Operator) -> dict[str, Any]:
    stand = _enforce_stand_scope(user, req.fabric_id or req.site)
    return set_factory_freeze(req.frozen, req.reason, user.username, fabric_id=stand)


@api_router.delete(
    "/system/freeze",
    tags=["system"],
    summary="Снятие экстренной заморозки фабрики",
)
@api_router.delete("/emergency/freeze", tags=["emergency"], include_in_schema=False)
def unfreeze(
    user: Operator,
    fabric_id: Annotated[str | None, Query(description="Идентификатор стенда/фабрики")] = None,
    site: Annotated[str | None, Query(description="Идентификатор площадки/стенду")] = None,
) -> dict[str, Any]:
    stand = _enforce_stand_scope(user, fabric_id or site)
    return set_factory_freeze(False, "Заморозка снята оператором", user.username, fabric_id=stand)


@api_router.get(
    "/system/bootstrap/status",
    tags=["system"],
    summary="Матрёшка: публичный статус первичной инициализации кластера и всех 4 слоёв",
)
@api_router.get("/system/settings", tags=["system"], include_in_schema=False)
def get_bootstrap_status(
    request: Request,
    session: SessionDep,
    container: ContainerDep,
) -> dict[str, Any]:
    return build_bootstrap_status(request, session, container)


def _open_owner_session(
    result: dict[str, Any], request: Request, response: Response, container: ContainerDep
) -> dict[str, Any]:
    token = result.get("token")
    if token:
        result["csrf_token"] = create_session(
            response,
            request,
            ApiPrincipal(username="owner", role=UserRole.OWNER),
            token=token,
            settings=container.settings,
            db_user=True,
        )
    return result


@api_router.post(
    "/system/bootstrap/claim-owner",
    tags=["system"],
    summary="Шаг 1 Матрёшки: выдать себе права Владельца (Owner) и привязать текущий ПК",
)
def claim_owner_bootstrap(
    req: ClaimOwnerRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    container: ContainerDep,
) -> dict[str, Any]:
    result = claim_owner(req, request, session, container)
    return _open_owner_session(result, request, response, container)


@api_router.post(
    "/system/bootstrap/recover-owner",
    tags=["system"],
    summary="Перенос прав Владельца на новый ПК через Telegram-код или Master Recovery Key",
)
def recover_owner_access(
    req: RecoverOwnerRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    container: ContainerDep,
) -> dict[str, Any]:
    result = recover_owner(req, request, session, container)
    return _open_owner_session(result, request, response, container)


@api_router.post(
    "/system/bootstrap/configure",
    tags=["system"],
    summary="Шаг 2 и 3 Матрёшки: сохранить и проверить GitHub SoT, Telegram-бота и ИИ-ключи в общем томе Docker",
)
@api_router.post("/system/bootstrap/apply", tags=["system"], include_in_schema=False)
@api_router.post("/system/settings", tags=["system"], include_in_schema=False)
@api_router.post("/system/git/connect", tags=["system"], include_in_schema=False)
def configure_bootstrap_layers(
    req: BootstrapConfigureRequest,
    container: ContainerDep,
    session: SessionDep,
    _: Owner,
) -> dict[str, Any]:
    return configure_layers(req, container, session)


@api_router.post(
    "/system/bootstrap/discover-server",
    tags=["system"],
    summary="Шаг 4 Матрёшки: автономная разведка и распаковка опорного сервера по SSH (контейнеры, свитчи, БД)",
)
@api_router.post("/system/bootstrap/discover", tags=["system"], include_in_schema=False)
def discover_and_attach_server(
    req: ServerDiscoverRequest,
    session: SessionDep,
    container: ContainerDep,
    _: Owner,
) -> dict[str, Any]:
    if req.mode == "ssh" and not req.host.strip():
        rt = container.settings.apply_runtime_overlay()
        host_fallback = str(rt.get("clab_host") or container.settings.clab_host or "").strip()
        if host_fallback:
            req.host = host_fallback
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Укажите IP/хост опорного сервера для режима SSH",
            )
    return discover_server(req, session, container)


api_router.include_router(system_git_integrations_router)
