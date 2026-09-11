# Pharma Intel project progress

Last updated: 2026-09-12

## Product goal

Pharma Intel is a pharmaceutical commercial decision platform. The planned
system will manage master data, import business data, produce analytics and
recommendations, support what-if scenarios, and provide Power BI reporting.

## Accepted architecture

- Django provides server-rendered pages, authentication, authorization,
  administration, uploads, and business workflows.
- PostgreSQL stores application and business data.
- Docker Compose runs PostgreSQL during local development.
- Python workers will later handle long-running imports and analytics.
- Power BI will later provide business dashboards.
- Version 1 uses one company per application and database deployment.

## Completed and verified

- Python 3.14.2 local environment.
- Django 6.1.1 and psycopg 3.3.5.
- PostgreSQL 18.6 in a healthy Docker container with persistent storage.
- Private local configuration through an untracked `.env` file.
- Explicit local debug mode so Django serves Admin static assets during
  development; deployed environments must set it to false.
- A restricted `pharma_app` PostgreSQL role for normal application access.
- A separate `pharma_test_runner` role for disposable test databases.
- An email-based custom Django user model.
- Administrator and Business User application roles.
- Django Admin user management and password validation.
- Initial authentication migrations applied successfully.
- A local superuser created successfully.
- Three authentication tests passed against PostgreSQL.
- The product migration applied successfully.
- All four product master-data tests passed against PostgreSQL.

## Product master data

The product model, migration, and Django Admin configuration are complete.
Product records contain a stable code, name, active status, and creation/update
times. The database prevents blank fields and product-code duplicates that differ
only by letter case.

## Configuration flow

```text
manage.py / ASGI / WSGI
    -> config.settings
    -> python-dotenv reads .env
    -> Django loads accounts and master_data
    -> psycopg connects to PostgreSQL
```

## Application data flow

```text
Browser
    -> config/urls.py
    -> Django Admin or future application view
    -> Django form and model validation
    -> Django ORM
    -> psycopg
    -> PostgreSQL container
    -> persistent Docker volume
```

## Generated values and data

Python's cryptographically secure `secrets` module generated the local Django
signing key and PostgreSQL passwords. The fixed local database names, role names,
host, and port were selected as project configuration. None of these values came
from the Django website.

Django generated `accounts/migrations/0001_initial.py` from the user model when
`makemigrations` was run. `migrate` used migration files to create PostgreSQL
tables. The user manually supplied the first administrator email and password;
Django stored a password hash rather than the readable password.

No synthetic or real pharmaceutical business data has been generated yet.

## Main files

| File | Responsibility |
| --- | --- |
| `.env` | Private local settings and passwords; never committed |
| `.env.example` | Safe list of required environment variables |
| `requirements.txt` | Verified Python dependencies |
| `compose.yaml` | PostgreSQL container, health check, port, and volume |
| `config/settings.py` | Django applications, security, and database configuration |
| `config/test_settings.py` | Dedicated local test database credentials |
| `config/urls.py` | Top-level URL routes, including Django Admin |
| `accounts/models.py` | Email-based user and application roles |
| `accounts/managers.py` | Regular-user and superuser creation rules |
| `accounts/forms.py` | Custom-user forms |
| `accounts/admin.py` | User management in Django Admin |
| `accounts/tests.py` | Authentication and uniqueness tests |
| `master_data/models.py` | Product master-data model |
| `master_data/admin.py` | Product management in Django Admin |
| `master_data/tests.py` | Product normalization and constraint tests |
| `docker/postgres/init-app.sql` | Initial local database and application role |
| `scripts/create_local_env.py` | Local Django secret generation |
| `scripts/configure_local_database.py` | Local database credential generation |
| `scripts/check_database.py` | Database identity, privilege, and transaction check |
| `scripts/configure_local_test_database.py` | Dedicated test-role configuration |
| `docs/architecture-decisions.md` | Stack, requirements, and open decisions |
| `docs/decisions/0001-single-company-deployment.md` | Deployment-boundary decision |
| `docs/local-testing.md` | Local PostgreSQL test design |
| `docs/product-master-data.md` | Product fields and business rules |

## Security separation

PostgreSQL roles control database-server privileges. Django roles control what a
person can do inside Pharma Intel. These are separate layers:

- The PostgreSQL administrator initializes the local server.
- `pharma_app` serves normal Django database operations.
- `pharma_test_runner` creates and removes disposable test databases.
- Django Administrator and Business User roles control application features.

## Remaining work

The remaining master data includes territories, hospitals, doctors, and sales
representatives. Business-data imports, background processing, analytics,
scoring, scenarios, deployment, and Power BI integration remain future work.
