# Pharma Intel project progress

Last updated: 2026-09-15

## Product goal

Pharma Intel is a pharmaceutical commercial decision platform. The planned
system will manage master data, import business data, produce analytics and
recommendations, support what-if scenarios, and provide Power BI reporting.

## Accepted architecture

- Django provides server-rendered pages, authentication, authorization,
  administration, uploads, and business workflows.
- PostgreSQL stores application and business data.
- Docker Compose runs PostgreSQL during local development.
- A PostgreSQL-backed Python worker handles durable CSV imports; later workers
  will handle analytics.
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
- The Calendar and Sales migrations created the shared reporting date dimension,
  immutable source-line Sales facts, reporting indexes, duplicate protection,
  and leaf-Territory database guard successfully.
- All 16 focused relationship tests passed against PostgreSQL.
- All 30 focused Calendar and Sales tests passed against PostgreSQL.
- The complete 105-test project suite passed against PostgreSQL after the
  versioned sales row contract and deterministic demo were added.
- The MFA-protected Terraform role has been verified against AWS.
- The private, versioned, KMS-encrypted Terraform-state bucket is deployed in
  `ap-south-1`; its local bootstrap state was migrated successfully to S3 and a
  follow-up plan reported no changes.
- The separate private, versioned, KMS-encrypted staging sales-import bucket is
  deployed in `ap-south-1` from a reviewed plan containing nine additions and
  no changes or deletions.
- The administrator-only sales-import intake, immutable PostgreSQL audit row,
  bounded CSV/XLSX pre-screen, S3/KMS storage adapter, duplicate guard, and
  interrupted-upload reconciliation command are implemented.
- All eight sales-import migrations applied successfully, Django reported no
  system issues, migration state reported no pending model changes, and the
  complete 175-test suite passed against PostgreSQL. The S3 tests use a strict
  fake; a live runtime-role integration check remains part of deployment.
- The database-independent `sales_rows_v1` streaming CSV parser, deterministic
  ten-row demo CSV, and idempotent development-only demo setup command are
  implemented and verified.
- The durable sales-import queue, leased attempts, staged normalized rows,
  persisted validation issues, exact-version S3 download, database reference
  checks, and all-or-nothing Sales publication are implemented.
- Concurrent workers claim different jobs with `SKIP LOCKED`; expiring leases,
  fencing tokens, heartbeats, bounded retries, and attempt history protect work
  from duplicate or stale completion.
- Each intake records its exact KMS key ARN, so key rotation does not change the
  evidence used to verify older files. A missing or invalid exact S3 version
  permanently fails the intake while preserving its lineage and allows a fresh
  replacement upload.
- The sales-import list and detail pages show separate intake and processing
  status, row counts, latest-attempt errors, and paginated validation issues
  without exposing private S3 lineage.

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

## Calendar and Sales foundation

`CalendarDate` provides one reusable reporting row per date. The
`populate_calendar` command builds and verifies complete years, including leap
days and ISO week-year boundaries. The chosen history and forecast range remains
a deployment decision, so migrations do not populate it silently.

`SalesTransaction` stores one immutable source-system sales line. Each row keeps
its original date, Product, leaf Territory, optional Hospital, optional Sales
Representative, signed decimal measures, and currency. PostgreSQL prevents a
second row with the same source-system/record pair, rejects invalid measure
signs and non-finite numbers, and protects all referenced records. Dated
relationship validation uses the transaction date rather than today's
assignments. Sales remains read-only in Admin until the audited import and
correction workflows are implemented.

## Sales import intake

An Administrator can submit one bounded CSV or XLSX original through the Django
application. PostgreSQL records its uploader, source, format, size, SHA-256,
private S3 location/version, exact KMS key ARN, intake contract, row contract, status, and
timestamps. S3 stores the original with the staging KMS key, object versioning,
checksum, and both contract identifiers as lineage metadata. The web request
does not parse rows; a received CSV is queued for the separate worker.

Duplicate active originals for the same source are prevented by PostgreSQL. An
uncertain S3 response remains `receiving`; `reconcile_sales_imports` checks the
recorded bucket, checksum, size, encryption, version, and metadata before moving
that row to `received` or `failed`. Deployment still needs a dedicated runtime
IAM role and a scheduler for this recovery command.

`sales_rows_v1` defines nine exact sales columns and bounded syntax/value rules.
Its CSV parser streams rows, returns stable issue codes, and reports physical
file line numbers. The worker downloads the recorded S3 version, stages valid
rows, persists bounded issue details, resolves Calendar and master-data codes,
checks dated Territory assignments, and publishes only after the entire file
passes. Identical existing facts are reused; a conflicting source record rejects
the whole file. XLSX remains securely stored in `awaiting_parser` until its row
parser is implemented.

## Configuration flow

```text
manage.py / ASGI / WSGI
    -> config.settings
    -> python-dotenv reads .env
    -> Django loads accounts, master_data, business_data, and sales_imports
    -> psycopg connects to PostgreSQL
```

## Application data flow

