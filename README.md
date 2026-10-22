# CROC DREAM — NetOps Platform

**Платформа централизованного управления, транзакционной оркестрации и предиктивного мониторинга гетерогенной сетевой инфраструктуры.**

**Команда:** CROC DREAM  
**Статус:** Полная готовность к защите (сборка `tsc -b` + `vite` зелёная, `oxlint` 0 ошибок, все модульные и интеграционные тесты `pytest` проходят на 100%).

---

## Навигатор по документации модулей (Git Module Guides)

Каждый блок репозитория снабжён собственной подробной инструкцией с описанием архитектуры, файлов и контрактов:

| Модуль / Каталог | Инструкция | За что отвечает блок |
|------------------|------------|----------------------|
| **`backend/`** | [**`backend/README.md`**](backend/README.md) | Серверное ядро FastAPI, PostgreSQL + Alembic, Celery/Redis, управление мульти-стендами (`stands.py`), L1–L3 топология и коммутация (`topology*.py`), живучесть и бэкапы (`vitality.py`), `ssh_pool.py` + `table_parsers.py`, Statistical Forecast, `AI Guard`, `Emergency Hub` |
| **`frontend/`** | [**`frontend/README.md`**](frontend/README.md) | Клиентское ядро NOC (React 19 + TypeScript 5.9, `0 any`), модульный API-клиент `src/api/*`, хуки `src/hooks/*`, словари `src/locales/*`, раскладка `layout/`, `views/`, `modals/`, `charts/`, визард «Матрёшка», менеджер стендов `StandsModal` |
| **`intent/`** | [**`intent/README.md`**](intent/README.md) | Git Source of Truth: реестр устройств `inventory.yaml`, декларативные профили узлов `devices/*.yaml`, профили платформ `platforms/*.yaml`, стенды `stands/*` и правила нормализации `diff_filters.yaml` |
| **`templates/`** | [**`templates/README.md`**](templates/README.md) | Мульти-вендорный движок шаблонов Jinja2 (`StrictUndefined`) для 8 платформ (`Cisco IOS-XE`, `Arista EOS`, `Huawei VRP`, `Eltex MES`, `Eltex ESR`, `Juniper Junos`, `YADRO KORNFE`, `Linux Server`) |
| **`lab/`** | [**`lab/README.md`**](lab/README.md) | Лабораторный стенд CLOS: эталонные конфигурации `golden_configs/`, файловый эмулятор `running/`, резервные копии `backups/` и скрипт сброса `reset_lab.sh` |
| **`scripts/`** | [**`scripts/README.md`**](scripts/README.md) | CLI-утилиты самодиагностики (`doctor.py`), проверки SSH-стенда (`verify_live_lab.py`), сброса (`reset_lab.py`) и демо-прогона (`automate_netops.py`) |
| **`docs/`** | [**`docs/backend-architecture.md`**](docs/backend-architecture.md) | Глубокое описание внутренней архитектуры бэкенда, единого конвейера телеметрии, пред-очистки логов и правил Post-Check ([`docs/templates-integration.md`](docs/templates-integration.md), [`docs/LIVE_LAB_GUIDE.md`](docs/LIVE_LAB_GUIDE.md)) |

---

## Содержание

