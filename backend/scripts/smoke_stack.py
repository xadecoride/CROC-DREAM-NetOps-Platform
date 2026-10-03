from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

# Smoke-тест поднятого docker compose: API, база, очередь, воркер, Beat и сами контейнеры.
# На устройствах ничего не меняет: только dry-run и скан дрейфа. Зависимостей нет,
# нужен Python 3.11+. Запуск из корня репозитория:
#   python3 backend/scripts/smoke_stack.py

FINISHED = frozenset({"SUCCESS", "FAILED"})
DRY_RUN_STEPS = frozenset({"preflight", "render", "collect", "diff"})
SCANNED = frozenset({"IN_SYNC", "DRIFT_DETECTED"})

Check = Callable[[], str]


class SmokeError(Exception):
    pass


class Api:
    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token

    def get(self, path: str) -> Any:
        return self._call("GET", path, None, 200)

    def post(self, path: str, body: object | None = None, *, expect: int = 200) -> Any:
        return self._call("POST", path, body, expect)

    def wait_job(self, job_id: str, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            job: dict[str, Any] = self.get(f"/api/v1/jobs/{job_id}")
            if job["status"] in FINISHED:
                return job
            if time.monotonic() > deadline:
                raise SmokeError(f"задача {job_id} не завершилась за {timeout:g} с")
            time.sleep(1)

    def _call(self, method: str, path: str, body: object | None, expect: int) -> Any:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self._base_url + path, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._token}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        if status != expect:
            text = raw.decode(errors="replace")[:300]
            raise SmokeError(f"{method} {path} ответил {status}: {text}")
        return json.loads(raw) if raw else None


class StackSmoke:
    def __init__(self, api: Api, *, devices: int, timeout: float, beat_timeout: float) -> None:
        self._api = api
        self._devices = devices
        self._timeout = timeout
        self._beat_timeout = beat_timeout
        self._started = datetime.now(UTC)
        self._device_ids: list[int] = []

    def checks(self) -> list[tuple[str, Check]]:
        checks: list[tuple[str, Check]] = [
            ("готовность API", self.ready),
            ("токен", self.auth),
            ("импорт инвентаря", self.inventory),
            ("dry-run через Celery", self.dry_run),
            ("скан дрейфа", self.drift_scan),
        ]
        if self._beat_timeout > 0:
            checks.append(("плановый скан от Celery Beat", self.scheduled_scan))
        return checks

    def ready(self) -> str:
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                self._api.get("/readyz")
            except (SmokeError, OSError) as exc:
                if time.monotonic() > deadline:
                    raise SmokeError(f"API не готов за {self._timeout:g} с: {exc}") from exc
                time.sleep(2)
            else:
                return "база доступна, миграции применены"

    def auth(self) -> str:
        user = self._api.get("/api/v1/auth/me")
        if user["role"] != "admin":
            raise SmokeError(f"нужен токен администратора, а роль {user['role']}")
        return f"{user['username']} ({user['role']})"

    def inventory(self) -> str:
        self._api.post("/api/v1/inventory/sync")
        devices = self._api.get("/api/v1/devices")
        if len(devices) != self._devices:
            raise SmokeError(f"устройств в базе: {len(devices)}, ожидали {self._devices}")
        self._device_ids = [device["id"] for device in devices]
        return ", ".join(sorted(device["hostname"] for device in devices))

    def dry_run(self) -> str:
        accepted = self._api.post(
            "/api/v1/jobs/dry-run", {"device_ids": self._device_ids}, expect=202
        )
        job = self._wait_success(accepted["job_id"])
        if job["progress"] != 100:
            raise SmokeError(f"задача завершилась, а прогресс {job['progress']}%")
        missing = DRY_RUN_STEPS - {line["step"] for line in job["logs"]}
        if missing:
            raise SmokeError(f"в логах задачи нет шагов: {', '.join(sorted(missing))}")
        devices = self._api.get(f"/api/v1/jobs/{job['id']}/diff")["devices"]
        empty = [
            d["hostname"] for d in devices if not (d["running_config"] and d["intended_config"])
        ]
        if empty:
            raise SmokeError(f"в диффе нет конфигов: {', '.join(empty)}")
        changed = sum(1 for device in devices if device["remediation_patch"])
        return f"устройств: {len(devices)}, с изменениями: {changed}"

    def drift_scan(self) -> str:
        accepted = self._api.post("/api/v1/drift/scan", expect=202)
        self._wait_success(accepted["job_id"])
        statuses = Counter(device["status"] for device in self._api.get("/api/v1/devices"))
        if not statuses.keys() <= SCANNED:
            raise SmokeError(f"после скана статусы {dict(statuses)}")
        return ", ".join(f"{status}: {count}" for status, count in sorted(statuses.items()))

    # Beat ставит скан не сразу, а через NETOPS_DRIFT_SCAN_INTERVAL_SECONDS после старта.
    def scheduled_scan(self) -> str:
        deadline = time.monotonic() + self._beat_timeout
        while time.monotonic() < deadline:
            for job in self._api.get("/api/v1/jobs?type=DRIFT_SCAN&limit=100"):
                fresh = datetime.fromisoformat(job["created_at"]) >= self._started
                if job["created_by"] != "scheduler" or not fresh or job["status"] not in FINISHED:
                    continue
                if job["status"] != "SUCCESS":
                    raise SmokeError(f"плановый скан {job['id']} упал: {job['error']}")
                return f"Beat поставил скан, воркер его выполнил ({job['id']})"
            time.sleep(3)
        raise SmokeError(f"за {self._beat_timeout:g} с Beat не поставил ни одного скана")

    def _wait_success(self, job_id: str) -> dict[str, Any]:
        job = self._api.wait_job(job_id, self._timeout)
        if job["status"] != "SUCCESS":
            reasons = "; ".join(
                f"{t['hostname']}: {t['error']}" for t in job["targets"] if t["error"]
            )
            raise SmokeError(f"задача {job_id} {job['status']}: {job['error']}. {reasons}")
        return job


