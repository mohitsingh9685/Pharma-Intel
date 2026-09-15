# Business-data foundation: Calendar and Sales

This milestone adds the first reporting-ready business fact and its shared date
dimension. `CalendarDate` describes each business date once. Every
`SalesTransaction` points to one of those dates and to the master-data records
that describe what was sold and where it was sold.

```text
CalendarDate ───────┐
Product ────────────┤
Territory (leaf) ───┼──> SalesTransaction
Hospital (optional) ┤
Sales rep (optional)┘
```

The Calendar is a dimension: it gives reports useful names and groupings for a
date. Sales is a fact: it records a business event and its numeric measures.

## CalendarDate

`business_data.CalendarDate` contains one row for every calendar day in the
configured range. Rows are created for complete years with this idempotent
management command:

```bash
.venv/bin/python manage.py populate_calendar --start-year 2020 --end-year 2030
```

Both years are included, so this example creates every date from 1 January 2020
through 31 December 2030. Running the same command again is safe: existing dates
are retained and missing dates are added. One invocation may cover at most 100
years, which protects against an accidental unbounded insert.

The row stores `date`, a numeric `date_key` in `YYYYMMDD` form, and derived
values used frequently by reports:

- calendar year, quarter, month, day of month, and day of year;
- month name and abbreviation;
- ISO year, ISO week, ISO weekday, weekday name, and weekend flag;
- week, month, quarter, and year boundary dates;
- a sortable year-month key plus year-month and year-quarter labels.

These values are stored instead of recalculated in every report. The command
derives them from the date, including leap days and ISO weeks that cross a
calendar-year boundary. A PostgreSQL trigger derives the same values for direct
inserts, and the database blocks Calendar updates and deletes. Users do not
enter the derived values independently.

## SalesTransaction

`business_data.SalesTransaction` has a deliberately narrow grain: **one row is
one sales line supplied by a source system**. It is not a daily or monthly
summary. Keeping the source grain preserves detail and lets reports aggregate it
in different ways later.

Each row contains:

| Field | Meaning |
| --- | --- |
| `source_system` | Stable name of the system or file contract that produced the line |
| `source_record_id` | Stable identifier for that line inside the source system |
| `calendar_date` | Required business date from `CalendarDate` |
| `product` | Required product sold |
| `territory` | Required leaf Territory where the source attributes the sale |
| `hospital` | Hospital explicitly supplied by the source, or empty when omitted |
| `sales_representative` | Representative explicitly supplied by the source, or empty when omitted |
| `quantity` | Signed decimal quantity |
| `revenue_amount` | Signed decimal revenue amount |
| `currency_code` | Three-letter uppercase currency code for the revenue amount |

Quantity and revenue use decimal database types so values such as `0.1` are
stored predictably. Quantity cannot be zero. A positive quantity with
non-negative revenue represents a sale; zero revenue is allowed for a free sale.
A negative quantity with non-positive revenue represents a return and subtracts
from totals. A return must keep the original currency. Reports must group or
filter revenue by `currency_code`; adding amounts in different currencies is
invalid until a currency-conversion policy is implemented.

The pair `(source_system, source_record_id)` is unique in PostgreSQL. This is
the idempotency key: retrying an import cannot create a second fact for the same
source line. Source systems must therefore supply identifiers that remain stable
across retries.

## Historical attribution

The fact stores its Product and leaf Territory directly. It also stores Hospital
and Sales Representative when those values were explicitly present in the
source. An omitted optional value remains empty; the application does not fill
it from today's master-data relationships.

This makes historical reporting deterministic. If a hospital later moves to a
different territory, or a representative's coverage changes, an older sale
still reports against the attribution recorded on that sale. Effective-dated
master-data relationships remain useful for validation and other historical
questions, but they do not silently rewrite a fact.

References from Sales to Calendar and master data are protected. A referenced
date, product, territory, hospital, or representative cannot be deleted while a
sale depends on it. Master data can be deactivated while its historical facts
remain intact.

When a Hospital or Sales Representative is supplied, shared validation confirms
that it belonged to the selected Territory on the transaction date. This is an
ingestion-time quality rule rather than a permanent database relationship:
authorized corrections to assignment history must not rewrite or invalidate an
already accepted fact. A future bulk importer must call its set-based equivalent
before inserting rows.

## Immutability and future ingestion

Sales rows are historical facts. PostgreSQL rejects updates and deletes, and
Django Admin currently exposes them for read-only review. Admin entry is also
disabled until the staged importer and correction workflow can preserve source
lineage and handle mistakes safely. This prevents a routine UI action from
changing a previously reported total without leaving evidence.

The current foundation establishes the fact grain, references, validations,
and duplicate guard. The original-file intake now stores private S3 originals
and immutable file-level audit metadata. The versioned `sales_rows_v1` contract
and streaming CSV parser define the exact columns and perform bounded syntax and
value validation without database I/O. They do not yet resolve codes or insert
facts. The following workflows still require their own design and are
intentionally deferred:

- background ingestion orchestration and `.xlsx` row parsing;
- database-backed master-data validation, staging, and source-row lineage;
- validation result review and retry handling;
- authorized correction and void/reversal records;
- currency conversion and exchange-rate history.

Until a correction policy is implemented, incorrect production facts must not
be silently overwritten.

## Reporting use

Power BI can relate `SalesTransaction.calendar_date` to `CalendarDate.date` and
use the Calendar columns for year, quarter, month, ISO week, and weekday
filtering. Product and Territory provide the required business breakdowns;
Hospital and Sales Representative add detail when the source supplied it.

Quantities and revenue aggregate with `SUM`, so signed returns reduce the
corresponding totals. Revenue measures must retain a currency filter or grouping.
The direct fact references also keep results stable when current master-data
assignments change.
