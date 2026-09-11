# Product master data

## Purpose

Products are the first pharmaceutical master-data records. Future sales,
prescription, target, competitor, and analytics records will refer to a product
instead of repeating product names in every row.

## Initial data contract

| Field | Meaning |
| --- | --- |
| `code` | Stable company identifier used by uploads and future integrations |
| `name` | Human-readable product name |
| `is_active` | Whether the product may be used for new business activity |
| `created_at` | When the record was created |
| `updated_at` | When the record was last changed |

Normal Django writes trim product codes and store them in uppercase. The database
rejects blank or untrimmed codes and names, plus duplicate codes that differ only
by case or surrounding whitespace.

Django Admin accepts the code during creation and makes it read-only afterward.
The name and active status remain editable.

Products are deactivated instead of deleted through Django Admin. This supports
future historical reporting because old business records must continue to refer
to the same product.

Additional pharmaceutical attributes will be introduced only when their source,
definition, and validation rules are agreed. This avoids storing ambiguous data.

During local development, Django Admin styling is served by
`django.contrib.staticfiles` when `DJANGO_DEBUG=true`. Deployment will keep debug
mode off and serve collected static assets through the selected production web
stack.
