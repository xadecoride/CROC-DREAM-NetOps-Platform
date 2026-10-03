from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from netops.api.deps import ContainerDep, SessionDep, Viewer
from netops.db import Base
from netops.schemas.intent import IntentIssueRead, IntentLintReport
from netops.settings import ApiPrincipal

router = APIRouter()


@router.get("/healthz", tags=["system"], summary="Сервис жив")
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", tags=["system"], summary="Готовность: база доступна и миграции применены")
def readiness(session: SessionDep) -> JSONResponse:
    try:
        existing = set(inspect(session.connection()).get_table_names())
    except SQLAlchemyError:
        return _unavailable("unreachable")
    if not set(Base.metadata.tables) <= existing:
        return _unavailable("migrations not applied")
    return JSONResponse(content={"status": "ok", "database": "ok"})


def _unavailable(reason: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "unavailable", "database": reason},
    )


api_router = APIRouter()


@api_router.get(
    "/auth/me", response_model=ApiPrincipal, tags=["auth"], summary="Текущий пользователь"
)
def current_user(user: Viewer) -> ApiPrincipal:
    return user


from pydantic import BaseModel
from netops.network.rendering import JinjaConfigRenderer

class ChaosRequest(BaseModel):
    scenario: str

@api_router.get(
    "/intent/lint",
    response_model=IntentLintReport,
    tags=["intent"],
    summary="Проверка репозитория intent (pre-flight lint)",
)
def lint_intent(container: ContainerDep, _: Viewer) -> IntentLintReport:
    issues = container.intents.lint()
    return IntentLintReport(
        valid=not issues, issues=[IntentIssueRead.model_validate(issue) for issue in issues]
    )


@api_router.post("/system/chaos", tags=["system"], summary="Simulate network incident")
def inject_chaos(req: ChaosRequest, container: ContainerDep, _: Viewer) -> dict[str, str]:
    lab_dir = container.settings.offline_lab_path
    if req.scenario == "acl_drift":
        leaf1_cfg = lab_dir / "leaf-1.croc.lab.cfg"
        if leaf1_cfg.exists():
            content = leaf1_cfg.read_text(encoding="utf-8")
            if "15 permit ip any any" not in content:
                content = content.replace("20 deny ip any any", "15 permit ip any any\n 20 deny ip any any")
                leaf1_cfg.write_text(content, encoding="utf-8")
        return {"status": "ok", "message": "Внедрен несанкционированный ACL в leaf-1.croc.lab (Дрейф создан!)"}
    elif req.scenario == "port_down":
        leaf2_cfg = lab_dir / "leaf-2.croc.lab.cfg"
        if leaf2_cfg.exists():
            content = leaf2_cfg.read_text(encoding="utf-8")
            content = content.replace(
                "interface GigabitEthernet3\n description Uplink to spine-2\n mtu 9000\n ip address 10.0.2.3 255.255.255.254\n no shutdown",
                "interface GigabitEthernet3\n description Uplink to spine-2\n mtu 9000\n ip address 10.0.2.3 255.255.255.254\n shutdown",
            )
            leaf2_cfg.write_text(content, encoding="utf-8")
        return {"status": "ok", "message": "Интерфейс GigabitEthernet3 на leaf-2 переведен в shutdown"}
    elif req.scenario == "reset_lab":
        renderer = JinjaConfigRenderer(container.settings.templates_path)
        snapshot = container.intents.load()
        for dev in snapshot.inventory.devices:
            intent = snapshot.intent_for(dev.hostname)
            cfg = renderer.render(dev, intent)
            (lab_dir / f"{dev.hostname}.cfg").write_text(cfg, encoding="utf-8")
        return {"status": "ok", "message": "Все 4 устройства успешно сброшены к чистому эталону Git SoT"}
    return {"status": "ok", "message": "Неизвестный сценарий"}

