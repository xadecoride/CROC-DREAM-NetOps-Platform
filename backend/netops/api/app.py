from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine

from netops import __version__
from netops.api.deps import AppContainer
from netops.api.errors import register_error_handlers
from netops.api.routes import api_v1, system
from netops.db import build_engine, build_session_factory
from netops.intent import IntentRepository
from netops.services import JobDispatcher
from netops.settings import Settings, get_settings
from netops.worker.celery_app import CeleryJobDispatcher, create_celery_app

DESCRIPTION = """
Централизованное управление и мониторинг гетерогенной сетевой инфраструктуры.

Авторизация: заголовок `Authorization: Bearer <token>`. Роли: **viewer** (чтение),
**operator** (dry-run, деплой, скан и устранение дрейфа), **admin** (инвентарь и
пользователи).
"""


def create_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    dispatcher: JobDispatcher | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    engine = engine or build_engine(settings.database_url)
    container = AppContainer(
        settings=settings,
        session_factory=build_session_factory(engine),
        dispatcher=dispatcher or CeleryJobDispatcher(create_celery_app(settings)),
        intents=IntentRepository(settings.intent_repo_path, inventory_file=settings.inventory_file),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        import os

        if not os.environ.get("PYTEST_CURRENT_TEST"):
            try:
                rt = settings.apply_runtime_overlay()
                settings.offline_lab_path.mkdir(parents=True, exist_ok=True)
                if not os.environ.get("PYTEST_CURRENT_TEST") and str(rt.get("exposure_mode") or "public_tunnel") == "public_tunnel":
                    from netops.services.bootstrap_exposure import ensure_public_role_tunnels_async

                    ensure_public_role_tunnels_async(settings)
                if rt.get("bootstrap_completed"):
                    from netops.services import DeviceService
                    from netops.worker.celery_app import (
                        COLLECT_METRICS_TASK,
                        REFRESH_COLLECTOR_TARGETS_TASK,
                    )

                    snapshot = container.intents.load()
                    with container.session_factory() as session:
                        DeviceService(session).sync_inventory(snapshot.inventory)
                    celery_app = create_celery_app(settings)
                    celery_app.send_task(COLLECT_METRICS_TASK)
                    celery_app.send_task(REFRESH_COLLECTOR_TARGETS_TASK)
            except Exception:
                _ = None
        yield
        engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.state.container = container
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type", "X-Client-Id", "X-CSRF-Token"],
    )
    register_error_handlers(app)
    app.include_router(system.router)
    app.include_router(api_v1)
    return app
