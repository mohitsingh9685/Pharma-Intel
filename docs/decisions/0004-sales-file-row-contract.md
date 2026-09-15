# ADR 0004: Versioned sales-file row contract

Status: Accepted

## Context

The original-file intake preserves CSV and Excel bytes in S3, but storage alone
does not define how a source row becomes an immutable `SalesTransaction`.
Imports need one explicit schema and predictable failure behavior so retries and
concurrent workers cannot publish partial or contradictory sales totals.

## Decision

The first logical row contract is `sales_rows_v1`. It is stored separately from
the `sales_v1` intake-envelope version on every `SalesImport` and on the matching
S3 object metadata.

V1 requires these nine columns exactly once, in any order:

```text
source_record_id,transaction_date,product_code,territory_code,hospital_code,sales_representative_code,quantity,revenue_amount,currency_code
```

Hospital and Sales Representative values may be blank. All other values are
required. The detailed text, date, decimal, sign, normalization, and size rules
are recorded in `docs/sales-import-intake.md` and implemented by the streaming
CSV contract parser.

Processing will use an all-or-nothing publish rule. A worker will validate the
whole file, including database-backed master-data and effective-date checks,
before inserting Sales facts. If any row is invalid, it will persist the import
and validation results but insert no Sales facts from that file.

Duplicate source IDs inside one file invalidate the file. When a retry meets an
existing `(source_system, source_record_id)` Sales fact, an identical normalized
row will be treated as already applied; different values will be a conflict and
will reject the batch. The final publish will occur in one database transaction.

The implemented parser covers CSV. An Excel parser may use the same logical V1
fields later, but an uploaded `.xlsx` file is not currently row-processed.

## Consequences

- Storage format and business-row format can evolve independently.
- Dashboards cannot observe half of an invalid batch.
- Retry behavior is deterministic and cannot rewrite immutable Sales facts.
- Validation results and worker orchestration still require separate durable
  models before uploaded rows can be published.