def check_containers() -> list[tuple[str, bool, str]]:
    services = _run("docker", "compose", "config", "--services").split()
    containers = {item["Service"]: item for item in _compose_ps()}
    results = []
    for service in services:
        container = containers.get(service)
        if container is None:
            results.append((service, False, "контейнер не создан"))
            continue
        restarts = int(_run("docker", "inspect", "-f", "{{.RestartCount}}", container["Name"]))
        health = container.get("Health") or ""
        alive = container["State"] == "running" and health in {"", "healthy"} and restarts == 0
        state = ", ".join(filter(None, [container["State"], health]))
        results.append((service, alive, f"{state}, перезапусков: {restarts}"))
    return results


# Старые версии compose отдают JSON-массив, новые — по объекту в строке.
def _compose_ps() -> list[dict[str, Any]]:
    output = _run("docker", "compose", "ps", "--all", "--format", "json").strip()
    if output.startswith("["):
        items: list[dict[str, Any]] = json.loads(output)
        return items
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def _run(*command: str) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout


def _report(title: str, ok: bool, detail: str) -> None:
    print(f"[{'OK' if ok else 'FAIL'}] {title}: {detail}", flush=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-тест стека NetOps в docker compose")
    parser.add_argument("--api", default=f"http://localhost:{os.environ.get('API_PORT', '8000')}")
    parser.add_argument(
        "--token",
        default=os.environ.get("NETOPS_SMOKE_TOKEN", "dev-admin-token"),
        help="токен администратора (по умолчанию из backend/.env.example)",
    )
    parser.add_argument("--devices", type=int, default=4, help="сколько устройств в инвентаре")
    parser.add_argument("--timeout", type=float, default=120, help="ожидание API и задач, с")
    parser.add_argument(
        "--beat-timeout",
        type=float,
        default=0,
        help="сколько ждать плановый скан от Celery Beat, с; 0 — не проверять",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    smoke = StackSmoke(
        Api(args.api, args.token),
        devices=args.devices,
        timeout=args.timeout,
        beat_timeout=args.beat_timeout,
    )
    ok = True
    for title, check in smoke.checks():
        try:
            detail = check()
        except (SmokeError, OSError) as exc:
            _report(title, False, str(exc))
            ok = False
            break
        _report(title, True, detail)

    try:
        containers = check_containers()
    except (OSError, subprocess.CalledProcessError) as exc:
        containers = [("docker compose", False, str(exc))]
    for service, alive, detail in containers:
        _report(f"контейнер {service}", alive, detail)
        ok = ok and alive

    print("\nСтек работает" if ok else "\nЕсть проблемы, подробности выше")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
