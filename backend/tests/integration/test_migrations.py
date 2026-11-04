from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

import netops.models  # noqa: F401
from netops.db import Base, build_engine

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def alembic_config(tmp_path: Path) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path / 'migrations.db'}")
    config.attributes["configure_logger"] = False
    return config


def test_upgrade_matches_models(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")

    engine = build_engine(alembic_config.get_main_option("sqlalchemy.url") or "")
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []
    engine.dispose()


def test_downgrade_removes_everything(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")

    engine = build_engine(alembic_config.get_main_option("sqlalchemy.url") or "")
    assert inspect(engine).get_table_names() == ["alembic_version"]
    engine.dispose()


def test_upgrade_when_system_state_already_exists(alembic_config: Config) -> None:
    from netops.models.system_state import SystemState

    command.upgrade(alembic_config, "0006")
    engine = build_engine(alembic_config.get_main_option("sqlalchemy.url") or "")
    SystemState.__table__.create(bind=engine, checkfirst=True)
    engine.dispose()

    command.upgrade(alembic_config, "head")
    engine = build_engine(alembic_config.get_main_option("sqlalchemy.url") or "")
    assert "system_state" in inspect(engine).get_table_names()
    engine.dispose()
