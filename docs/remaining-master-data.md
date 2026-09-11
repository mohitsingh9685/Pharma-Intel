# Hospital, Doctor/HCP, and sales-representative master data

## Purpose

These records complete the stable identity layer defined in the project plan.
Future uploads and business transactions can refer to codes instead of repeating
names that may be spelled differently over time.

## Shared identity fields

Hospitals, Doctors/HCPs, and Sales Representatives each contain:

| Field | Meaning |
| --- | --- |
| `code` | Stable company identifier used by files and integrations |
| `name` | Human-readable name |
| `is_active` | Whether new activity may use the record |
| `created_at` | When the record was created |
| `updated_at` | When the record was last changed |

Codes are normalized by normal Django writes and are unique regardless of case
or surrounding whitespace. Database constraints reject blank or untrimmed codes
and names. Different records may have the same name because names do not safely
identify hospitals or people.

Django Admin allows the code during creation and makes it read-only afterward.
Records are deactivated instead of deleted so historical transactions can keep
their original references.

## Sales representative login

A Sales Representative can optionally link to one application User. This keeps
the employee/CRM identity separate from login access because some representatives
will never use the application. One login cannot be linked to two representatives,
and a linked user cannot be deleted while the representative exists.

## Relationships deliberately kept separate

Territory-to-hospital, hospital-to-HCP, HCP-to-specialty, and
territory-to-representative relationships can change. Storing them directly on
the identity records would make old reports use today's assignment.

Those relationships will use separate effective-dated assignment records after
their business rules are agreed. Specialty will be controlled reference data,
not unrestricted text, so analytics do not split one specialty across spelling
variants.
