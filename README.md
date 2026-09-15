# Pharma Intel

A pharmaceutical commercial analytics and decision-support platform built with
Django, PostgreSQL, background Python workers, and Power BI.

## Current state

The repository contains the Django/PostgreSQL foundation, email-based users,
master data with effective-dated relationships, a reporting Calendar dimension,
immutable source-line Sales facts, and the administrator-facing sales-file
intake. The versioned `sales_rows_v1` contract, streaming CSV value parser, and
deterministic local demo data are also implemented. Django 6.1.1, PostgreSQL
18.6, and psycopg 3.3.5 are verified locally. Background ingestion, XLSX row
parsing, analytics, workers, dashboards, deployment, and measurable capacity
targets remain future milestones, so this is not yet a production deployment.

See [the architecture decisions](docs/architecture-decisions.md) for the agreed
scope and open questions, including company isolation and expected capacity.

## Local setup

Use Python 3.14. Run commands from the repository root. If you already have the
project's virtual environment, activate it instead of creating it again.

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/create_local_env.py
python scripts/configure_local_database.py
unset DJANGO_SECRET_KEY
docker compose config --quiet
docker compose up -d --wait --wait-timeout 120
python manage.py check
python scripts/check_database.py
python manage.py migrate
python -m pip check
```

The environment creation script writes a random development signing key to a
private `.env` file and refuses to overwrite an existing file. Run that script
only for the initial setup. Existing process environment variables take priority
over `.env`; `unset` removes a temporary shell key left over from earlier setup.

Keep `.env` private. `.env.example` documents the required variable and contains
no working secret. The `.gitignore` excludes `.env`, the virtual environment,
and generated Python caches.

The Django check validates configuration. The separate database check verifies
an actual connection, the application role's administrative privilege flags,
and a temporary write/read operation that is rolled back. Neither check proves
production readiness or concurrency capacity.

## Local PostgreSQL

Start Docker Desktop before running Compose. The database is reachable from
this Mac at `127.0.0.1:5433`; port 5433 avoids the common local PostgreSQL port
5432 and can be changed through `DATABASE_PORT` in `.env`.

Compose uses the exact digest of the official image downloaded by the user.
The named volume is mounted at `/var/lib/postgresql`, matching the versioned
data layout used by PostgreSQL 18 and newer official images. Database files are
outside Git and persist when the container is stopped or recreated.

The initial SQL creates `pharma_intel` and a separate `pharma_app` login.
Django uses this login instead of the `postgres` administrator. The local app
role owns its database so it can run migrations, but cannot create databases,
manage roles, replicate, or act as a superuser. Production runtime and migration
roles will be separated before deployment.

`configure_local_database.py` generates missing passwords, preserves existing
values and the Django key, and updates `.env` with owner-only permissions.
Concurrent setup runs are serialized, and configuration replacement is atomic.
The helper supports macOS/Linux. Real environment variables override `.env` in
both Django and Compose; remove stale overrides if configuration disagrees.

Initialization SQL and the container's initialization passwords apply only to a
new, empty volume. Editing `.env` does not rotate passwords in an existing
database. Preserve the volume and use a deliberate database password change if
credentials need updating; do not regenerate credentials or delete data to fix
an authentication error.

Useful commands from the repository root:

```bash
docker compose ps
docker compose stop
docker compose up -d --wait --wait-timeout 120
```

Container health reports server readiness. Run `python scripts/check_database.py`
to establish that Django can authenticate and use its database. Use
`docker compose config --quiet` to validate Compose without printing passwords.

## Sales contract demo

The following development-only command creates the local master data and dates
needed by the deterministic sales sample:

```bash
.venv/bin/python manage.py prepare_sales_demo
```

It creates or verifies `samples/sales/sales_rows_v1_demo.csv`. Submit that file
through the sales-import page with source system `DEMO-ERP`. This exercises the
original-file intake; background row ingestion is not connected yet.

## File connections

| File | Purpose |
| --- | --- |
| `requirements.txt` | Exact runtime dependency versions verified during setup |
| `manage.py` | Entry point for project management commands |
| `config/settings.py` | Loads local configuration and defines Django settings |
| `config/urls.py` | Top-level URL routes, including Django Admin |
| `config/asgi.py`, `config/wsgi.py` | Entry points used by compatible web servers |
| `scripts/create_local_env.py` | Creates the private local configuration once |
| `scripts/configure_local_database.py` | Adds missing database credentials safely |
| `compose.yaml` | Runs the pinned local PostgreSQL image with persistent storage |
| `docker/postgres/init-app.sql` | Creates the database and application login on first initialization |
| `scripts/check_database.py` | Verifies Django's database connection and a rolled-back write |
| `accounts/` | Email-based users, roles, forms, Admin, and tests |
| `master_data/` | Commercial identities and effective-dated relationships |
| `business_data/` | Reporting Calendar and immutable source-line Sales facts |
| `sales_imports/` | Original-file intake, the V1 row contract, CSV validation, S3 storage, audit, and recovery |
| `sales_imports/contracts.py` | Exact `sales_rows_v1` columns and database-independent CSV row validation |
| `sales_imports/synthetic.py` | Deterministic V1 demonstration rows and CSV bytes |
| `sales_imports/management/commands/prepare_sales_demo.py` | Safe local master-data and sample-file preparation |
| `samples/sales/sales_rows_v1_demo.csv` | Canonical ten-row demonstration file |
| `docs/business-data-foundation.md` | Calendar population and Sales data rules |
| `docs/sales-import-intake.md` | Sales-file intake flow, safeguards, and recovery behavior |
| `docs/decisions/0004-sales-file-row-contract.md` | Versioned Sales row schema and atomic import decision |
| `.env.example` | Shareable example of the required local configuration |
| `AGENTS.md` | Learning workflow and engineering standards |

## Development workflow

The user runs terminal commands; the assistant prepares file changes and explains
them. Group closely related work into coherent steps and review the relevant
check results. Commit and push after the full initial setup is complete, then
after each completed feature. Individual setup steps and lessons do not require
a Git checkpoint.

For new dependency selections and upgrades, prefer the latest stable compatible
releases, verify the resolved versions, and record exact pins. Re-run relevant
checks after an upgrade and include the changes in the next setup or feature
checkpoint.
