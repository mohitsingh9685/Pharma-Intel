from django.db import migrations, models


FORWARD_SQL = """
CREATE OR REPLACE FUNCTION sales_imports_protect_audit_row()
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
        NEW.row_contract,
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
        OLD.row_contract,
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
"""


REVERSE_SQL = """
CREATE OR REPLACE FUNCTION sales_imports_protect_audit_row()
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
"""


class Migration(migrations.Migration):
    dependencies = [
        ("sales_imports", "0002_integrity_guards"),
    ]

    operations = [
        migrations.AddField(
            model_name="salesimport",
            name="row_contract",
            field=models.CharField(
                choices=[("sales_rows_v1", "Sales rows v1")],
                default="sales_rows_v1",
                editable=False,
                max_length=32,
            ),
        ),
        migrations.AddConstraint(
            model_name="salesimport",
            constraint=models.CheckConstraint(
                condition=models.Q(row_contract="sales_rows_v1"),
                name="sales_import_row_contract_valid",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="salesimport",
            name="sales_import_active_file_unique",
        ),
        migrations.AddConstraint(
            model_name="salesimport",
            constraint=models.UniqueConstraint(
                models.F("data_contract"),
                models.F("row_contract"),
                models.F("source_system"),
                models.F("sha256"),
                condition=models.Q(status__in=("receiving", "received")),
                name="sales_import_active_file_unique",
            ),
        ),
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
