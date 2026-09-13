# Decision 0002: Effective-dated master-data relationships

## Status

Accepted for the version 1 data model.

## Decision

Stable master-data identities and mutable business assignments use separate
tables. Assignment dates are inclusive, may be open-ended, and may contain gaps.
PostgreSQL prevents overlapping periods for relationship combinations that must
be unique on a business date.

- A geography child has at most one higher-level parent.
- A hospital has at most one leaf Territory.
- An HCP may have several hospital affiliations and specialties, with at most
  one primary of each.
- A representative may cover several territories as Primary or Support, while
  each territory has at most one Primary representative.

All relationship foreign keys are protected from deletion. Database triggers
enforce cross-table geography-level rules that ordinary check constraints cannot
express. Historical queries resolve relationships as of the business event date.

## Consequences

Current assignments can change without rewriting older hierarchy results. Date
range conflicts are rejected safely under concurrent writes. Data imports must
use the same model or service validation for hierarchy-level rules because an
ordinary SQL check constraint cannot inspect another table's level.

The correction/void workflow and audit approval policy must be decided before
backdated production corrections are enabled.
