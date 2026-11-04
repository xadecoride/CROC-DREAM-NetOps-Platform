"""Редактирование декларативной модели устройства (intent/devices/<hostname>.yaml) из UI.

Git SoT остаётся единственным источником истины: правится именно intent-YAML, а целевая
конфигурация (intended) затем рендерится из него шаблонами при dry-run. Файл проходит
pydantic-валидацию и lint фабрики; при ошибках изменение откатывается.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from netops.api.deps import Admin, ContainerDep, DeviceServiceDep, Viewer
from netops.api.routes.devices import assert_device_allowed
from netops.intent.lint import IntentValidationError, issues_from_validation_error
from netops.intent.models import DeviceIntent

router = APIRouter(tags=["intent"])


class IntentSource(BaseModel):
    hostname: str
    exists: bool
    yaml: str
    editable: bool = True


class IntentSourceUpdate(BaseModel):
    yaml: str = Field(min_length=1, max_length=200_000)


class IntentIssueOut(BaseModel):
    source: str
    field: str
    message: str


class IntentSaveResult(BaseModel):
    saved: bool
    hostname: str
    issues: list[IntentIssueOut] = []


def _device_path(root: Path, hostname: str) -> Path:
    directory = (root / "devices").resolve()
    path = (directory / f"{hostname}.yaml").resolve()
    if path.parent != directory:  # защита от ../ в hostname
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Некорректное имя устройства")
    return path


def _templates_exist(container: ContainerDep, platform: str) -> bool:
    return (Path(container.settings.templates_path) / platform).is_dir()


@router.get("/devices/{device_id}/intent-source", response_model=IntentSource)
def get_intent_source(
    device_id: int, service: DeviceServiceDep, container: ContainerDep, principal: Viewer
) -> IntentSource:
    device = service.get(device_id)
    assert_device_allowed(device, principal.allowed_system)
    path = _device_path(container.settings.intent_repo_path, device.hostname)
    platform = device.platform.value
    editable = _templates_exist(container, platform)
    if path.is_file():
        return IntentSource(
            hostname=device.hostname,
            exists=True,
            yaml=path.read_text(encoding="utf-8"),
            editable=editable,
        )
    return IntentSource(
        hostname=device.hostname,
        exists=False,
        yaml=f'---\nhostname: "{device.hostname}"\ninterfaces: []\n',
        editable=editable,
    )


@router.put("/devices/{device_id}/intent-source", response_model=IntentSaveResult)
def save_intent_source(
    device_id: int,
    data: IntentSourceUpdate,
    service: DeviceServiceDep,
    container: ContainerDep,
    admin: Admin,
) -> IntentSaveResult:
    device = service.get(device_id)
    assert_device_allowed(device, admin.allowed_system)
    if not _templates_exist(container, device.platform.value):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Для платформы {device.platform.value} нет шаблонов генерации — intent не поддерживается",
        )
    try:
        raw = yaml.safe_load(data.yaml)
    except yaml.YAMLError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"YAML: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("hostname") != device.hostname:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"hostname в YAML должен быть {device.hostname!r}",
        )
    def _failed(items: list[Any]) -> IntentSaveResult:
        return IntentSaveResult(
            saved=False,
            hostname=device.hostname,
            issues=[IntentIssueOut(source=i.source, field=i.location, message=i.message) for i in items],
        )

    try:
        DeviceIntent.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError
        issues = issues_from_validation_error(
            exc, source=f"devices/{device.hostname}.yaml", hostname=device.hostname
        )
        return _failed(issues)

    path = _device_path(container.settings.intent_repo_path, device.hostname)
    previous = path.read_text(encoding="utf-8") if path.is_file() else None
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(data.yaml, encoding="utf-8")
    os.replace(tmp, path)

    # Лint всей фабрики: межустройственные ошибки (ASN, адреса, пиры) откатывают правку.
    try:
        issues = [
            i
            for i in container.intents.lint(device.fabric_id)
            if (i.hostname or Path(i.source).stem) == device.hostname
        ]
    except IntentValidationError as exc:
        issues = list(exc.issues)
    if issues:
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(previous, encoding="utf-8")
        return _failed(issues)
    return IntentSaveResult(saved=True, hostname=device.hostname)
