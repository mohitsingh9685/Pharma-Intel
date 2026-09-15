import django.db.models.functions.text
from django.db import migrations, models


FORWARD_SQL = """
CREATE FUNCTION sales_imports_protect_audit_row()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Sales import audit records cannot be deleted.',
            CONSTRAINT = 'sales_import_audit_immutable';
    END IF;

    IF OLD.status <> 'receiving'
       OR NEW.status = OLD.status
       OR NEW.status NOT IN ('received', 'failed')
    THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'The sales import status transition is not allowed.',
            CONSTRAINT = 'sales_import_status_transition_valid';
    END IF;

    IF ROW(
        NEW.id,
        NEW.data_contract,
        NEW.source_system,
        NEW.original_filename,
        NEW.content_type,
        NEW.file_format,
        NEW.size_bytes,
        NEW.sha256,
        NEW.storage_bucket,
        NEW.storage_key,
        NEW.uploaded_by_id,
        NEW.created_at
    ) IS DISTINCT FROM ROW(
        OLD.id,
        OLD.data_contract,
        OLD.source_system,
        OLD.original_filename,
        OLD.content_type,
        OLD.file_format,
        OLD.size_bytes,
        OLD.sha256,
        OLD.storage_bucket,
        OLD.storage_key,
        OLD.uploaded_by_id,
        OLD.created_at
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Sales import lineage fields are immutable.',
            CONSTRAINT = 'sales_import_lineage_immutable';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER sales_imports_protect_audit_row_trigger
BEFORE UPDATE OR DELETE
ON sales_imports_salesimport
FOR EACH ROW
EXECUTE FUNCTION sales_imports_protect_audit_row();
"""


REVERSE_SQL = """
DROP TRIGGER IF EXISTS sales_imports_protect_audit_row_trigger
ON sales_imports_salesimport;
DROP FUNCTION IF EXISTS sales_imports_protect_audit_row();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("sales_imports", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="salesimport",
            name="failure_code",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "None"),
                    ("storage_error", "Storage error"),
                    ("object_missing", "Stored object missing"),
                    ("verification_failed", "Storage verification failed"),
                ],
                default="",
                editable=False,
                max_length=32,
            ),
        ),
        migrations.AddConstraint(
            model_name="salesimport",
            constraint=models.CheckConstraint(
                condition=models.Q(("data_contract", "sales_v1")),
                name="sales_import_contract_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="salesimport",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("source_system", django.db.models.functions.text.Trim("source_system"))
                ),
                name="sales_import_source_trimmed",
            ),
        ),
        migrations.AddConstraint(
            model_name="salesimport",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "source_system__regex",
                        "^[A-Z0-9]([A-Z0-9 ._:/-]*[A-Z0-9])?$",
                    )
                ),
                name="sales_import_source_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="salesimport",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(("original_filename__regex", r"^\s*$"))
                    & models.Q(
                        (
                            "original_filename",
                            django.db.models.functions.text.Trim("original_filename"),
                        )
                    )
                ),
                name="sales_import_filename_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="salesimport",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(("storage_bucket", ""))
                    & ~models.Q(("storage_key", ""))
                ),
                name="sales_import_storage_location_set",
            ),
        ),
        migrations.AddConstraint(
            model_name="salesimport",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "failure_code__in",
                        (
                            "",
                            "storage_error",
                            "object_missing",
                            "verification_failed",
                        ),
                    )
                ),
                name="sales_import_failure_code_valid",
            ),
        ),
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