```text
Browser
    -> config/urls.py
    -> role-protected Django view and upload form
    -> bounded file inspection + SHA-256
    -> SalesImport audit row in PostgreSQL
    -> original bytes in private S3 with KMS encryption
    -> SalesImportJob in PostgreSQL
    -> process_sales_imports worker
    -> exact-version download + staged validation
    -> atomic SalesTransaction publication or persisted rejection issues
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

`prepare_sales_demo` now creates or verifies reserved local demo master data,
ten matching Calendar dates, two effective assignments, and a deterministic
ten-row `sales_rows_v1` CSV. It is disabled outside debug mode, refuses to
overwrite conflicting records or file bytes, and does not create Sales facts.
The Calendar command remains available when an administrator needs complete
year ranges beyond this narrow demonstration fixture.

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
| `business_data/calendar.py` | Deterministic Calendar field definitions |
| `business_data/models.py` | Calendar dimension and immutable Sales fact |
| `business_data/validation.py` | Dated Sales-to-master-data validation |
| `business_data/admin.py` | Read-only Calendar and safe Sales entry/review |
| `business_data/management/commands/populate_calendar.py` | Complete-year Calendar population and verification |
| `business_data/tests.py` | Sales integrity and lifecycle tests |
| `business_data/test_calendar.py` | Calendar generation and command tests |
| `business_data/test_validation.py` | Dated Sales dimension-validation tests |
| `sales_imports/models.py` | Intake audit, durable job/attempt, staged row, and issue records |
| `sales_imports/validation.py` | Bounded CSV/XLSX intake pre-screen and SHA-256 calculation |
| `sales_imports/contracts.py` | Versioned CSV columns, typed rows, and streaming value validation |
| `sales_imports/synthetic.py` | Deterministic demonstration Sales rows and CSV generation |
| `sales_imports/storage.py` | Private versioned S3/KMS upload and exact-version verified download |
| `sales_imports/services.py` | Duplicate-safe intake orchestration outside long database transactions |
| `sales_imports/queue.py` | Concurrent claims, leases, fencing, retries, and terminal job transitions |
| `sales_imports/processing.py` | Staging, database validation, idempotency, and atomic Sales publication |
| `sales_imports/management/commands/prepare_sales_demo.py` | Safe local demo master data and sample CSV preparation |
| `sales_imports/management/commands/process_sales_imports.py` | Processes a bounded batch of queued CSV imports |
| `sales_imports/management/commands/retry_sales_import.py` | Safely requeues a recoverable terminal CSV job while preserving attempts |
| `sales_imports/management/commands/backfill_sales_import_kms.py` | Verifies and records exact KMS provenance for legacy stored imports |
| `sales_imports/management/commands/reconcile_sales_imports.py` | Recovery for uncertain S3 outcomes |
| `sales_imports/tests.py` | Intake permissions, limits, storage, recovery, and database-integrity tests |
| `sales_imports/test_contract.py` | Fast row-contract and canonical-sample tests |
| `sales_imports/test_demo_command.py` | Demo setup integration and idempotency tests |
| `sales_imports/test_queue.py` | Queue, lease, retry, stale-worker, and concurrent-claim tests |
| `sales_imports/test_storage_download.py` | Exact-version S3 download and integrity tests |
| `sales_imports/test_processing_ui.py` | Processing status and issue-visibility tests |
| `sales_imports/test_processing_audit_guards.py` | Database-level processing audit immutability tests |
| `samples/sales/sales_rows_v1_demo.csv` | Canonical ten-row demonstration upload |
| `docker/postgres/init-app.sql` | Initial local database and application role |
| `scripts/create_local_env.py` | Local Django secret generation |
| `scripts/configure_local_database.py` | Local database credential generation |
| `scripts/check_database.py` | Database identity, privilege, and transaction check |
| `scripts/configure_local_test_database.py` | Dedicated test-role configuration |
| `scripts/render_aws_policy.py` | Renders reviewed IAM templates with the selected AWS account ID |
| `scripts/configure_aws_profiles.py` | Connects the Keychain-backed bootstrap profile to the MFA-protected role |
| `docs/architecture-decisions.md` | Stack, requirements, and open decisions |
| `docs/decisions/0001-single-company-deployment.md` | Deployment-boundary decision |
| `docs/local-testing.md` | Local PostgreSQL test design |
| `docs/product-master-data.md` | Product fields and business rules |
| `docs/territory-master-data.md` | Territory fields and hierarchy-history decision |
| `docs/remaining-master-data.md` | Hospital, HCP, and representative identity rules |
| `docs/master-data-relationships.md` | Relationship dates, cardinality, and lifecycle |
| `docs/decisions/0002-effective-dated-master-data-relationships.md` | Historical-assignment decision |
| `docs/business-data-foundation.md` | Calendar, Sales, and reporting rules |
| `docs/decisions/0003-sales-transaction-grain.md` | Sales grain and attribution decision |
| `docs/decisions/0004-sales-file-row-contract.md` | Sales-file schema, retry, and atomic-publish decision |
| `docs/sales-import-processing.md` | Worker queue, validation, retry, and publish behavior |
| `infra/terraform/iam/` | Reviewed templates for the manually bootstrapped Terraform identity |
| `infra/terraform/bootstrap/` | Creates protected remote-state storage before backend migration |
| `infra/terraform/environments/staging/` | Creates the staging sales-import storage resources |
| `infra/terraform/modules/secure_s3_bucket/` | Shared private, versioned, KMS-encrypted S3 configuration |
| `docs/aws-storage-foundation.md` | AWS access, storage, encryption, and state-bootstrap design |

## Security separation

PostgreSQL roles control database-server privileges. Django roles control what a
person can do inside Pharma Intel. These are separate layers:

- The PostgreSQL administrator initializes the local server.
- `pharma_app` serves normal Django database operations.
- `pharma_test_runner` creates and removes disposable test databases.
- Django Administrator and Business User roles control application features.

## Remaining work

The master-data foundation, Calendar, Sales, original-file intake, and durable
V1 CSV processing are represented. XLSX row parsing, a deployed worker/runtime
IAM role, other business facts, the Sales correction workflow, analytics,
scoring, scenarios, deployment, and Power BI integration remain future work.
