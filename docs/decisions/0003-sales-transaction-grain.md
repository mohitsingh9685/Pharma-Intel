# Decision 0003: Sales transaction grain and historical attribution

## Status

Accepted for the version 1 sales foundation on 2026-09-14.

## Decision

One `SalesTransaction` row represents one line supplied by a source system. It
is not an application-generated daily or monthly aggregate.

The source line is identified by the database-unique pair
`(source_system, source_record_id)`. This key makes retries idempotent by
preventing the same source line from becoming two facts.

Every sale references a `CalendarDate`, Product, and leaf Territory. A Hospital
and Sales Representative are stored only when explicitly attributed by the
source; an omitted value remains null. Historical facts do not derive those
values later from whichever master-data relationship is current at query time.

Quantity and revenue are decimal, signed measures. Quantity cannot be zero. A
positive quantity with non-negative revenue represents a sale, and a negative
quantity with non-positive revenue represents a return. Zero revenue is allowed
for a free sale. Ordinary sums therefore produce net quantity and net revenue.
Revenue carries a required three-letter uppercase currency code and cannot be
combined across currencies without an explicit conversion policy.

Calendar and master-data foreign keys are protected from deletion. Sales facts
are immutable after creation and cannot be deleted through Django Admin.
PostgreSQL enforces the mutation guard even when a write bypasses Django.

Hospital and Representative relationships are checked against the transaction
date when a fact is accepted. This is an ingestion-time quality rule: later
authorized assignment corrections do not invalidate or rewrite accepted facts.
The future bulk importer must implement the same rule with set-based validation.

`CalendarDate` rows are generated for complete years by
`populate_calendar --start-year ... --end-year ...`. Frequently used calendar
attributes and period boundaries are stored on each row so the same definitions
are reused by Django, PostgreSQL, and Power BI.

## Why

Preserving source-line detail supports reconciliation, future drill-downs, and
new report groupings without re-importing already aggregated data. The stable
source key protects totals when an upload or background job is retried.

Direct historical attribution prevents a later territory, hospital, or
representative assignment change from altering an older sales result. Protected
references and immutable facts preserve the records that produced a reported
number.

A shared, stored Calendar dimension gives all reports the same definitions for
months, quarters, ISO weeks, weekends, and period boundaries. Complete years
avoid missing dates in charts, comparisons, and time-intelligence calculations.

## Consequences

- A source contract must provide a stable system name and record identifier for
  every sales line.
- Returns enter quantity and revenue with negative signs; reports can calculate
  net values with normal sums.
- Reports must keep revenue separated by currency until conversion is designed.
- Optional Hospital or Representative values mean "not supplied by the source,"
  rather than "unknown current assignment."
- Deactivating master data does not remove historical sales, while deleting a
  referenced record is rejected.
- File-level intake lineage is implemented. Batch/row lineage, authorized
  correction or void records, and the review workflow for invalid rows remain
  separate design decisions.
- Django Admin keeps Sales read-only until those ingestion and correction paths
  exist, so a manual typo cannot create an unrepairable production fact.
- Power BI uses `CalendarDate` as its date dimension and aggregates the
  transaction measures at the required reporting level.
