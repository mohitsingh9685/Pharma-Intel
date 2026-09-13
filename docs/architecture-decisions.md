# Pharma Commercial Decision Intelligence Platform

## Accepted technology decision

Use Django, PostgreSQL, background Python workers, and Power BI.

| Component | Responsibility |
| --- | --- |
| Django with server-rendered pages | Authentication, authorization, uploads, master-data editing, import review, and scenario controls |
| PostgreSQL | Business data, relational constraints, historical records, and operational metadata |
| Background Python workers | File validation and ingestion, analytics refreshes, scoring, and expensive simulations |
| Power BI | Business dashboards, filters, and drill-downs |

Start with one modular application and separately running workers. A separate
React/Next.js frontend or FastAPI service is not part of the agreed V1 stack.
The worker framework, durable queue, hosting, and Power BI integration method
remain to be selected.

Django fits the application's administration and data-processing workflows.
The application and workers can share Python business logic and validation.
Power BI provides the planned analytical views.

## Accepted deployment boundary

Version 1 uses one company per application and database deployment. Users and
business records belong to that deployment, so the schema does not carry a
company identifier on every row. Separate companies require separate deployments.
The reasoning and consequences are recorded in
`docs/decisions/0001-single-company-deployment.md`.

Master-data hierarchy and affiliation changes use separate effective-dated
records. The cardinality, date, and overlap rules are recorded in
`docs/decisions/0002-effective-dated-master-data-relationships.md`.

The first business-data foundation uses a shared generated Calendar dimension
and immutable source-line Sales facts. Its grain, duplicate key, signed measure
rules, and historical attribution are recorded in
`docs/decisions/0003-sales-transaction-grain.md`.

## Requirements retained from the project plan

- Two roles: Admin and Business User, with multiple users in each role.
- Admins upload CSV/Excel files, manage supported data, edit master data,
  and inspect validation/import results.
- Business users view analytics, drill down, review recommendations, and run
  what-if scenarios.
- External API and database ingestion integrations are outside V1 scope.
- Master data covers doctors, hospitals, products, territories, and sales reps.
- Business data covers sales, prescriptions, visits, targets, and competitor
  activity, supported by a calendar dimension.
- Historical business data is retained. Repeated source records must not create
  duplicate facts. Sales facts are immutable; their authorized correction and
  void workflow remains an open design decision.
- Synthetic data supports development and demonstration; the platform must
  accept changing company data through its supported ingestion workflows.
- Models require a baseline, evaluation, and business justification. Scenario
  outputs describe estimated impact. Synthetic results cannot establish
  real-world predictive accuracy.

## Engineering requirements to make concrete during design

- Shared validation rules for uploads and manual changes, backed by database
  constraints and transactions.
- Staged imports, traceable validation results, and retry-safe job processing.
- Consistent reporting when uploads and analytics refreshes run concurrently.
- Historical reporting that remains correct after master-data assignments change.
- Server-enforced access, audit records, monitored failures, and tested recovery.
- Bounded processing memory, efficient reporting queries, controlled database
  connections, and load tests against agreed capacity and response-time targets.

## Open decisions, in order

1. Expected simultaneous users, data volume, upload size, and upload frequency.
2. Required reporting freshness and acceptable refresh duration.
3. Hosting, identity provider, and Power BI access/licensing approach.
4. Remaining data contracts: non-sales record grains and keys, KPI definitions,
   the sales correction/void workflow, and behavior when an upload contains
   invalid rows.
5. Worker/queue implementation and measurable availability/recovery targets.

Capacity targets must be established before making performance and concurrency
claims.
