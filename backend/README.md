# NetOps Platform — бэкенд

Серверная часть платформы CROC DREAM (зона ответственности Лёши, раздел 3.4 ТЗ): REST API на
FastAPI, PostgreSQL + Alembic, фоновые задачи Celery/Redis, конечный автомат задач, пайплайн
dry-run → deploy с откатом и движок дрейфа по расписанию Celery Beat.

## Что готово

| Требование ТЗ | Где в коде |
|---|---|
| Таблицы PostgreSQL (устройства, задачи, снимки конфигураций, история дрейфа) и миграции Alembic | `netops/models/`, `migrations/versions/` |
| REST API из раздела 2.3 + CRUD устройств, история задач, импорт инвентаря | `netops/api/routes/` |
| Валидация intent через Pydantic (IP и маски, пересечения подсетей, ASN 1..4294967295, уникальность Router ID и IP по фабрике) со структурированными ошибками | `netops/intent/` |
| Celery + Redis: деплой и опрос идут в воркере, API не блокируется | `netops/worker/` |
| Конечный автомат `PENDING → RUNNING → SUCCESS / FAILED` с логом каждого шага | `netops/models/job.py`, `netops/pipeline/recorder.py` |
| Пайплайн: pre-flight lint → dry-run → ручное подтверждение → pre-check → apply (commit confirmed) → post-check → confirm/rollback | `netops/pipeline/` |
| Drift Engine: Celery Beat каждые 15 минут, классификация `IN_SYNC / DRIFT_DETECTED / UNREACHABLE`, устранение дрейфа | `netops/pipeline/pipelines.py`, `netops/services/jobs.py` |
| Нормализация конфигов (баннеры, счётчики байт, сертификаты, отступы) | `netops/network/normalization.py` |
| Post-check по правилам Health-Check: заявленные BGP-соседи в Established с префиксами, порты up/up, потери ping ≤ 20 %, ожидание сходимости BGP | `netops/network/health.py`, `netops/pipeline/deployment.py` |
| RBAC: viewer / operator / admin, управление пользователями и токенами | `netops/api/deps.py`, `netops/services/users.py` |
| Общий docker-compose: бэкенд, PostgreSQL, Redis, миграции, воркер, Beat | `../docker-compose.yml` |
| CI/CD: линтеры и тесты, сборка образа, smoke-тест всего стека, публикация образа в GHCR | `../.github/workflows/ci-cd.yml`, `scripts/smoke_stack.py` |
| OpenAPI/Swagger | `/docs`, `/openapi.json` |

## Быстрый старт

