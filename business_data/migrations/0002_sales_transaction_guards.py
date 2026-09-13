from django.db import migrations


FORWARD_SQL = """
CREATE FUNCTION business_data_validate_sales_leaf_territory()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    assigned_level text;
BEGIN
    SELECT level INTO assigned_level
    FROM master_data_territory
    WHERE id = NEW.territory_id;

    IF assigned_level IS NOT NULL AND assigned_level <> 'territory' THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Sales transactions require a Territory-level geography.',
            CONSTRAINT = 'business_data_sales_requires_leaf_territory';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER business_data_validate_sales_leaf_territory_trigger
BEFORE INSERT OR UPDATE OF territory_id
ON business_data_salestransaction
FOR EACH ROW
EXECUTE FUNCTION business_data_validate_sales_leaf_territory();

CREATE FUNCTION business_data_prevent_sales_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '23514',
        MESSAGE = 'Sales transactions are immutable; create a correction record.',
        CONSTRAINT = 'business_data_sales_immutable';
END;
$$;

CREATE TRIGGER business_data_prevent_sales_mutation_trigger
BEFORE UPDATE OR DELETE
ON business_data_salestransaction
FOR EACH ROW
EXECUTE FUNCTION business_data_prevent_sales_mutation();
"""


REVERSE_SQL = """
DROP TRIGGER IF EXISTS business_data_prevent_sales_mutation_trigger
ON business_data_salestransaction;
DROP FUNCTION IF EXISTS business_data_prevent_sales_mutation();

DROP TRIGGER IF EXISTS business_data_validate_sales_leaf_territory_trigger
ON business_data_salestransaction;
DROP FUNCTION IF EXISTS business_data_validate_sales_leaf_territory();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("business_data", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
