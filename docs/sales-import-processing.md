# Durable sales-import processing

## Flow

The upload request ends after the original file is securely stored and its
intake audit row is confirmed. CSV row work runs separately:

```text
received CSV
  -> PostgreSQL job queue
  -> leased worker attempt
  -> exact S3 version download and verification
  -> streaming contract parse into staged rows and issues
  -> master-data and dated-assignment validation
  -> one atomic SalesTransaction publish
  -> published or rejected result with reviewable counts
```

This separation keeps network and file-processing time out of web requests and
makes interrupted work recoverable.

## Durable records

- `SalesImportJob` is the current state for one intake. It stores availability,
  attempt and lease data, result counts, and the last processing error.
- `SalesImportAttempt` preserves each worker run, including its phase, outcome,
  timestamps, counts, and error.
- `SalesImportStagedRow` stores each syntactically valid normalized CSV row. A
  published row links to the resulting `SalesTransaction`; an identical fact is
  marked as reused and linked to that existing fact.
- `SalesImportIssue` stores stable file/row, column, code, message, and validation
  phase. Stored details are capped by `SALES_IMPORT_MAX_ISSUES`; the job still
  retains the full issue count and a summary says when details were truncated.

Jobs move through `waiting_file`, `queued`, `processing`, and a terminal result.
Temporary failures use `retry_wait`; rejected files keep their validation
issues. XLSX imports use `awaiting_parser` until an Excel row parser is added.
The processing migration also creates the appropriate starting job for every
intake record that existed before this feature.

## Concurrent workers and retries

A worker claims one available row with PostgreSQL row locking and
`SKIP LOCKED`, so another worker can immediately claim a different job. Each
claim receives an expiring lease and a random fencing token. Every heartbeat and
state change must present that token; a worker whose lease was replaced cannot
finish the newer attempt. An expired lease is reclaimed and its old attempt is
recorded as expired.

Transient S3 download and database failures are retried with bounded exponential
backoff, up to the job's attempt limit. Missing exact object versions and failed
integrity checks permanently fail both the worker job and the intake while
preserving its bucket, key, version, checksum, and KMS-key evidence. That removes
the failed intake from the active duplicate guard, so an operator can upload a
fresh replacement without erasing the failed audit record. Storage configuration
errors fail the job but leave the verified intake unchanged for investigation.
An operator can explicitly requeue a recoverable failed or rejected CSV after
fixing its cause. The retry adds to the attempt budget and preserves every prior
attempt and issue. Immutable source-record conflicts cannot be requeued because
the submitted bytes disagree with an existing fact.

## Verification and validation

The worker requests the recorded S3 `VersionId`, not whichever object version is
currently latest. Before parsing, it verifies the bucket/key lineage, version,
content length, S3 checksum, application SHA-256, the KMS key ARN recorded for
that upload, import ID, and contract metadata. Recording the key per intake keeps
historical files verifiable after a configured key rotation. Imports created
before key provenance was added keep a null key and cannot be processed until
the exact S3 metadata is verified and backfilled; the current configured key is
never guessed for historical files.
Bytes are copied in bounded chunks to a temporary seekable file and checked
again while downloading.

CSV contract validation checks the exact V1 columns and typed values. Database
validation then requires:

- a `CalendarDate` for the transaction date;
- an existing Product and leaf-level Territory;
- any supplied Hospital and Sales Representative codes to exist; and
- those optional identities to be assigned to that Territory on the transaction
  date.

Historical rows may reference an identity that is inactive today. Its dated
assignment, rather than its current active flag, determines historical validity.

## Atomic and idempotent publish

Publication runs in one PostgreSQL transaction after taking a session lock for
the source system. The lock is acquired before the repeatable-read snapshot, so
concurrent imports for one source see preceding commits in order. If any row has
a contract, master-data, assignment, or source-record conflict, the job is
rejected and **zero** Sales facts from that file are inserted.

The source lock has a bounded wait and waiting workers renew their leases.
Because it is a PostgreSQL session advisory lock, worker connections must remain
on one database session for the whole job; a transaction-pooling proxy such as
PgBouncer transaction mode is incompatible. Use direct connections or session
pooling for these workers.

`(source_system, source_record_id)` is the idempotency key. An existing fact with
the same values is reused and linked to the staged row. The same key with
different values is a conflict that rejects the complete file. New facts, staged
row links, counters, and the published job result commit together.

## Operation

After migrations are applied and the deployment runtime has its S3/KMS role,
process a bounded number of available CSV jobs from the repository root:

```bash
.venv/bin/python manage.py process_sales_imports --max-jobs 10
```

The default is one job. `--worker-id` may supply a stable diagnostic label;
otherwise the command generates one. Running multiple copies is safe because
claims use PostgreSQL locking and leases. Production must supervise and repeat
this command, monitor failed/rejected jobs, and also schedule
`reconcile_sales_imports` for uncertain uploads.

To retry one recoverable terminal job after its cause is fixed:

```bash
.venv/bin/python manage.py retry_sales_import <sales-import-uuid>
```

For a legacy import that predates per-file KMS provenance, verify its stored
object and record the exact historical key before retrying:

```bash
.venv/bin/python manage.py backfill_sales_import_kms <sales-import-uuid>
```

Use `--additional-attempts N` when more than one new attempt is intentionally
required. The processing command exits nonzero when a job reaches operational
failure, and reconciliation exits nonzero while S3 failures remain deferred, so
a service supervisor can alert instead of treating those runs as healthy.
