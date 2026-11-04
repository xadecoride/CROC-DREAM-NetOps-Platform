from __future__ import annotations

import shutil
import uuid
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from netops.api import create_app
from netops.db import Base, build_engine, build_session_factory
from netops.enums import JobStatus, UserRole
from netops.intent import IntentRepository
from netops.models import Device
from netops.network import ConfigNormalizer, JinjaConfigRenderer
from netops.pipeline import JobRunner
from netops.services import DeviceService, JobService
from netops.settings import Settings
from netops.toolchain import Toolchain, build_toolchain

Settings.model_config["env_file"] = None
Settings.model_rebuild(force=True)

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATES = FIXTURES / "templates"

TOKENS = {
    UserRole.OWNER: "owner-token",
    UserRole.ADMIN: "admin-token",
    UserRole.OPERATOR: "operator-token",
    UserRole.VIEWER: "viewer-token",
}

# Служебный вывод IOS-XE, который нормализация должна убрать.
CISCO_NOISE_HEADER = """\
Building configuration...

Current configuration : 4242 bytes
!
! Last configuration change at 10:21:07 UTC Mon Sep 28 2026 by admin
!
version 17.9
"""
CISCO_NOISE_FOOTER = """\
crypto pki certificate chain TP-self-signed-4242
 certificate self-signed 01
  30820330 30820218 A0030201 02020101
  quit
!
end
"""


class RecordingDispatcher:
    def __init__(self) -> None:
        self.job_ids: list[uuid.UUID] = []
        self.error: Exception | None = None

    def dispatch(self, job_id: uuid.UUID) -> None:
        if self.error is not None:
            raise self.error
        self.job_ids.append(job_id)


@pytest.fixture(autouse=True)
def _reset_shared_state() -> Iterator[None]:
    from netops.services.ai_guard import _active_injections
    from netops.services.state import reset_state_manager
    from netops.settings import get_settings

    get_settings.cache_clear()
    _active_injections.clear()
    reset_state_manager()
    yield
    get_settings.cache_clear()
    _active_injections.clear()
    reset_state_manager()


def auth(role: UserRole) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[role]}"}


@pytest.fixture
def intent_repo(tmp_path: Path) -> Path:
    dst = Path(shutil.copytree(FIXTURES / "intent-repo", tmp_path / "intent"))
    repo_platforms = Path(__file__).resolve().parents[2] / "intent" / "platforms"
    if repo_platforms.is_dir():
        shutil.copytree(repo_platforms, dst / "platforms", dirs_exist_ok=True)
    return dst


@pytest.fixture
def lab_path(tmp_path: Path, intent_repo: Path) -> Path:
    lab = tmp_path / "lab"
    lab.mkdir()
    snapshot = IntentRepository(intent_repo).load()
    renderer = JinjaConfigRenderer(TEMPLATES)
    for device in snapshot.inventory.devices:
        config = renderer.render(device, snapshot.intents[device.hostname])
        if device.platform == "cisco_iosxe":
            config = CISCO_NOISE_HEADER + config + CISCO_NOISE_FOOTER
        else:
            config = "! Command: show running-config\n! device: lab (cEOS-lab)\n" + config + "end\n"
        (lab / f"{device.hostname}.cfg").write_text(config)
    return lab


@pytest.fixture
def settings(tmp_path: Path, intent_repo: Path, lab_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=f"sqlite:///{tmp_path / 'netops.db'}",
        intent_repo_path=intent_repo,
        templates_path=TEMPLATES,
        offline_lab_path=lab_path,
        api_tokens={
            token: {"username": f"{role.value}-user", "role": role}
            for role, token in TOKENS.items()
        },
        auth_profiles={"lab": {"username": "admin", "password": "admin"}},
        post_check_interval_seconds=0,
    )


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    engine = build_engine(settings.database_url)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return build_session_factory(engine)


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session


@pytest.fixture
def dispatcher() -> RecordingDispatcher:
    return RecordingDispatcher()


@pytest.fixture
def job_service(session: Session, dispatcher: RecordingDispatcher) -> JobService:
    return JobService(session, dispatcher)


@pytest.fixture
def toolchain(settings: Settings) -> Toolchain:
    return build_toolchain(settings)


@pytest.fixture
def devices(session: Session, intent_repo: Path) -> dict[str, Device]:
    service = DeviceService(session)
    service.sync_inventory(IntentRepository(intent_repo).load_inventory())
    return {device.hostname: device for device in service.list_devices()}


RunJob = Callable[..., JobStatus | None]


@pytest.fixture
def run_job(
    session_factory: sessionmaker[Session], session: Session, toolchain: Toolchain
) -> RunJob:
    def run(job_id: uuid.UUID, **overrides: object) -> JobStatus | None:
        chain = replace(toolchain, **overrides) if overrides else toolchain
        status = JobRunner(session_factory, lambda: chain).run(job_id)
        session.expire_all()
        return status

    return run


@pytest.fixture
def client(
    settings: Settings, engine: Engine, dispatcher: RecordingDispatcher
) -> Iterator[TestClient]:
    app = create_app(settings, engine=engine, dispatcher=dispatcher)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def normalizer() -> ConfigNormalizer:
    return ConfigNormalizer()
