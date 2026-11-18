[English](README.md) | [Русский](README.ru.md)

# CROC DREAM NetOps Platform

**Intent-based network automation: from YAML configuration intent to reviewed changes and drift monitoring.**

**🏆 1st Place — CROC “NetOps Platform” Case · Telecom Future («Телеком Будущее»), 2026**

Built by the five-member **CROC DREAM** team, this hackathon project brings configuration planning, operator approval and network-state comparison into one workflow. It includes a React operator interface, a Python API and a reproducible offline lab.

## What it does

- **Validate intent:** check YAML inventory, device configuration schemas and references before planning changes.
- **Preview configuration changes:** render vendor-specific Jinja2 templates and calculate remediation and rollback diffs with hier_config.
- **Review and approve:** inspect plans in the operator UI, approve deployment and reject stale plans when running configuration has changed.
- **Track execution:** persist jobs, configuration snapshots and audit data; apply and post-check changes per device.
- **Detect drift:** compare running configuration with intended state through scheduled Celery tasks in the Compose deployment.
- **Monitor the lab:** use Prometheus and SNMP exporter, with optional Telegram notifications.

```text
YAML intent → validation → configuration render → diff / plan
          → operator approval → per-device apply / post-check
          → scheduled drift monitoring
```

## My contribution

**Daniil Kalinin ([@xadecoride](https://github.com/xadecoride)) — Tech Lead**

I worked on the architecture, task allocation and team coordination, reviewed pull requests, and shared CI/CD work with the backend developer. Backend, frontend and lab implementation were a team effort.

## Technology stack

| Area | Technologies |
|---|---|
| Backend | Python, FastAPI, Pydantic, SQLAlchemy, Alembic, PostgreSQL |
| Background processing | Celery, Redis, Celery Beat |
| Network automation | YAML, Jinja2, hier_config, Scrapli / Paramiko |
| Operator interface | React, TypeScript, Vite, Tailwind CSS, Monaco Editor |
| Lab and tooling | Docker Compose, Containerlab, Prometheus, SNMP exporter, GitHub Actions |

## Quick start: offline lab

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js 22.12+ and npm.

```bash
git clone https://github.com/xadecoride/CROC-DREAM-NetOps-Platform.git
cd CROC-DREAM-NetOps-Platform/backend
uv sync --locked
uv run python run_local.py
```

In a second terminal, from the repository root:

```bash
cd frontend
npm ci
npm run dev
```

- **Operator UI:** http://localhost:5173
- **API documentation:** http://127.0.0.1:8000/docs

Standalone mode uses SQLite and file-based device state. For PostgreSQL, Celery workers and scheduled drift scans, follow the [Docker Compose setup](docs/technical-overview.md#docker-compose-offline-pipeline-with-background-tasks).

## Project scope

This is a **hackathon prototype for lab use**, not a production deployment. The offline lab demonstrates the planning and deployment workflow without network equipment. Live SSH-driver code and Cisco, Arista and Huawei templates are included, but live-device acceptance and vendor-specific checks remain separate validation work.

Use development tokens and lab credentials only in a trusted local environment. Review the [vendor matrix](docs/technical-overview.md#vendor-matrix) and [technical limitations](docs/technical-overview.md#known-limitations) before enabling live access. Production readiness, complete vendor support and fabric-wide atomic rollback are not claimed.

## Verification and documentation

The recorded local backend test run on **18 November 2026** reported **355 passed, 1 skipped and 79% coverage** on source revision `d1d8118`. The skipped test requires a separately supplied live inventory. These results concern local tests and fixtures, not live equipment. See [verification details](docs/technical-overview.md#verification) and [GitHub Actions](https://github.com/xadecoride/CROC-DREAM-NetOps-Platform/actions).

- [Technical overview, architecture and lab setup](docs/technical-overview.md)
- [Backend architecture](docs/backend-architecture.md)
- [YAML intent format](intent/README.md)
- [Offline lab](lab/README.md)
- [External live-lab setup](docs/live-lab.md)
- [Frontend architecture](frontend/ARCHITECTURE.md)
