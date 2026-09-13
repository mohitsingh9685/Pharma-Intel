# Pharma Intel project progress

Last updated: 2026-09-13

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
- Both territory and master-data integrity migrations applied successfully.
- The Hospital, Doctor/HCP, and Sales Representative migration applied
  successfully.
- The relationship migrations installed `btree_gist`, created five dated
  assignment tables, installed cross-table geography guards, and made Territory
  levels immutable successfully.
- All 16 focused relationship tests passed against PostgreSQL.
- The complete 37-test project suite passed against PostgreSQL.

## Product master data

The product model, migration, and Django Admin configuration are complete.
Product records contain a stable code, name, active status, and creation/update
times. The database prevents blank fields and product-code duplicates that differ
only by letter case.

## Territory master data

The territory-identity model, migration, and Django Admin configuration are
complete. Territory records contain a stable code, name, required commercial
level, active status, and creation/update times. The database prevents blank or
untrimmed fields, invalid levels, and territory-code duplicates that differ only
by letter case or whitespace. Codes and levels cannot be edited through Admin
after creation, and the database makes levels immutable. Hierarchy relationships
are stored separately with effective dates so changes do not rewrite historical
reporting.

## Remaining master-data identities

Hospital, Doctor/HCP, and Sales Representative identities are implemented with
the same stable-code, lifecycle, normalization, and database-integrity rules.
A Sales Representative may optionally link to one application login. Mutable
territory, hospital, and specialty relationships remain separate so they can be
effective-dated without changing historical results.

## Effective-dated master-data relationships

Controlled Specialties and five dated relationship types now connect geography,
hospitals, Doctors/HCPs, specialties, and Sales Representatives. Inclusive date
ranges retain assignment history. PostgreSQL exclusion constraints prevent
invalid overlaps during concurrent writes, and Admin provides searchable pages
for maintaining and reviewing each relationship type.

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

No automatic sample-data generator has been added. The records currently in the
database were entered manually through Django Admin.

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
| `master_data/models.py` | Shared rules and all five planned master-data identities |
| `master_data/admin.py` | Master-data management in Django Admin |
| `master_data/tests.py` | Master-data normalization, integrity, and lifecycle tests |
| `master_data/test_relationships.py` | Historical relationship and overlap tests |
| `docker/postgres/init-app.sql` | Initial local database and application role |
| `scripts/create_local_env.py` | Local Django secret generation |
| `scripts/configure_local_database.py` | Local database credential generation |
| `scripts/check_database.py` | Database identity, privilege, and transaction check |
| `scripts/configure_local_test_database.py` | Dedicated test-role configuration |
| `docs/architecture-decisions.md` | Stack, requirements, and open decisions |
| `docs/decisions/0001-single-company-deployment.md` | Deployment-boundary decision |
| `docs/local-testing.md` | Local PostgreSQL test design |
| `docs/product-master-data.md` | Product fields and business rules |
| `docs/territory-master-data.md` | Territory fields and hierarchy-history decision |
| `docs/remaining-master-data.md` | Hospital, HCP, and representative identity rules |
| `docs/master-data-relationships.md` | Relationship dates, cardinality, and lifecycle |
| `docs/decisions/0002-effective-dated-master-data-relationships.md` | Historical-assignment decision |

## Security separation

PostgreSQL roles control database-server privileges. Django roles control what a
person can do inside Pharma Intel. These are separate layers:

- The PostgreSQL administrator initializes the local server.
- `pharma_app` serves normal Django database operations.
- `pharma_test_runner` creates and removes disposable test databases.
- Django Administrator and Business User roles control application features.

## Remaining work

The master-data identities, controlled specialties, hierarchy, and affiliation
foundations are now represented. Business transaction models, correction/audit
workflow, imports, background processing, analytics, scoring, scenarios,
deployment, and Power BI integration remain future work.
