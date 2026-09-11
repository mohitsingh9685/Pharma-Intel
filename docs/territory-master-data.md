# Territory master data

## Purpose

Territories provide stable commercial geography identities for future hospitals,
doctors, sales representatives, targets, visits, and reporting.

## Initial data contract

| Field | Meaning |
| --- | --- |
| `code` | Stable company identifier used by uploads and integrations |
| `name` | Human-readable commercial geography name |
| `level` | Required classification: Zone, Region, Area, or Territory |
| `is_active` | Whether new business activity may use the record |
| `created_at` | When the record was created |
| `updated_at` | When the record was last changed |

Normal Django writes trim codes and store them in uppercase. The database
rejects surrounding whitespace, blank codes, blank names, duplicate codes that
differ only by case or whitespace, and unknown or missing levels. Django Admin
supports search and filters by level and active status.

Codes and levels are editable while a record is first created. Django Admin
makes them read-only afterward because changing either value would change the
record's identity. Names and active status remain editable.

## Why parent relationships are not stored here

A direct `parent` field would describe only the current hierarchy. Reassigning
an Area from one Region to another would then make old reports appear as if the
new hierarchy had always existed.

Hierarchy links will therefore be modeled separately as effective-dated
assignments after the historical-assignment and correction policies are agreed.
The stable Territory identities introduced here will not need to be replaced.

Territories are deactivated instead of deleted through Django Admin so future
historical records can retain their references.
