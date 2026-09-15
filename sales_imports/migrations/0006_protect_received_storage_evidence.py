from django.db import migrations


FORWARD_SQL = """
CREATE FUNCTION sales_imports_protect_storage_evidence()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
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

CREATE TRIGGER sales_imports_protect_storage_evidence_trigger
BEFORE UPDATE
ON sales_imports_salesimport
FOR EACH ROW
EXECUTE FUNCTION sales_imports_protect_storage_evidence();
"""


REVERSE_SQL = """
DROP TRIGGER IF EXISTS sales_imports_protect_storage_evidence_trigger
ON sales_imports_salesimport;
DROP FUNCTION IF EXISTS sales_imports_protect_storage_evidence();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("sales_imports", "0005_storage_kms_provenance"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
