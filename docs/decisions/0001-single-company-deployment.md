# ADR 0001: One company per deployment

- Status: accepted
- Date: 2026-09-12

## Decision

Version 1 serves one company from each deployed application and database.
Company data therefore does not need a tenant identifier on every table.

## Why

This gives the first release a smaller authorization surface and a clear data
boundary: separate companies use separate application and database deployments.
It reduces the chance of a query accidentally exposing one company's data to
another company.

## Consequences

- Administrator and business-user roles are scoped to one deployment.
- Backups, restores, configuration, and upgrades are managed per company.
- Supporting several companies in one deployment later requires a separate
  architecture decision and an explicit tenant-isolation migration.

