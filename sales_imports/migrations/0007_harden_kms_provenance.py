from django.db import migrations, models


FORWARD_SQL = """
ALTER TABLE sales_imports_salesimport
ADD CONSTRAINT sales_import_kms_required_new
CHECK (storage_kms_key_arn IS NOT NULL) NOT VALID;

CREATE OR REPLACE FUNCTION sales_imports_protect_audit_row()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    kms_backfill boolean;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Sales import audit records cannot be deleted.',
            CONSTRAINT = 'sales_import_audit_immutable';
    END IF;

    kms_backfill := (
        OLD.status = NEW.status
        AND OLD.storage_kms_key_arn IS NULL
        AND NEW.storage_kms_key_arn IS NOT NULL
    );

    IF NOT (
        (OLD.status = 'receiving' AND NEW.status IN ('received', 'failed'))
        OR (OLD.status = 'received' AND NEW.status = 'failed')
        OR kms_backfill
    ) THEN
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
    ) OR (
        NOT kms_backfill
        AND NEW.storage_kms_key_arn IS DISTINCT FROM OLD.storage_kms_key_arn
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Sales import lineage fields are immutable.',
            CONSTRAINT = 'sales_import_lineage_immutable';
    END IF;

    IF kms_backfill AND ROW(
        NEW.status,
        NEW.failure_code,
        NEW.received_at,
        NEW.failed_at,
        NEW.storage_version_id
    ) IS DISTINCT FROM ROW(
        OLD.status,
        OLD.failure_code,
        OLD.received_at,
        OLD.failed_at,
        OLD.storage_version_id
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'KMS backfill cannot rewrite import status evidence.',
            CONSTRAINT = 'sales_import_kms_backfill_only';
    END IF;

    IF OLD.status = 'receiving'
       AND NEW.status = 'failed'
       AND (
           NEW.received_at IS DISTINCT FROM OLD.received_at
           OR NEW.storage_version_id IS DISTINCT FROM OLD.storage_version_id
       )
    THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'A receiving import cannot fabricate received evidence.',
            CONSTRAINT = 'sales_import_received_evidence_valid';
    END IF;

    IF OLD.status = 'received'
       AND NEW.status = 'failed'
       AND (
           NEW.received_at IS DISTINCT FROM OLD.received_at
           OR NEW.storage_version_id IS DISTINCT FROM OLD.storage_version_id
       )
    THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Received storage evidence is immutable.',
            CONSTRAINT = 'sales_import_storage_evidence_immutable';
    END IF;

    RETURN NEW;
END;
$$;
"""


REVERSE_SQL = """
ALTER TABLE sales_imports_salesimport
DROP CONSTRAINT IF EXISTS sales_import_kms_required_new;

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

    IF NOT (
        (OLD.status = 'receiving' AND NEW.status IN ('received', 'failed'))
        OR (OLD.status = 'received' AND NEW.status = 'failed')
    ) THEN
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
        NEW.storage_kms_key_arn,
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
        OLD.storage_kms_key_arn,
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
        ("sales_imports", "0006_protect_received_storage_evidence"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="salesimport",
            name="sales_import_status_metadata_consistent",
        ),
        migrations.AddConstraint(
            model_name="salesimport",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        status="receiving",
                        received_at__isnull=True,
                        failed_at__isnull=True,
                        storage_version_id="",
                        failure_code="",
                    )
                    | (
                        models.Q(
                            status="received",
                            received_at__isnull=False,
                            failed_at__isnull=True,
                            failure_code="",
                        )
                        & ~models.Q(storage_version_id="")
                    )
                    | (
                        models.Q(status="failed", failed_at__isnull=False)
                        & ~models.Q(failure_code="")
                        & (
                            models.Q(
                                received_at__isnull=True,
                                storage_version_id="",
                            )
                            | (
                                models.Q(
                                    received_at__isnull=False,
                                    failure_code__in=(
                                        "object_missing",
                                        "verification_failed",
                                    ),
                                )
                                & ~models.Q(storage_version_id="")
                            )
                        )
                    )
                ),
                name="sales_import_status_metadata_consistent",
            ),
        ),
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
