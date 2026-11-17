[English](README.md) | [Русский](README.ru.md)

# CROC DREAM NetOps Platform

A team hackathon prototype for network configuration management: YAML intent validation, vendor-specific rendering, configuration diffs, manually approved deployment and drift monitoring.

**1st Place — CROC “NetOps Platform” Case** · «Телеком Будущее», 2026.

The **CROC DREAM** team had five participants. Daniil Kalinin ([@xadecoride](https://github.com/xadecoride)) was Tech Lead: architecture, task coordination, PR review and shared CI/CD work with the backend developer. The implementation was a team effort; this repository does not represent sole authorship.

## Scope and status

The source contains a planning/deployment pipeline, persisted jobs and snapshots, an offline driver, a live SSH driver, role checks, a React operator interface and scheduled drift tasks. The offline mode is a reproducible file-based lab, not a simulation of every property of a real network OS.

Live-driver code is present, but end-to-end device transactions and vendor-specific health checks require further validation. This is not a claim of production readiness, full six-vendor support or atomic changes across an entire fabric.

## Configuration pipeline

Git/YAML intent → pre-flight lint → Jinja2 render → running-config normalization → hier_config diff (remediation + rollback) → manual approval → transactional apply → post-check → scheduled drift monitoring (Celery Beat)

1. Load inventory and device intent from a checked-out YAML repository; validate schemas and references.
2. Render device configuration with Jinja2 and collect the current running configuration.
3. Normalize configuration text and apply diff filters before calculating remediation and rollback through hier_config.
4. Store the plan, normalized snapshots and a hash of the collected running configuration.
5. Require an operator-approved plan for deployment. Recollect running configuration and reject a stale plan if its hash changed.
6. Apply, post-check and confirm or roll back **per device**. Rollout is sequential and stops after a failure; already confirmed devices are not proven to participate in a fabric-wide atomic rollback.
7. In the Compose deployment, Celery Beat schedules drift scans; the worker compares running configuration with the rendered intent.

Git provides the history of intent changes. A mounted checkout supplies the files; this is not a claim of an automatic Git webhook/reconciliation controller.

## Architecture and stack

```text
React / Monaco operator UI
  -> FastAPI: devices, inventory, plans, jobs and drift reports
  -> PostgreSQL: devices, jobs, plans, snapshots and audit data
  -> Celery worker / Redis broker
       -> intent lint -> Jinja2 -> normalization -> hier_config
       -> offline files OR Scrapli / Paramiko SSH driver
       -> post-check -> confirm / rollback

Celery Beat -> scheduled drift and telemetry tasks
Prometheus / SNMP exporter -> lab monitoring
Telegram bot -> notifications and status commands (optional credentials)
```

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI, Pydantic, SQLAlchemy 2, Alembic, PostgreSQL 16 |
| Background work | Celery, Redis 7, Celery Beat |
| Configuration | Git/YAML, Jinja2, hier_config |
| Device access | Scrapli, scrapli-community, Paramiko; live dependencies are an optional `scrapli` extra |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS 4, Monaco Editor |
| Labs and monitoring | Containerlab, Docker Compose, Prometheus, SNMP exporter, Telegram bot |

The standalone launcher uses SQLite and an in-process threaded dispatcher instead of PostgreSQL/Celery. Grafana is not included in the checked-in Compose stack. Nornir is not a project dependency.

## Quick start

### Standalone development: offline files

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js 22.12+ and npm.

```bash
git clone https://github.com/xadecoride/CROC-DREAM-NetOps-Platform.git
cd CROC-DREAM-NetOps-Platform/backend
uv sync --locked
uv run python run_local.py
```

In another terminal, from the repository root:

```bash
cd frontend
npm ci
npm run dev
```

- UI: http://localhost:5173
- API documentation: http://127.0.0.1:8000/docs
- Health/readiness: `/healthz`, `/readyz`

The launcher initializes SQLite, imports inventory and uses `lab/running/` for device state. It contains development API tokens and lab credential placeholders. Keep it local. It does **not** start Celery Beat, so this mode must not be presented as verification of scheduled drift monitoring.

### Docker Compose: offline pipeline with background tasks

From the repository root, with Docker Compose v2.24+:

```bash
cp backend/.env.example backend/.env
# Review API tokens, SSH profiles and published ports locally.
docker compose config --quiet
docker compose up --build -d
docker compose ps
```

Services: PostgreSQL, Redis, API, worker, Beat, Telegram bot, Prometheus, SNMP exporter and frontend. The API applies Alembic migrations on startup; worker/Beat wait for API readiness. The bot remains idle without its configured credentials.

| Service | Default address |
|---|---|
| UI | http://localhost:5173 |
| API documentation | http://localhost:8000/docs |
| Prometheus | http://localhost:9090 |

```bash
docker compose logs -f api worker beat
docker compose down
```

The default network driver is `offline`. Keep development credentials and published service ports inside a trusted lab. A full container build/startup was not performed for this documentation update.

## Three lab modes

| Mode | Inventory and state | What it establishes |
|---|---|---|
| File-based offline lab | `intent/inventory.yaml`; four device files in `lab/running/` | Pipeline behavior without network devices. Apply/confirm/rollback operate on files; default health is synthetic from intent expectations |
| Local Containerlab | `lab/fabric.clab.yaml`; Cisco spine, Arista spine, two Huawei leaves and two Alpine hosts | A separate six-node topology, **four network devices**. Requires vendor images, appropriate host resources and a matching live inventory |
| External live lab | Separately documented six-network-device topology: two Arista spines, two Cisco leaves, two Huawei leaves | SSH access to an external lab. Requires supplied inventory, credentials, images and vendor-specific acceptance checks |

These topologies are not interchangeable. The local Containerlab node names and roles differ from the external live-lab layout.

### Live configuration prerequisites

There is **no tracked `intent-live/` directory** in this revision. The Compose lab overlay expects it by default; supply your own matching intent checkout rather than treating the overlay as ready out of the box.

1. Provision authorized vendor images and a working Containerlab topology separately.
2. Create live inventory and per-device intent files; verify hostnames, platforms, management addresses, ports and `auth_profile` references.
3. Configure `NETOPS_AUTH_PROFILES` locally in `backend/.env`, never in Git. Live local development also needs `uv sync --extra scrapli`.
4. On the lab host, use the existing `clab_mgmt` network and point `INTENT_LIVE` at the supplied intent directory:

```bash
INTENT_LIVE=/absolute/path/to/live-intent docker compose \
  -f docker-compose.yml -f docker-compose.lab.yml config --quiet
```

After reviewing the merged configuration and the limitations below, replace `config --quiet` with `up -d --build` to start it. The overlay selects the `scrapli` driver and joins SNMP exporter to the Containerlab network. Validate read-only collection and health checks before attempting any deployment.

See [docs/live-lab.md](docs/live-lab.md) for the external topology and image/host notes; that document describes a lab setup, not proof of successful device transactions.

## Vendor matrix

| Platform | Rendering / diff | Live-driver code | Current boundary |
|---|---|---|---|
| Cisco IOS-XE (`cisco_iosxe`) | Templates; hier_config `CISCO_IOS` | Scrapli; configuration, confirm and abort branches | Transaction/session commands must be accepted on the actual OS/image; no live acceptance result is claimed |
| Arista EOS (`arista_eos`) | Templates; `ARISTA_EOS` | Scrapli; named configuration session and timer branches | Session behavior, error handling and reconnect/confirm need live verification |
| Huawei VRP (`huawei_vrp`) | Templates; `HUAWEI_VRP` | scrapli-community/Paramiko; commit and rollback-by-commit-id branches | Running-config collection and post-check still use Cisco-style commands; Huawei parsing helpers are not wired into `snapshot()` |
| Eltex ESR (`eltex_esr`) | Templates exist; `GENERIC` diff mapping | Platform mapping exists | Transactional apply is explicitly blocked; no Eltex node in the provided topology |
| Eltex MES | Template directory only | No implemented platform/driver path established | Template presence is not supported-platform coverage |

Intent substitutions are present in the current templates; the earlier claim that all templates were static is outdated. Some template sections still contain lab-specific addresses and configuration blocks. Render output must be reviewed for each target environment.

## Operator workflow and access

1. Start with the offline driver and synchronize the inventory.
2. Edit device YAML intent, run pre-flight checks and create a dry-run plan.
3. Review intended/running configuration, remediation and rollback in the UI.
4. Approve the plan and deploy only to intended managed devices.
5. Inspect job logs and post-check results before treating the change as complete.
6. To demonstrate drift, change a lab state file and request a drift scan, or use the Compose Beat schedule.

Offline deployment changes tracked files under `lab/running/`. Preserve any state you need and review `git diff -- lab/running`; do not reset unrelated work to clean up a demo.

The backend checks `viewer`, `operator` and `admin` roles. Device changes are blocked for `MONITORING_ONLY` devices until they are promoted to managed mode. The emergency command endpoint restricts command prefixes; the arbitrary-patch endpoint returns 501 rather than bypassing planning.

The UI includes development role tokens, not a complete session-based login. Server-side role checks do not make those demo credentials suitable for a shared deployment.

## Verification

Local run on **18 November 2026**, Python 3.13.14, source revision `d1d8118` before this documentation change:

```bash
cd backend
uv run --frozen pytest --cov=netops
```

**355 passed, 1 skipped; reported coverage 79%.** The skipped test requires `intent-live/inventory.yaml`, which is absent. These results cover local tests and fixtures, not live equipment or full-stack container acceptance.

Additional developer checks:

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

[.github/workflows/ci-cd.yml](.github/workflows/ci-cd.yml) defines backend checks, frontend build, a fixture-based Compose smoke test and conditional GHCR publishing. Workflow definitions are not a claim that a particular hosted run is passing.

## Known limitations

- **Live transactions:** many apply/commit responses are not checked for device-reported failure. Per-device branches need real OS/image validation; the rollout is not established as fabric-wide atomic.
- **Huawei collection and health:** the active path uses `show running-config`, `show ip bgp summary` and `show ip interface brief`. Huawei-specific parser helpers do not fix these call sites.
- **Health fallbacks:** unrecognized ping output can be interpreted as zero loss. Synthetic offline health must not be confused with observed network state.
- **SSH trust:** strict host-key verification is disabled; the Paramiko path accepts unknown keys. This needs correction before use outside a disposable lab.
- **Templates and vendor scope:** lab-specific blocks remain; Eltex ESR deployment is blocked, and MES templates alone do not establish support.
- **Deployment safety:** Huawei rollback commit identifiers are kept in worker memory; interruption/recovery behavior needs explicit testing.
- **Credentials:** development UI/API tokens and lab credential material need review and replacement. Some checked-in diff artifacts contain lab password hashes; they are not evidence that secrets are universally filtered.
- **Monitoring and integration:** Grafana is not in Compose. Frontend Playwright scripts are not wired into the npm scripts or the shown CI workflow.

## Roadmap

- Validate collection, command errors, transaction sessions and rollback on specific Cisco, Arista and Huawei lab images.
- Connect vendor-specific health commands/parsers and fail closed on unrecognized output.
- Remove remaining lab-specific template assumptions and test intent changes against rendered output.
- Decide explicit Eltex ESR/MES scope; add drivers, topology and acceptance tests before expanding the vendor list.
- Enable SSH host-key verification and replace demo authentication with a proper operator login.
- Review lab credential artifacts and external exposure; handle interrupted jobs and rollback state reliably.
- Add full-stack/live acceptance runs and wire browser tests into CI.
- Add Grafana dashboards if required; keep them separate from already implemented Prometheus monitoring.

## Repository and further documentation

```text
backend/          API, services, pipeline, drivers, migrations and tests
frontend/         React operator interface and Monaco diff views
intent/           Offline inventory and per-device YAML intent
templates/        Vendor Jinja2 templates
lab/              Containerlab manifest, golden configs and offline state
monitoring/       Prometheus, alert rules and SNMP exporter configuration
docs/             Architecture, templates and lab notes
scripts/          Diagnostic and lab utilities
```

- [Backend architecture](docs/backend-architecture.md)
- [Backend setup](backend/README.md)
- [Intent format](intent/README.md)
- [Template integration](docs/templates-integration.md)
- [Offline lab](lab/README.md)
- [Frontend architecture](frontend/ARCHITECTURE.md)

Some deeper documents reflect earlier development snapshots. Prefer the current source and the scope/limitations above when interpreting them.
