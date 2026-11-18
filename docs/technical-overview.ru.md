[Обзор проекта](../README.ru.md) · [English](technical-overview.md) | [Русский](technical-overview.ru.md)

# CROC DREAM NetOps Platform — технический обзор

Командный хакатонный прототип управления конфигурациями сети: проверка YAML intent, вендорский рендер, расчёт различий, применение после ручного подтверждения и мониторинг дрейфа.

**1-е место — кейс КРОК «NetOps Platform»** · «Телеком Будущее», 2026.

В команде **CROC DREAM** было пять участников. Даниил Калинин ([@xadecoride](https://github.com/xadecoride)) — Tech Lead: архитектура, координация задач, PR review и совместная работа над CI/CD с backend-разработчиком. Реализация командная; репозиторий не представлен как единоличная разработка.

## Объём и статус

В исходном коде есть pipeline планирования и применения, хранение задач и снимков, файловый драйвер, живой SSH-драйвер, проверка ролей, операторский интерфейс React и периодические задачи проверки дрейфа. Offline-режим — воспроизводимый файловый стенд, не полная эмуляция свойств сетевой ОС.

Код живого драйвера присутствует, но транзакции end to end и вендорские post-check требуют дальнейшей проверки. Не заявляются готовность к промышленной эксплуатации, полная поддержка шести вендоров или атомарное изменение всей фабрики.

## Pipeline конфигурации

Git/YAML intent → pre-flight lint → Jinja2 render → running-config normalization → hier_config diff (remediation + rollback) → manual approval → transactional apply → post-check → scheduled drift monitoring (Celery Beat)

1. Загрузка инвентаря и intent устройств из рабочей копии YAML-репозитория; проверка схем и ссылок.
2. Рендер конфигурации через Jinja2 и сбор текущего running-config.
3. Нормализация текста и фильтрация перед расчётом remediation и rollback через hier_config.
4. Сохранение плана, нормализованных снимков и хеша собранного running-config.
5. Для применения нужен подтверждённый оператором план. Running-config собирается повторно; при изменении хеша устаревший план отклоняется.
6. Применение, post-check и confirm либо rollback выполняются **для каждого устройства**. Раскатка последовательная и останавливается при ошибке; для уже подтверждённых устройств не доказан общий атомарный откат всей фабрики.
7. В Compose-развёртывании Celery Beat запускает периодические проверки дрейфа; worker сравнивает running-config с рендером intent.

Git хранит историю изменений intent. Файлы поступают из подключённой рабочей копии; автоматический контроллер с Git webhook и непрерывной reconciliation не заявляется.

## Архитектура и стек

```text
Операторский интерфейс React / Monaco
  -> FastAPI: устройства, инвентарь, планы, задачи и отчёты дрейфа
  -> PostgreSQL: устройства, задачи, планы, снимки и аудит
  -> Celery worker / Redis broker
       -> intent lint -> Jinja2 -> normalization -> hier_config
       -> offline-файлы ИЛИ SSH-драйвер Scrapli / Paramiko
       -> post-check -> confirm / rollback

Celery Beat -> периодические задачи дрейфа и телеметрии
Prometheus / SNMP exporter -> мониторинг стенда
Telegram bot -> уведомления и команды статуса (учётные данные опциональны)
```

| Слой | Технологии |
|---|---|
| Backend | Python 3.11+, FastAPI, Pydantic, SQLAlchemy 2, Alembic, PostgreSQL 16 |
| Фоновые задачи | Celery, Redis 7, Celery Beat |
| Конфигурации | Git/YAML, Jinja2, hier_config |
| Доступ к устройствам | Scrapli, scrapli-community, Paramiko; зависимости живого драйвера в дополнительном наборе `scrapli` |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS 4, Monaco Editor |
| Стенды и мониторинг | Containerlab, Docker Compose, Prometheus, SNMP exporter, Telegram bot |

Standalone-запуск использует SQLite и внутрипроцессный диспетчер потоков вместо PostgreSQL/Celery. Grafana не входит в предоставленный Compose. Nornir отсутствует в зависимостях проекта.

## Быстрый старт

### Standalone-разработка: offline-файлы

Нужны Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js 22.12+ и npm.

```bash
git clone https://github.com/xadecoride/CROC-DREAM-NetOps-Platform.git
cd CROC-DREAM-NetOps-Platform/backend
uv sync --locked
uv run python run_local.py
```

В другом терминале, из корня репозитория:

```bash
cd frontend
npm ci
npm run dev
```

- UI: http://localhost:5173
- API documentation: http://127.0.0.1:8000/docs
- Health/readiness: `/healthz`, `/readyz`

Launcher инициализирует SQLite, импортирует инвентарь и использует `lab/running/` как состояние устройств. В нём есть тестовые API-токены и заглушки учётных данных стенда. Используйте локально. Он **не запускает Celery Beat**, поэтому этот режим не подтверждает работу периодического мониторинга дрейфа.

### Docker Compose: offline-pipeline и фоновые задачи

Из корня репозитория, Docker Compose v2.24+:

```bash
cp backend/.env.example backend/.env
# Локально проверьте API-токены, SSH-профили и опубликованные порты.
docker compose config --quiet
docker compose up --build -d
docker compose ps
```

Сервисы: PostgreSQL, Redis, API, worker, Beat, Telegram bot, Prometheus, SNMP exporter и frontend. API применяет Alembic-миграции при старте; worker/Beat ждут готовности API. Без настроенных учётных данных бот простаивает.

| Сервис | Адрес по умолчанию |
|---|---|
| UI | http://localhost:5173 |
| API documentation | http://localhost:8000/docs |
| Prometheus | http://localhost:9090 |

```bash
docker compose logs -f api worker beat
docker compose down
```

Сетевой драйвер по умолчанию — `offline`. Тестовые учётные данные и опубликованные порты должны оставаться внутри доверенного стенда. Полная контейнерная сборка и запуск при этом обновлении документации не выполнялись.

## Три режима стенда

| Режим | Инвентарь и состояние | Что он проверяет |
|---|---|---|
| Файловый offline-стенд | `intent/inventory.yaml`; четыре файла устройств в `lab/running/` | Pipeline без сетевых устройств. Apply/confirm/rollback работают с файлами; health по умолчанию синтетический, из ожиданий intent |
| Локальный Containerlab | `lab/fabric.clab.yaml`; Cisco spine, Arista spine, два Huawei leaf и два Alpine host | Отдельная топология из шести узлов, **четыре сетевых устройства**. Нужны вендорские образы, ресурсы хоста и подходящий live-инвентарь |
| Внешний живой стенд | Отдельно описанная топология из шести сетевых устройств: два Arista spine, два Cisco leaf, два Huawei leaf | SSH-доступ к внешней лаборатории. Нужны предоставленный инвентарь, учётные данные, образы и вендорские приёмочные проверки |

Топологии не взаимозаменяемы. Имена и роли узлов локального Containerlab отличаются от схемы внешнего живого стенда.

### Подготовка живого режима

В этой ревизии **нет отслеживаемого каталога `intent-live/`**. Compose-оверлей ожидает его по умолчанию; нужно предоставить подходящую рабочую копию intent, а не считать оверлей готовым к запуску без настройки.

1. Отдельно подготовьте разрешённые к использованию вендорские образы и рабочую топологию Containerlab.
2. Создайте live-инвентарь и intent каждого устройства; проверьте hostname, platform, адреса управления, порты и ссылки `auth_profile`.
3. Локально настройте `NETOPS_AUTH_PROFILES` в `backend/.env`, не в Git. Для живого локального backend также нужен `uv sync --extra scrapli`.
4. На хосте стенда используйте существующую сеть `clab_mgmt` и укажите каталог intent через `INTENT_LIVE`:

```bash
INTENT_LIVE=/absolute/path/to/live-intent docker compose \
  -f docker-compose.yml -f docker-compose.lab.yml config --quiet
```

После проверки объединённой конфигурации и ограничений ниже замените `config --quiet` на `up -d --build`. Оверлей выбирает драйвер `scrapli` и подключает SNMP exporter к сети Containerlab. До применения изменений проверьте сбор конфигурации и health в режиме чтения.

Описание внешней топологии, образов и хоста: [live-lab.md](live-lab.md). Это инструкция по стенду, не доказательство успешных транзакций устройств.

## Матрица вендоров

| Платформа | Рендер / diff | Код живого драйвера | Текущая граница |
|---|---|---|---|
| Cisco IOS-XE (`cisco_iosxe`) | Шаблоны; hier_config `CISCO_IOS` | Scrapli; ветки конфигурации, confirm и abort | Команды и сессии нужно проверить на конкретной ОС/образе; успешная живая приёмка не заявляется |
| Arista EOS (`arista_eos`) | Шаблоны; `ARISTA_EOS` | Scrapli; именованная configure session и таймер | Нужна живая проверка сессий, обработки ошибок и повторного подключения/confirm |
| Huawei VRP (`huawei_vrp`) | Шаблоны; `HUAWEI_VRP` | scrapli-community/Paramiko; commit и rollback по commit-id | Сбор running-config и post-check используют Cisco-style команды; Huawei-парсеры не подключены к `snapshot()` |
| Eltex ESR (`eltex_esr`) | Шаблоны есть; diff через `GENERIC` | Есть mapping платформы | Транзакционное применение явно заблокировано; узла Eltex в предоставленной топологии нет |
| Eltex MES | Только каталог шаблонов | Реализованный platform/driver-контур не установлен | Наличие шаблонов не означает поддержку платформы |

В текущих шаблонах есть подстановки из intent; старое утверждение, что все шаблоны статические, уже неактуально. В отдельных секциях остаются адреса и блоки конкретной лаборатории. Результат рендера нужно проверять для каждой целевой среды.

## Операторский сценарий и доступ

1. Начните с offline-драйвера и синхронизируйте инвентарь.
2. Измените YAML intent устройства, выполните pre-flight и создайте dry-run-план.
3. Проверьте intended/running, remediation и rollback в интерфейсе.
4. Подтвердите план и применяйте только к нужным managed-устройствам.
5. Проверьте логи задачи и post-check, прежде чем считать изменение завершённым.
6. Для демонстрации дрейфа измените файл состояния стенда и запустите scan вручную либо используйте расписание Beat в Compose.

Offline-применение меняет отслеживаемые файлы в `lab/running/`. Сохраняйте нужное состояние и проверяйте `git diff -- lab/running`; не сбрасывайте чужие изменения ради очистки демо.

Backend проверяет роли `viewer`, `operator`, `admin`. Изменения устройств `MONITORING_ONLY` блокируются до перевода в managed-режим. Emergency endpoint ограничивает префиксы команд; произвольный patch возвращает 501, чтобы не обходить планирование.

UI содержит тестовые токены ролей, а не полноценный вход с сессиями. Серверные проверки ролей не делают демонстрационные учётные данные подходящими для общей среды.

## Проверка

Локальный запуск **18 ноября 2026 года**, Python 3.13.14, исходная ревизия `d1d8118` до изменения документации:

```bash
cd backend
uv run --frozen pytest --cov=netops
```

**355 passed, 1 skipped; покрытие по отчёту 79%.** Пропущенный тест требует отсутствующий `intent-live/inventory.yaml`. Результат относится к локальным тестам и фикстурам, не к живому оборудованию или контейнерной приёмке всего стека.

Другие проверки для разработки:

```bash
uv run ruff check .
uv run black --check .
uv run mypy
```

```bash
cd frontend
npm ci
npm run lint
npm run build
```

[.github/workflows/ci-cd.yml](../.github/workflows/ci-cd.yml) описывает проверки backend, сборку frontend, Compose smoke-тест на фикстурах и условную публикацию в GHCR. Для ревизии документации `ad66257` успешно завершились [CI/CD](https://github.com/xadecoride/CROC-DREAM-NetOps-Platform/actions/runs/44202686204) и [Ruff](https://github.com/xadecoride/CROC-DREAM-NetOps-Platform/actions/runs/44202686260). Это результат конкретного запуска, не приёмка на живых устройствах.

## Известные ограничения

- **Живые транзакции:** многие ответы apply/commit не проверяются на ошибки устройства. Нужна проверка конкретных ОС и образов; атомарность всей фабрики не установлена.
- **Сбор и health Huawei:** активный путь использует `show running-config`, `show ip bgp summary`, `show ip interface brief`. Наличие отдельных Huawei-парсеров не исправляет эти вызовы.
- **Fallback health:** нераспознанный вывод ping может трактоваться как нулевые потери. Синтетический offline-health нельзя смешивать с наблюдаемым состоянием сети.
- **Доверие SSH:** строгая проверка host key отключена; Paramiko принимает неизвестные ключи. Это нужно изменить до использования вне одноразовой лаборатории.
- **Шаблоны и вендоры:** остаются lab-specific блоки; применение Eltex ESR заблокировано, а MES-шаблоны сами по себе не подтверждают поддержку.
- **Безопасность применения:** идентификаторы commit для Huawei rollback хранятся в памяти worker; поведение при прерывании и восстановлении нужно протестировать отдельно.
- **Учётные данные:** тестовые UI/API-токены и материалы лабораторных учётных записей нужно проверить и заменить. Некоторые diff-артефакты содержат хеши паролей стенда; универсальное удаление секретов не заявляется.
- **Мониторинг и интеграция:** Grafana отсутствует в Compose. Скрипты Playwright не подключены к npm scripts и показанному CI workflow.

## План развития

- Проверить сбор, ошибки команд, транзакционные сессии и rollback на конкретных образах Cisco, Arista и Huawei.
- Подключить вендорские команды и парсеры health; считать нераспознанный вывод ошибкой, не успешной проверкой.
- Убрать оставшиеся предположения о конкретном стенде из шаблонов; тестировать влияние intent на рендер.
- Определить объём поддержки Eltex ESR/MES; добавить драйверы, топологию и приёмочные тесты до расширения списка вендоров.
- Включить проверку SSH host key и заменить demo-авторизацию полноценным операторским входом.
- Проверить артефакты учётных данных и доступ извне; обеспечить восстановление прерванных задач и состояния rollback.
- Добавить full-stack/live-приёмку и запуск браузерных тестов в CI.
- При необходимости добавить Grafana dashboards, отдельно от уже реализованного Prometheus-мониторинга.

## Структура и дополнительная документация

```text
backend/          API, services, pipeline, драйверы, миграции и тесты
frontend/         Операторский интерфейс React и Monaco diff
intent/           Offline-инвентарь и YAML intent устройств
templates/        Вендорские шаблоны Jinja2
lab/              Containerlab, golden configs и offline-состояние
monitoring/       Prometheus, alert rules и SNMP exporter
docs/             Архитектура, шаблоны и заметки по стенду
scripts/          Диагностика и лабораторные утилиты
```

- [Архитектура backend](backend-architecture.md)
- [Запуск backend](../backend/README.md)
- [Формат intent](../intent/README.md)
- [Интеграция шаблонов](templates-integration.md)
- [Offline-стенд](../lab/README.md)
- [Архитектура frontend](../frontend/ARCHITECTURE.md)

Часть подробных документов отражает более ранние этапы разработки. При расхождениях ориентируйтесь на текущий код и описанные выше границы.
