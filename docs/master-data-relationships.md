# Effective-dated master-data relationships

## Purpose

Master-data identities answer who or what a record represents. Relationship
records answer where that identity belonged on a particular business date.
Keeping these concerns separate prevents a current reassignment from changing
older reports.

## Relationship model

```text
Commercial geography parent
    -> Commercial geography child
        -> Hospital
            -> Doctor / HCP

Doctor / HCP -> Specialty
Sales Representative -> Territory
```

The implemented records are:

| Record | Rule during one date period |
| --- | --- |
| Territory hierarchy assignment | A child geography has at most one parent |
| Hospital territory assignment | A hospital belongs to at most one Territory-level geography |
| HCP hospital affiliation | An HCP may use several hospitals; at most one is primary |
| HCP specialty assignment | An HCP may have several specialties; at most one is primary |
| Sales-representative territory assignment | Coverage may be Primary or Support; a territory has at most one Primary representative |

Hierarchy parents must be above their children. The supported order is Zone,
Region, Area, then Territory. Levels may be skipped, so Region can directly own
a Territory as required by the initial project plan.

## Effective dates

`effective_from` and `effective_to` are inclusive business dates. A record from
1 January through 31 January applies on both dates. A replacement may begin on
1 February. A blank end date means the assignment is ongoing.

The shared `effective_on(date)` query applies this rule consistently. Reports
must resolve an assignment using the business event's date, never the current
date.

Current/upcoming/ended labels use the deployment's `DJANGO_TIME_ZONE`. Local
development is configured for `Asia/Kolkata`; each deployment can set its own
business time zone.

PostgreSQL exclusion constraints reject overlapping periods at the database
level, including concurrent writes. The `btree_gist` extension provides the
index operators required to combine identity equality with date-range overlap.
Database triggers also reject invalid hierarchy direction, Hospital/Rep links
to non-Territory levels. Territory levels are immutable after creation, which
prevents concurrent edits from invalidating hierarchy rules.

Admin creation lists active identities only. Shared model validation also rejects
inactive identities in new current or future assignments, while allowing imports
of historical periods that ended before today. Existing assignments remain
editable so they can be closed after an identity is deactivated.

## Lifecycle and corrections

Foreign keys use `PROTECT`, and Django Admin disables physical deletion. Existing
relationship endpoints, classification, and start dates become read-only; users
close an assignment by entering its end date and create a new row for the next
period.

Backdated correction, voiding, and approval/audit rules remain a separate design
decision. Effective dating preserves assignment periods but does not by itself
freeze previously published reports.

Production deployments must preinstall the trusted `btree_gist` extension or
run migrations with a dedicated role allowed to create it. The normal production
web process should not receive database extension privileges.
