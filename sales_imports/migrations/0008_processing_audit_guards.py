from django.db import migrations


FORWARD_SQL = """
CREATE FUNCTION sales_imports_protect_job_audit()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Sales-import jobs cannot be deleted.',
            CONSTRAINT = 'sales_job_audit_delete_denied';
    END IF;

    IF ROW(NEW.sales_import_id, NEW.created_at)
       IS DISTINCT FROM ROW(OLD.sales_import_id, OLD.created_at)
    THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Sales-import job identity is immutable.',
            CONSTRAINT = 'sales_job_identity_immutable';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER sales_imports_protect_job_audit_trigger
BEFORE UPDATE OR DELETE
ON sales_imports_salesimportjob
FOR EACH ROW
EXECUTE FUNCTION sales_imports_protect_job_audit();

CREATE FUNCTION sales_imports_protect_attempt_audit()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Sales-import attempts cannot be deleted.',
            CONSTRAINT = 'sales_attempt_audit_delete_denied';
    END IF;

    IF OLD.outcome <> 'running' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Completed sales-import attempts are immutable.',
            CONSTRAINT = 'sales_attempt_terminal_immutable';
    END IF;

    IF ROW(
        NEW.id,
        NEW.job_id,
        NEW.attempt_number,
        NEW.lease_token,
        NEW.worker_id,
        NEW.started_at
    ) IS DISTINCT FROM ROW(
        OLD.id,
        OLD.job_id,
        OLD.attempt_number,
        OLD.lease_token,
        OLD.worker_id,
        OLD.started_at
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Sales-import attempt identity is immutable.',
            CONSTRAINT = 'sales_attempt_identity_immutable';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER sales_imports_protect_attempt_audit_trigger
BEFORE UPDATE OR DELETE
ON sales_imports_salesimportattempt
FOR EACH ROW
EXECUTE FUNCTION sales_imports_protect_attempt_audit();

CREATE FUNCTION sales_imports_protect_staged_row_audit()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    parent_outcome text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Staged sales-import rows cannot be deleted.',
            CONSTRAINT = 'sales_staged_audit_delete_denied';
    END IF;

    IF OLD.outcome <> 'staged' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Finalized staged sales-import rows are immutable.',
            CONSTRAINT = 'sales_staged_terminal_immutable';
    END IF;

    IF ROW(
        NEW.id,
        NEW.attempt_id,
        NEW.row_number,
        NEW.source_record_id,
        NEW.transaction_date,
        NEW.product_code,
        NEW.territory_code,
        NEW.hospital_code,
        NEW.sales_representative_code,
        NEW.quantity,
        NEW.revenue_amount,
        NEW.currency_code,
        NEW.created_at
    ) IS DISTINCT FROM ROW(
        OLD.id,
        OLD.attempt_id,
        OLD.row_number,
        OLD.source_record_id,
        OLD.transaction_date,
        OLD.product_code,
        OLD.territory_code,
        OLD.hospital_code,
        OLD.sales_representative_code,
        OLD.quantity,
        OLD.revenue_amount,
        OLD.currency_code,
        OLD.created_at
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Staged sales-import source values are immutable.',
            CONSTRAINT = 'sales_staged_values_immutable';
    END IF;

    SELECT outcome
    INTO parent_outcome
    FROM sales_imports_salesimportattempt
    WHERE id = OLD.attempt_id
    FOR SHARE;

    IF parent_outcome IS DISTINCT FROM 'running' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Rows belonging to a completed attempt are immutable.',
            CONSTRAINT = 'sales_staged_attempt_terminal';
    END IF;

    IF NEW.outcome <> 'staged' AND NOT EXISTS (
        SELECT 1
        FROM business_data_salestransaction AS fact
        JOIN sales_imports_salesimportattempt AS attempt
          ON attempt.id = NEW.attempt_id
        JOIN sales_imports_salesimportjob AS job
          ON job.sales_import_id = attempt.job_id
        JOIN sales_imports_salesimport AS intake
          ON intake.id = job.sales_import_id
        WHERE fact.id = NEW.sales_transaction_id
          AND fact.calendar_date_id = NEW.transaction_date
          AND fact.calendar_date_id = NEW.calendar_date_id
          AND fact.product_id = NEW.product_id
          AND fact.territory_id = NEW.territory_id
          AND fact.hospital_id IS NOT DISTINCT FROM NEW.hospital_id
          AND fact.sales_representative_id
              IS NOT DISTINCT FROM NEW.sales_representative_id
          AND fact.quantity = NEW.quantity
          AND fact.revenue_amount = NEW.revenue_amount
          AND fact.currency_code = NEW.currency_code
          AND fact.source_system = intake.source_system
          AND fact.source_record_id = NEW.source_record_id
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'A finalized staged row must link to its matching sales fact.',
            CONSTRAINT = 'sales_staged_transaction_mismatch';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER sales_imports_protect_staged_row_audit_trigger
BEFORE UPDATE OR DELETE
ON sales_imports_salesimportstagedrow
FOR EACH ROW
EXECUTE FUNCTION sales_imports_protect_staged_row_audit();

CREATE FUNCTION sales_imports_protect_issue_audit()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Sales-import issues cannot be deleted.',
            CONSTRAINT = 'sales_issue_audit_delete_denied';
    END IF;

    RAISE EXCEPTION USING
        ERRCODE = '23514',
        MESSAGE = 'Sales-import issues are immutable.',
        CONSTRAINT = 'sales_issue_audit_immutable';
END;
$$;

CREATE TRIGGER sales_imports_protect_issue_audit_trigger
BEFORE UPDATE OR DELETE
ON sales_imports_salesimportissue
FOR EACH ROW
EXECUTE FUNCTION sales_imports_protect_issue_audit();
"""


REVERSE_SQL = """
DROP TRIGGER IF EXISTS sales_imports_protect_issue_audit_trigger
ON sales_imports_salesimportissue;
DROP FUNCTION IF EXISTS sales_imports_protect_issue_audit();

DROP TRIGGER IF EXISTS sales_imports_protect_staged_row_audit_trigger
ON sales_imports_salesimportstagedrow;
DROP FUNCTION IF EXISTS sales_imports_protect_staged_row_audit();

DROP TRIGGER IF EXISTS sales_imports_protect_attempt_audit_trigger
ON sales_imports_salesimportattempt;
DROP FUNCTION IF EXISTS sales_imports_protect_attempt_audit();

DROP TRIGGER IF EXISTS sales_imports_protect_job_audit_trigger
ON sales_imports_salesimportjob;
DROP FUNCTION IF EXISTS sales_imports_protect_job_audit();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("sales_imports", "0007_harden_kms_provenance"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
