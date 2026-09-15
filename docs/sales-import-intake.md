# Sales-import intake

## Purpose

Administrators can submit an original UTF-8 CSV or structurally screened `.xlsx`
sales file.
This milestone stores and audits the original file; it does not yet turn file
rows into `SalesTransaction` records.

```text
Administrator
  -> Django upload limit and file inspection
  -> SalesImport row in PostgreSQL (receiving)
  -> private S3 object encrypted with the staging KMS key
  -> S3 size, checksum metadata, encryption, and version verification
  -> SalesImport status becomes received
```

PostgreSQL stores the import reference, source system, original filename,
format, byte count, SHA-256 checksum, uploader, S3 location and version, intake
contract, row contract, status, and timestamps. S3 stores the original file
bytes and both contract identifiers as object metadata. The custom intake pages
do not expose the S3 key, bucket, checksum, or a public download URL.

## `sales_rows_v1` CSV contract

`sales_v1` versions the original-file intake envelope. `sales_rows_v1` separately
versions the rows expected inside that file. Keeping both identifiers means a
future row format can coexist with the current storage and audit workflow.

The V1 CSV contains exactly these headers. Their order may change, but names are
case-sensitive and missing, duplicate, or unknown headers are rejected.

| Column | Required | Rule |
| --- | --- | --- |
| `source_record_id` | Yes | Stable source-line identifier, at most 255 characters |
| `transaction_date` | Yes | Real date in exact `YYYY-MM-DD` form |
| `product_code` | Yes | Product code, at most 64 characters |
| `territory_code` | Yes | Leaf-Territory code, at most 64 characters |
| `hospital_code` | No | Hospital code, at most 64 characters |
| `sales_representative_code` | No | Sales Representative code, at most 64 characters |
| `quantity` | Yes | Decimal with at most 18 digits and 3 decimal places; cannot be zero |
| `revenue_amount` | Yes | Decimal with at most 20 digits and 4 decimal places |
| `currency_code` | Yes | Three ASCII letters, such as `INR` or `USD` |

Values are trimmed. Master-data codes and currency are normalized to uppercase;
`source_record_id` retains its case. Text fields cannot contain control
characters. Decimal values use digits, an optional leading minus sign, and an
optional decimal point; exponent notation, grouping commas, currency symbols,
and a leading plus sign are invalid. Positive quantities require non-negative
revenue, while negative quantities require non-positive revenue. This supports
ordinary sales, free sales, and returns.

The streaming CSV parser skips fully blank rows and counts every other row
toward `SALES_IMPORT_MAX_ROWS`, including a row with the wrong number of cells.
It rejects duplicate source record IDs within one file after trimming. File-level
schema, row-limit, empty-file, and malformed-CSV problems stop parsing; value
problems are returned with their physical CSV row number and stable issue code.

This parser performs database-independent syntax and value validation. It does
not yet resolve master-data codes, check dated assignments, stage rows, or write
`SalesTransaction` records. The upload request also does not run it yet. A
background ingestion worker will perform those steps. `.xlsx` row parsing remains
future work; Excel uploads currently receive only the bounded package pre-screen.

## Safety and recovery

The request handler stops a file when it exceeds the configured limit. CSV is
required to be non-empty UTF-8 text without null bytes. Excel files receive a
bounded ZIP/OOXML structural pre-screen: the package must contain the workbook
members, and it cannot contain macros, unsafe paths, excessive member counts,
encryption flags, or excessive expanded size. A future Excel parser will validate
its XML, sheets, columns, and row values against `sales_rows_v1`.

The checksum, source system, `sales_v1` intake-envelope version, and
`sales_rows_v1` row-contract version form the active duplicate key. A repeated
file for the same source and contracts returns the existing receiving or
received import instead of writing another object. Attempts that reach audit-row
creation remain in the audit trail; validation or missing-configuration failures
happen before a row is created.

An upload first creates a `receiving` row, commits it, then performs S3 network
I/O without holding a database transaction open. A successful upload is marked
`received`. Any uncertain write or confirmation failure stays `receiving`
because a network error can occur after S3 accepted the bytes. The reconciliation
command checks older rows and either recovers their S3 version, records a
missing/invalid object, or leaves a transient AWS error for a later retry. It
verifies the recorded bucket, S3 checksum, size, encryption key, object version,
import reference, intake-envelope metadata, and row-contract metadata.

PostgreSQL constraints enforce normalized lineage values and consistent status
metadata. A row trigger permits only `receiving` to `received` or `failed` and
rejects ordinary SQL `UPDATE` and `DELETE` attempts that bypass Django. The
production runtime role must not own the table or receive `TRUNCATE`, trigger,
or schema-changing privileges; migrations use a separate deployment identity.

## Configuration and operations

The application reads the S3 bucket, KMS key ARN, region, upload ceiling, and
reconciliation grace period from environment settings. AWS access keys are not
application settings. boto3 uses its normal credential chain, which will obtain
temporary credentials from the deployment's dedicated application/worker role.
That runtime role depends on the still-open hosting decision and must not reuse
the Terraform role.

The reconciliation operation is:

```bash
.venv/bin/python manage.py reconcile_sales_imports
```

Until the deployment worker/scheduler milestone, an operator must run this
command after an interrupted or uncertain S3 upload. Deployment must schedule
it, monitor failures, and grant the runtime role `HeadObject` checksum access
plus the required KMS decrypt permission.

## Deterministic local demo

The development-only command below prepares matching local dimensions and a
canonical V1 CSV:

```bash
.venv/bin/python manage.py prepare_sales_demo
```

It creates or reuses one Product, leaf Territory, Hospital, Sales
Representative, ten Calendar dates, and the effective Hospital and
Representative assignments needed by the sample. It writes ten deterministic
rows to `samples/sales/sales_rows_v1_demo.csv`. Use `DEMO-ERP` as the source
system when submitting that file through the upload page.

The sample includes ordinary sales, a free sale, returns, and rows that omit one
or both optional dimensions. Re-running the command reuses exact matching data
and an identical file. It refuses conflicting records or a different file at
the requested output path, and it is disabled when `DJANGO_DEBUG` is false. The
command prepares data and a file; it does not upload the file or create Sales
facts.

The next ingestion milestone will add a durable background worker, database-
backed master-data validation, staged row lineage and issue storage, and
idempotent insertion of accepted `SalesTransaction` rows.
