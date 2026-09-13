from django.db import migrations


FORWARD_SQL = """
CREATE OR REPLACE FUNCTION master_data_protect_referenced_territory_level()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.level IS DISTINCT FROM OLD.level THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'A territory level cannot be changed after creation.',
            CONSTRAINT = 'master_data_territory_level_immutable';
    END IF;

    RETURN NEW;
END;
$$;
"""


REVERSE_SQL = """
CREATE OR REPLACE FUNCTION master_data_protect_referenced_territory_level()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.level IS DISTINCT FROM OLD.level
       AND (
           EXISTS (
               SELECT 1
               FROM master_data_territoryhierarchyassignment
               WHERE parent_id = OLD.id OR child_id = OLD.id
           )
           OR EXISTS (
               SELECT 1
               FROM master_data_hospitalterritoryassignment
               WHERE territory_id = OLD.id
           )
           OR EXISTS (
               SELECT 1
               FROM master_data_salesrepresentativeterritoryassignment
               WHERE territory_id = OLD.id
           )
       )
    THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'A referenced territory level cannot be changed.',
            CONSTRAINT = 'master_data_referenced_territory_level_immutable';
    END IF;

    RETURN NEW;
END;
$$;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("master_data", "0006_relationship_level_guards"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