1. [Что это за проект](#1-что-это-за-проект)
2. [Поддерживаемые вендоры и платформы (8 профилей)](#2-поддерживаемые-вендоры-и-платформы-8-профилей)
3. [Текущий статус модулей](#3-текущий-статус-модулей)
4. [Архитектура и структура репозитория](#4-архитектура-и-структура-репозитория)
5. [Быстрый старт (Docker)](#5-быстрый-старт-docker)
6. [Сквозной демо-сценарий (для защиты)](#6-сквозной-демо-сценарий-для-защиты)

---

## 1. Что это за проект

Платформа централизованного управления разнородной сетевой инфраструктурой по методологии **Intent-Based Networking (IBN)** и **GitOps**:
- Инженер описывает целевое состояние сети (Intent) в вендор-независимом YAML (`intent/devices/*.yaml`).
- Платформа выполняет **Pre-Flight Lint** всей фабрики (проверка пересечений IP/масок, дублей Router ID и ASN).
- Рендерит целевые конфигурации через Jinja2-шаблоны под конкретного вендора, очищает `running-config` от динамического шума и строит минимальный иерархический патч (`hier_config`) и зеркальный патч отката (`rollback_patch`).
- Проверяет патч через **AI Pre-Flight Guard**, требует явного подтверждения оператора, выкатывает изменения транзакционно (`commit confirmed` / `configure session`) с автоматической верификацией **Post-Check** (BGP Established, префиксы, порты `up/up`, потери ping ≤ 20%) и авто-откатом при сбое.
- Поддерживает **мульти-стендовую оркестрацию (`Stands`)**, интерактивную **L1–L3 коммутацию топологии**, двухсостоянийную диагностику живучести (**Vitality `UP`/`DOWN`**) с автоматическим сохранением и подхватом резервных копий (`lab/backups/`), а также гостевой доступ по статическому QR-коду (`inv_*`) и именным токенам (`crk_*`).
- Каждые 15 минут сканирует сеть на конфигурационный дрейф (**Drift Engine**), выполняет пред-очистку логов/снапшотов (`retention.pre_collect_cleanup`), собирает телеметрию через каскад `gNMI → Node Exporter → SNMP → SSH Pool` в **VictoriaMetrics / Prometheus** и прогнозирует исчерпание ресурсов с помощью модели временных рядов **Statistical Forecast**.

**Ключевой сквозной конвейер:**

`Git/YAML Intent → Pre-Flight Lint → Jinja2 Render → Dry-Run & Hier-Diff → AI Guard & Approval → Transactional Deploy / Auto-Rollback → Unified Telemetry & Statistical Forecast`

---

## 2. Поддерживаемые вендоры и платформы (8 профилей)

Платформа поддерживает **7 сетевых ОС и серверный контур Linux**, включая российских производителей (**Eltex**, **YADRO**):

| Вендор | Платформа (`Platform`) | Роль в стенде / Назначение | Шаблоны Jinja2 | Нормализация и Diff | Драйвер (`Scrapli` + `Offline`) |
|--------|------------------------|----------------------------|:--------------:|:-------------------:|:-------------------------------:|
| **Cisco** | `cisco_iosxe` (c8000v / CSR1000v) | `spine-1.croc.lab` (Spine-ядро CLOS) | ✅ `templates/cisco_iosxe/` | ✅ `hier_config` + фильтры | ✅ Реализован и проверен |
| **Arista** | `arista_eos` (cEOS 4.32) | `spine-2.croc.lab` (Spine-ядро CLOS) | ✅ `templates/arista_eos/` | ✅ `hier_config` + фильтры | ✅ Реализован и проверен |
| **Huawei** | `huawei_vrp` (CloudEngine CE12800) | `leaf-1`, `leaf-2` (Leaf-уровень CLOS) | ✅ `templates/huawei_vrp/` | ✅ `undo` + `commit-id` | ✅ Реализован и проверен |
| **Eltex** | `eltex_mes` / `eltex_esr` | Российский вендор (Leaf / Border / Edge) | ✅ `eltex_mes/`, `eltex_esr/` | ✅ `hier_config` + фильтры | ✅ Реализован и проверен |
| **YADRO** | `yadro_kornfe` (KORNFE SONiC) | Российский вендор ЦОД (Leaf / Spine) | ✅ `templates/yadro_kornfe/` | ✅ Фильтры секций | ✅ Реализован и проверен |
| **Juniper** | `juniper_junos` (vMX / vQFX) | Магистральный Edge / Core | ✅ `templates/juniper_junos/` | ✅ Фильтры секций | ✅ Реализован и проверен |
| **Linux** | `linux_server` (POSIX / iproute2) | Вычислительные серверы / Хосты ЦОД | ✅ `templates/linux_server/` | ✅ Фильтры секций | ✅ SSH + `node_exporter` |

---

## 3. Текущий статус модулей

| Подсистема | Статус | Где реализовано |
|------------|--------|-----------------|
| **Backend Core (FastAPI + SQLAlchemy 2.0 + Alembic + Celery/Redis)** | ✅ Готово (677 тестов проходят, все модули соблюдают лимит размера) | [`backend/netops/`](backend/netops) |
| **Мульти-стенды (`stands.py`), L1–L3 Топология (`topology*.py`) и Живучесть (`vitality.py`)** | ✅ Готово (шифрование паролей Jump-хостов, авто-бэкапы `lab/backups/`, коммутация линков, чекпоинты) | [`stands.py`](backend/netops/services/stands.py), [`topology.py`](backend/netops/services/topology.py), [`vitality.py`](backend/netops/services/vitality.py) |
| **Pre-Flight Intent Linter & Pydantic Schema** | ✅ Готово (`inventory.yaml`, `platforms/*.yaml` и `devices/*.yaml` синхронизированы) | [`backend/netops/intent/`](backend/netops/intent) |
| **Multi-Vendor Jinja2 Templates (8 платформ)** | ✅ Готово (`base`, `interfaces`, `acls`, `bgp`, `vxlan`) | [`templates/`](templates) |
| **Иерархический Diff и Нормализация CLI** | ✅ Готово (`hier_config` + `diff_filters.yaml`) | [`backend/netops/network/`](backend/netops/network) |
| **Двойной сетевой контур (`ssh_pool.py` + `table_parsers.py` + `OfflineLab`)** | ✅ Готово (1 VTY-сессия на узел, мультиплексирование `direct-tcpip` через единый SSH Jump-туннель, проверка `SSH-` баннера) | [`ssh_pool.py`](backend/netops/network/ssh_pool.py), [`table_parsers.py`](backend/netops/network/table_parsers.py), [`scrapli_driver.py`](backend/netops/network/scrapli_driver.py) |
| **Предиктивная аналитика Statistical Forecast + `AI Pre-Flight Guard`** | ✅ Готово (квантили `p10–p90`, ETA пробития SLA, блокировка опасных патчей) | [`forecast.py`](backend/netops/services/forecast.py), [`ai_guard.py`](backend/netops/services/ai_guard.py) |
| **Аварийный пульт `Emergency Hub` и `Factory Freeze`** | ✅ Готово (прямой CLI, экстренный патч, Soft/Hard Reset, Kill Switch) | [`emergency.py`](backend/netops/api/routes/emergency.py), [`emergency_reset.py`](backend/netops/api/routes/emergency_reset.py) |
| **5-слойный визард «Матрёшка» (`WelcomeModal`) и QR-доступ (`static_viewer.py`)** | ✅ Готово (5 шагов настройки + многопользовательский read-only вход по QR-коду с мгновенным отзывом) | [`system.py`](backend/netops/api/routes/system.py), [`static_viewer.py`](backend/netops/services/static_viewer.py), [`bootstrap_discovery.py`](backend/netops/services/bootstrap_discovery.py) |
| **Frontend NOC SPA (React 19 + TS Strict, `0 any`)** | ✅ Готово (2D топология + Macro Fleet View, редактор Intent, RBAC, изоляция `allowed_system`) | [`frontend/src/`](frontend/src) |
| **Единый конвейер наблюдаемости и очистки (`retention.py` + `collector.py` + `VictoriaMetrics`)** | ✅ Готово (автоочистка логов/БД перед сбором, шардированный кэш метрик, gNMI/SNMP/Exporter/SSH) | [`retention.py`](backend/netops/services/retention.py), [`backend/netops/telemetry/`](backend/netops/telemetry) |

---

## 4. Архитектура и структура репозитория

```text
CROC-DREAM-NetOps-Platform/
├── README.md                  # Главный навигатор по платформе (этот файл)
├── AGENTS.md                  # Архитектурные инварианты проекта (проверяются в test_repo_data.py)
├── TEST_READY.md              # Матрица приёмочных E2E-тестов (14/14 PASSED)
├── Dockerfile                 # All-in-One контейнерный образ платформы (docker/entrypoint.sh)
├── docker-compose.yml         # Полный микросервисный стек: postgres, redis, api, worker, beat, victoriametrics, prometheus, gnmic, snmp-exporter, node-exporter, grafana, telegram-bot, frontend
├── backend/                   # Серверное ядро FastAPI + Celery + Scrapli (см. backend/README.md)
│   ├── Dockerfile             # Контейнерный образ бэкенда и воркеров (offline-resilient uv sync)
│   ├── netops/                # Доменные модули: api/, services/, pipeline/, network/, intent/, models/, schemas/, telemetry/, worker/
│   ├── migrations/            # Миграции схемы БД (Alembic, 0001..0011)
│   └── tests/                 # 677 модульных, API и интеграционных тестов (pytest)
├── frontend/                  # Клиентский интерфейс NOC на React 19 + TypeScript (см. frontend/README.md)
│   └── src/
│       ├── api/               # Модульный типизированный HTTP-клиент (client, auth, http, types, domainTypes, standsApi, deviceJobApi)
│       ├── hooks/             # Кастомные хуки (useNetopsPolling, useKeyboardShortcuts)
│       ├── locales/           # Двуязычные словари локализации (ru.ts, en.ts)
│       ├── App.tsx            # Корневой оркестратор состояния
│       └── components/
│           ├── layout/        # Каркас и дизайн-система (Header, Sidebar, MobileNav, CommandPalette, AppOverlays, ToastNotification, ui)
│           ├── views/         # Рабочие экраны (Dashboard, DeviceList, DevicePage, DiffViewer, DriftView, IntentEditor, JobsView, ChaosLabView, EmergencyHub + субмодули device/)
│           ├── modals/        # Модальные окна и дроверы (WelcomeModal, ShareModal, StandsModal, AccessCodeModal, DryRunModal, AddDeviceModal, DeviceDrawer, DeviceDetailModal, CopilotPanel, ShortcutsModal)
│           └── charts/        # Визуализация (TopologyMap, TopologyMacroFleetView, TopologyCommutationPanel, TopologyToolbar, TopologyCheckpointBar, ForecastChart)
├── intent/                    # Git Source of Truth: inventory.yaml, platforms/*.yaml, devices/*.yaml, stands/* (см. intent/README.md)
├── templates/                 # Шаблоны Jinja2 для 8 платформ: cisco_iosxe, arista_eos, huawei_vrp, eltex_mes, eltex_esr, juniper_junos, yadro_kornfe, linux_server (см. templates/README.md)
├── lab/                       # Лабораторный стенд: golden_configs/, running/, backups/, reset_lab.sh (см. lab/README.md)
├── scripts/                   # Инженерные CLI-утилиты: doctor.py, verify_live_lab.py, reset_lab.py, automate_netops.py (см. scripts/README.md)
├── monitoring/                # Конфигурация Prometheus, SNMP Exporter, gNMIc и дашборды Grafana
└── docs/                      # Архитектурная документация и гайды (backend-architecture.md, LIVE_LAB_GUIDE.md, templates-integration.md)
```

---

## 5. Быстрый старт (Docker)

Единая точка входа в платформу — собранный Docker-стек:

```bash
docker compose up --build -d
python3 backend/scripts/smoke_stack.py
```

- **Консоль Владельца (Owner)**: `http://localhost:5173`
- **Ролевые консоли (Admin / Operator / Jury)**: `http://localhost:5174`, `:5175`, `:5176`
- **Swagger / OpenAPI**: `http://localhost:8001/docs`
- **Grafana / Prometheus / VictoriaMetrics**: `http://localhost:3000`, `:9090`, `:8428/vmui`

### Как дать гостю или жюри доступ к стенду

1. **Статический QR-код для Жюри (многопользовательский Read-Only)**:
   - Владелец открывает `http://localhost:5173` → **«Поделиться»** → блок **«QR-доступ для Жюри (Static Viewer)»**.
   - Любое число членов жюри одновременно сканирует QR-код или переходит по ссылке `http://<хост>:5176/?token=inv_...` со своих ноутбуков и телефонов без конфликта привязки устройств.
   - При нажатии **«Перевыпустить»** или отключении тумблера все открытые сессии по старому QR-коду мгновенно аннулируются (`401 Unauthorized`).
2. **Персональные ролевые приглашения (`crk_*`)**:
   - Во вкладке **«Участники»** выберите роль (`Jury` / `Operator` / `Admin`), срок жизни и (опционально) `allowed_system` (например, `fabric:croc-dc1-pod1`) → **«Создать»**.
   - Персональный токен привязывается к первому устройству участника (`device-bound`); сбросить привязку или отозвать доступ можно в один клик.
3. Проверка стенда: `NETOPS_SMOKE_TOKEN=<токен admin> python3 backend/scripts/smoke_stack.py`.

---

## 6. Сквозной демо-сценарий (для защиты)

1. **Первичная инициализация («Матрёшка») и Стенды (`StandsModal`)**: откройте `http://127.0.0.1:5173/?setup=1`, проверьте 5 слоёв готовности контура или переключите активный стенд в менеджере стендов.
2. **Обзор топологии, коммутации L1–L3 и прогнозов (`Dashboard`)**: покажите граф CLOS-фабрики (`TopologyMap` / `TopologyMacroFleetView`), панель коммутации линков (`TopologyCommutationPanel`), чекпоинты топологии, статус `AI Pre-Flight Guard` и прогноз загрузки Statistical Forecast.
3. **Инспекция узла, Живучесть и Intent (`DevicePage` / `IntentEditor`)**: кликните на любой узел на карте, покажите статус живучести (`UP`/`DOWN` с авто-фоллбэком на `lab/backups/`), отредактируйте YAML-интент в `IntentEditor` или перейдите в диагностику узла.
4. **Dry-Run и Diff (`DryRunModal` → `DiffViewer`)**: запустите `Dry-Run`, покажите построчное сравнение `running` vs `candidate` в двухпанельном Monaco DiffViewer и вызовите ИИ-объяснение рисков.
5. **Chaos Engineering и Авто-ремедиация (`ChaosLabView` → `DriftView`)**: инжектируйте несанкционированный дрейф на `spine-1` или `leaf-1`, покажите обнаружение `DRIFT_DETECTED` и устраните отклонение одной кнопкой `Remediate`.
6. **Аварийный контур (`EmergencyHub`)**: продемонстрируйте прямой CLI-терминал, рубильник `Factory Freeze` (блокирующий любые выкатки при аварии) и возврат к эталону через `Soft Reset`.

