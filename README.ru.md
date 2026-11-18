[English](README.md) | [Русский](README.ru.md)

# CROC DREAM NetOps Platform

**Intent-based автоматизация сети: от YAML intent до согласованных изменений и мониторинга дрейфа.**

**🏆 1-е место — кейс КРОК «NetOps Platform» · «Телеком Будущее», 2026**

Хакатонный проект команды **CROC DREAM** из пяти участников объединяет планирование конфигураций, подтверждение оператором и сравнение состояния сети. В составе проекта — операторский интерфейс React, Python API и воспроизводимый offline-стенд.

## Возможности

- **Проверка intent:** валидация YAML-инвентаря, схем конфигурации устройств и ссылок до подготовки изменений.
- **Предварительный просмотр:** рендер вендорских Jinja2-шаблонов и расчёт remediation/rollback diff через hier_config.
- **Согласование изменений:** просмотр и подтверждение плана в UI; отклонение устаревшего плана при изменении running-config.
- **Контроль выполнения:** хранение задач, снимков конфигурации и аудита; применение и post-check отдельно для каждого устройства.
- **Обнаружение дрейфа:** периодическое сравнение текущей конфигурации с intent через Celery в Compose-развёртывании.
- **Мониторинг стенда:** Prometheus и SNMP exporter, опциональные уведомления Telegram.

```text
YAML intent → валидация → рендер конфигурации → diff / план
          → подтверждение оператором → apply / post-check устройства
          → периодический мониторинг дрейфа
```

## Мой вклад

**Даниил Калинин ([@xadecoride](https://github.com/xadecoride)) — Tech Lead**

Занимался архитектурой, распределением задач и координацией команды, проводил PR review и работал над CI/CD совместно с backend-разработчиком. Реализация backend, frontend и стенда — командная работа.

## Технологический стек

| Область | Технологии |
|---|---|
| Backend | Python, FastAPI, Pydantic, SQLAlchemy, Alembic, PostgreSQL |
| Фоновые задачи | Celery, Redis, Celery Beat |
| Сетевая автоматизация | YAML, Jinja2, hier_config, Scrapli / Paramiko |
| Операторский интерфейс | React, TypeScript, Vite, Tailwind CSS, Monaco Editor |
| Стенд и инструменты | Docker Compose, Containerlab, Prometheus, SNMP exporter, GitHub Actions |

## Быстрый старт: offline-стенд

Нужны Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js 22.12+ и npm.

```bash
git clone https://github.com/xadecoride/CROC-DREAM-NetOps-Platform.git
cd CROC-DREAM-NetOps-Platform/backend
uv sync --locked
uv run python run_local.py
```

Во втором терминале, из корня репозитория:

```bash
cd frontend
npm ci
npm run dev
```

- **Операторский UI:** http://localhost:5173
- **Документация API:** http://127.0.0.1:8000/docs

Standalone-режим использует SQLite и файловое состояние устройств. Для PostgreSQL, Celery worker и периодического мониторинга дрейфа используйте [инструкцию Docker Compose](docs/technical-overview.ru.md#docker-compose-offline-pipeline-и-фоновые-задачи).

## Границы проекта

Это **хакатонный прототип для лаборатории**, не промышленное внедрение. Offline-стенд демонстрирует планирование и применение без сетевого оборудования. В проекте есть код живого SSH-драйвера и шаблоны Cisco, Arista, Huawei, но приёмка на живых устройствах и вендорские проверки требуют отдельной валидации.

Тестовые токены и учётные данные используйте только в доверенной локальной среде. До включения живого доступа изучите [матрицу вендоров](docs/technical-overview.ru.md#матрица-вендоров) и [технические ограничения](docs/technical-overview.ru.md#известные-ограничения). Готовность к продакшену, полная поддержка вендоров и атомарный откат всей фабрики не заявляются.

## Проверка и документация

Зафиксированный локальный запуск backend-тестов **18 ноября 2026 года**: **355 passed, 1 skipped, покрытие 79%**, исходная ревизия `d1d8118`. Пропущенный тест требует отдельно предоставленного live-инвентаря. Результаты относятся к локальным тестам и фикстурам, не к живому оборудованию. Подробнее: [проверки](docs/technical-overview.ru.md#проверка) и [GitHub Actions](https://github.com/xadecoride/CROC-DREAM-NetOps-Platform/actions).

- [Технический обзор, архитектура и запуск стендов](docs/technical-overview.ru.md)
- [Архитектура backend](docs/backend-architecture.md)
- [Формат YAML intent](intent/README.md)
- [Offline-стенд](lab/README.md)
- [Подготовка внешнего живого стенда](docs/live-lab.md)
- [Архитектура frontend](frontend/ARCHITECTURE.md)