Нужны Python 3.11+ и [uv](https://docs.astral.sh/uv/).

```bash
cd backend
uv sync                      # зависимости + dev-инструменты
cp .env.example .env         # поправить пути и токены
uv run alembic upgrade head  # схема БД
uv run uvicorn netops.main:app --reload
```

Воркер и планировщик (нужен Redis):

```bash
uv run celery -A netops.worker.tasks worker --loglevel=INFO
uv run celery -A netops.worker.tasks beat --loglevel=INFO
```

Всю платформу поднимает `docker-compose.yml` в корне репозитория (из корня):

```bash
docker compose up --build
```

При старте API применяет миграции (`alembic upgrade head`) и запускает сервер; воркер и Beat
стартуют, когда API стал `healthy`. Настройки бэкенда берутся из `backend/.env`. Порты и каталоги с intent, шаблонами и
офлайн-стендом можно переопределить переменными `API_PORT`, `POSTGRES_PORT`, `INTENT_REPO`,
`TEMPLATES`, `OFFLINE_LAB` и т. д. (см. шапку `docker-compose.yml`). Фронтенд и мониторинг
добавятся туда же позже.

Swagger: <http://localhost:8000/docs>. Авторизация в API — заголовок `Authorization: Bearer <token>`.

### Если в базе нет таблиц

Миграции применяет контейнер `api` при старте. Пока схемы нет, `/readyz` отвечает 503
`migrations not applied`, API не становится `healthy`, и воркер с Beat не запускаются.

```bash
docker compose ps                                  # api должен быть healthy
docker compose logs api | grep -i alembic          # что сделали миграции, текст ошибки
docker compose exec api alembic upgrade head       # применить миграции ещё раз
docker compose exec postgres psql -U netops -d netops -c '\dt'
```

Если стек поднимали до этого изменения, в нём остался старый контейнер `migrate` в статусе
`Exited`; его убирает `docker compose up -d --build --remove-orphans`.

Postgres считается готовым только когда принимает TCP-соединения к базе `netops`, а миграции
дополнительно ждут базу до минуты: при первом старте контейнер Postgres сначала инициализируется
и какое-то время слушает только unix-сокет.

## Проверки

```bash
uv run pytest --cov=netops   # 289 тестов, SQLite, без Redis и сети
uv run ruff check .
uv run black --check .
uv run mypy                  # strict
```

Тест `tests/integration/test_migrations.py` прогоняет миграции и сверяет результат с моделями —
если поменяли модель и забыли миграцию, он упадёт. Новая миграция:
`uv run alembic revision --autogenerate -m "..."`, затем проверить файл руками.

Тесты `tests/integration/test_repo_data.py` проверяют данные стенда в корне репозитория: intent
проходит линт, шаблоны из `templates/` рендерятся для каждого устройства. Пока
`intent/inventory.yaml` нет, они пропускаются.

**Smoke-тест стека.** Когда стек поднят (`docker compose up -d --wait`), из корня репозитория:

```bash
python3 backend/scripts/smoke_stack.py
```

Скрипт проверяет, что миграции применены, токен администратора работает, dry-run и скан дрейфа
на всех устройствах проходят через Redis и воркер, а все контейнеры compose `running` /
`healthy` и ни разу не перезапускались. На устройствах ничего не меняет. С `--beat-timeout 120`
дополнительно дождётся планового скана от Celery Beat — для этого
`NETOPS_DRIFT_SCAN_INTERVAL_SECONDS` нужно уменьшить, например до 60.

**CI/CD** (`.github/workflows/ci-cd.yml`) запускается на каждый PR:

- линтеры и тесты: ruff, black, mypy, pytest; покрытие — в сводке запуска;
- сборка Docker-образа и весь стек в docker compose со smoke-тестом: стенд на файлах из
  фикстур (4 устройства), интервал скана 60 с;
- после мержа в `main` образ публикуется в `ghcr.io/xadecoride/netops-backend`
  (теги `latest` и `sha-<коммит>`).

## Конфигурация

Все переменные с префиксом `NETOPS_`, полный список — `netops/settings.py` и `.env.example`.

| Переменная | Назначение |
|---|---|
| `DATABASE_URL` | PostgreSQL, `postgresql+psycopg://...` |
| `REDIS_URL` | брокер Celery |
| `INTENT_REPO_PATH` | checkout Git-репозитория с intent |
| `TEMPLATES_PATH` | шаблоны Jinja2 (`<platform>/{base,interfaces,acls,bgp}.j2`) |
| `NORMALIZATION_RULES_PATH` | необязательный YAML с дополнительными правилами нормализации |
| `NETWORK_DRIVER` | пока только `offline` (см. ниже) |
| `COMMIT_CONFIRM_TIMEOUT_SECONDS` | таймер `commit confirmed`, по умолчанию 180 |
| `POST_CHECK_ATTEMPTS`, `POST_CHECK_INTERVAL_SECONDS` | повторы post-check, пока сходится BGP: по умолчанию 6 × 10 с (не больше половины таймера) |
| `MAX_PING_LOSS_PERCENT` | допустимые потери ping, по умолчанию 20 |
| `DRIFT_SCAN_INTERVAL_SECONDS` | период фонового скана, по умолчанию 900 (15 минут) |
| `API_TOKENS` | начальные токены `{"<token>": {"username": ..., "role": "viewer\|operator\|admin"}}`: нужны хотя бы для первого администратора, остальных пользователей он заводит через API |
| `AUTH_PROFILES` | JSON `{"lab": {"username": ..., "password": ...}}`: SSH-учётки для `Device.auth_profile` |
| `CORS_ORIGINS` | адреса фронтенда, которым разрешены запросы из браузера; по умолчанию Vite `http://localhost:5173` |

## API

| Метод | URL | Роль | Что делает |
|---|---|---|---|
| GET | `/api/v1/devices?status=&role=&platform=` | viewer | список устройств |
| GET | `/api/v1/devices/{id}` | viewer | карточка устройства + параметры из intent |
| POST/PATCH/DELETE | `/api/v1/devices[/{id}]` | admin | управление инвентарём |
| POST | `/api/v1/inventory/sync` | admin | импорт `inventory.yaml` из Git |
| GET | `/api/v1/intent/lint` | viewer | pre-flight lint репозитория intent |
| POST | `/api/v1/jobs/dry-run` | operator | `{"device_ids": [1, 2], "intent_source": "git_main"}` → `job_id` |
| GET | `/api/v1/jobs?type=&status=` | viewer | история задач |
| GET | `/api/v1/jobs/{id}` | viewer | статус, прогресс (%), устройства, логи шагов |
| GET | `/api/v1/jobs/{id}/logs?after_id=` | viewer | только новые строки лога — для живого экрана деплоя |
| GET | `/api/v1/jobs/{id}/diff` | viewer | `running_config`, `intended_config`, `remediation_patch`, `rollback_patch` по каждому устройству |
| POST | `/api/v1/jobs/deploy` | operator | `{"job_id": "<dry-run>", "confirmed_by": "..."}` → `job_id` деплоя |
| POST | `/api/v1/drift/scan` | operator | внеочередной скан (`{"device_ids": [...]}` или все) |
| GET | `/api/v1/drift/report?since=&until=&status=` | viewer | последняя проверка каждого устройства |
| POST | `/api/v1/drift/remediate` | operator | `{"device_id": 1}` → `job_id` |
| GET | `/api/v1/auth/me` | viewer | текущий пользователь и роль (для UI) |
| GET/POST | `/api/v1/users` | admin | пользователи; при создании выдаётся токен (показывается один раз) |
| GET/PATCH/DELETE | `/api/v1/users/{id}` | admin | роль, блокировка (`is_active`), удаление; себя изменить нельзя |
| POST | `/api/v1/users/{id}/token` | admin | выпустить новый токен, старый перестаёт работать |
| GET | `/healthz`, `/readyz` | — | сервис жив / готов: база доступна и миграции применены |

Ошибки: `404` — нет сущности, `409` — конфликт состояния (dry-run не успешен, устройство уже
деплоится и т. п.), `422` — ошибка валидации (для intent — со списком `issues` с файлом и полем),
`503` — очередь недоступна (задача сразу помечается `FAILED`).

## Как работают задачи

- **DRY_RUN** — lint всего intent (при ошибке — `FAILED` без обращения к сети) → рендер
  шаблонов → сбор running-config → нормализация → hier_config. Снимки конфигов и патчи
  сохраняются в `config_snapshots` / `job_targets`.
- **DEPLOY** — только для успешного dry-run с изменениями, одно подтверждение на dry-run.
  Устройства обрабатываются по очереди, при первой ошибке раскатка останавливается (остальные — `SKIPPED`).
  Перед применением running-config сверяется по хешу с dry-run: если на устройстве что-то поменяли,
  деплой отклоняется. Далее pre-check → apply → post-check → confirm или rollback (`ROLLED_BACK`).
- **DRIFT_SCAN** — по расписанию (пропускается, если предыдущий скан ещё идёт) или вручную;
  пишет `drift_records` и статус устройства.
- **DRIFT_REMEDIATE** — пересчитывает компенсирующий патч на текущем running-config и
  прогоняет его через тот же транзакционный деплой.

Прогресс (`progress` в `GET /api/v1/jobs/{id}`): у dry-run и скана — по этапам (рендер — 33 %,
сбор running-config — 66 %), у деплоя — по устройствам; 100 % — после успешного завершения.
У упавшей задачи прогресс остаётся на том этапе, где она остановилась.

**Post-check** (правила Health-Check из раздела 2.6). При dry-run из intent запоминается, что
заявлено для устройства: BGP-соседи и интерфейсы. После применения:

- каждый заявленный сосед — `Established` и принимает префиксы (`Prefixes Accepted > 0`);
- каждый включённый заявленный интерфейс — `up/up`; выключенные (`enabled: false`) могут лежать;
- интерфейсы, которых нет в intent, проверяются на регрессию: был `up/up` — должен остаться;
- потери ping до соседей не больше 20 %.

Сосед, удалённый из intent, может пропасть — это не авария. Пока BGP сходится, проверка
повторяется (`POST_CHECK_ATTEMPTS` раз); не прошла последняя попытка — откат.

Задача `FAILED`, если упало хотя бы одно устройство. Зависшие задачи (воркер умер, сообщение
потерялось) через `JOB_TIMEOUT_SECONDS` переводятся в `FAILED` фоновой задачей Beat, устройства
освобождаются. Статус устройства `UNKNOWN` (добавлен к статусам ТЗ) — ещё не проверялось или
состояние неизвестно после неудачного отката.

## Точки интеграции для команды

**Максим (Scrapli/Nornir).** Пайплайн работает с сетью только через протоколы из
`netops/network/base.py`: `ConfigCollector` (параллельный сбор running-config),
`ConfigDeployer` (`apply` с commit confirmed / `confirm` / `rollback`) и `HealthProbe`
(снимок BGP, интерфейсов, ping). В `HealthProbe.snapshot` приходят ожидания из intent: список
заявленных соседей — это и адреса, которые нужно пинговать (5 пакетов). Реализацию достаточно зарегистрировать в `build_toolchain()`
(`netops/toolchain.py`) под новым значением `NETOPS_NETWORK_DRIVER`, оркестрацию менять не нужно.
Пока используется `OfflineLab` — «стенд на файлах»: running-config читается из
`<OFFLINE_LAB_PATH>/<hostname>.cfg`, confirm записывает туда intended config. Так можно
показать весь сценарий без Containerlab.

**Тимофей (шаблоны).** Шаблоны: `<TEMPLATES_PATH>/<platform>/{base,interfaces,acls,bgp}.j2`,
склеиваются в этом порядке, отсутствующие секции пропускаются. В контексте: `device`
(`InventoryDevice`: hostname, platform, role, management_ip…) и `intent` (`DeviceIntent`).
Адреса — объекты `ipaddress`: `iface.ipv4_address.ip`, `.netmask`, `.with_prefixlen`,
`prefix.hostmask`. Неизвестная переменная — ошибка (`StrictUndefined`). Рабочие примеры
под IOS-XE и EOS лежат в `tests/fixtures/templates/`.

**Аня (нормализация).** Встроенные правила — в `DEFAULT_RULES`
(`netops/network/normalization.py`). Дополнительные правила можно подключить YAML-файлом без
правок кода:

```yaml
cisco_iosxe:
  ignore_lines: ['^service timestamps']   # регулярки по строке
  ignore_sections: ['^line vty']          # строка + все вложенные
```

**Даня (формат intent).** Репозиторий intent: `inventory.yaml` (список устройств) и
`devices/<hostname>.yaml` (`interfaces`, `bgp`, `acls` по разделу 2.1 ТЗ). Схема — Pydantic-модели
в `netops/intent/models.py`, пример — `tests/fixtures/intent-repo/` (CLOS: 2 spine Arista + 2 leaf Cisco).
Неизвестные поля запрещены, чтобы опечатки не проходили молча.

## Что ещё не сделано

- Драйвер Scrapli/Nornir и реальные health-check команды (Максим) — пока `OfflineLab`.
- Реальные шаблоны Jinja2 (Тимофей) — в репозитории только тестовые. Когда шаблоны и intent
  появятся в `templates/` и `intent/`, CI проверит, что они рендерятся для всех устройств.
- Метрики для Prometheus и Grafana — отложены; фронтенд в `docker-compose.yml` — когда будет
  образ React-приложения.
- Из раздела 2.7 для администратора пока нет «утверждения опасных изменений» и
  «принудительного наката команд отката» — вынести на обсуждение, что считать опасным изменением.
- LLM-ассистент (Максим): ключ модели нельзя отдавать в браузер, поэтому вызов лучше делать
  через бэкенд — эндпоинт добавим, когда будет выбран провайдер.
- `intent_source` поддерживает только `git_main` (читается рабочая копия в `INTENT_REPO_PATH`,
  `git pull` выполняется снаружи).
