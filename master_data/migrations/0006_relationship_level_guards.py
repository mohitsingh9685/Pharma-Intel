from django.db import migrations


FORWARD_SQL = """
CREATE FUNCTION master_data_validate_territory_hierarchy()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    parent_level text;
    child_level text;
BEGIN
    SELECT level INTO parent_level
    FROM master_data_territory
    WHERE id = NEW.parent_id;

    SELECT level INTO child_level
    FROM master_data_territory
    WHERE id = NEW.child_id;

    IF parent_level IS NOT NULL
       AND child_level IS NOT NULL
       AND (CASE parent_level
               WHEN 'zone' THEN 0
               WHEN 'region' THEN 1
               WHEN 'area' THEN 2
               WHEN 'territory' THEN 3
               ELSE 99
           END) >= (CASE child_level
               WHEN 'zone' THEN 0
               WHEN 'region' THEN 1
               WHEN 'area' THEN 2
               WHEN 'territory' THEN 3
               ELSE -1
           END)
    THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'The hierarchy parent must be above its child.',
            CONSTRAINT = 'master_data_territory_hierarchy_levels_valid';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER master_data_validate_territory_hierarchy_trigger
BEFORE INSERT OR UPDATE OF parent_id, child_id
ON master_data_territoryhierarchyassignment
FOR EACH ROW
EXECUTE FUNCTION master_data_validate_territory_hierarchy();

CREATE FUNCTION master_data_validate_leaf_territory_assignment()
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
            MESSAGE = 'This relationship requires a Territory-level geography.',
            CONSTRAINT = 'master_data_assignment_requires_leaf_territory';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER master_data_validate_hospital_leaf_territory_trigger
BEFORE INSERT OR UPDATE OF territory_id
ON master_data_hospitalterritoryassignment
FOR EACH ROW
EXECUTE FUNCTION master_data_validate_leaf_territory_assignment();

CREATE TRIGGER master_data_validate_sales_rep_leaf_territory_trigger
BEFORE INSERT OR UPDATE OF territory_id
ON master_data_salesrepresentativeterritoryassignment
FOR EACH ROW
EXECUTE FUNCTION master_data_validate_leaf_territory_assignment();

CREATE FUNCTION master_data_protect_referenced_territory_level()
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

CREATE TRIGGER master_data_protect_referenced_territory_level_trigger
BEFORE UPDATE OF level
ON master_data_territory
FOR EACH ROW
EXECUTE FUNCTION master_data_protect_referenced_territory_level();
"""


REVERSE_SQL = """
DROP TRIGGER IF EXISTS master_data_protect_referenced_territory_level_trigger
ON master_data_territory;
DROP FUNCTION IF EXISTS master_data_protect_referenced_territory_level();

DROP TRIGGER IF EXISTS master_data_validate_sales_rep_leaf_territory_trigger
ON master_data_salesrepresentativeterritoryassignment;
DROP TRIGGER IF EXISTS master_data_validate_hospital_leaf_territory_trigger
ON master_data_hospitalterritoryassignment;
DROP FUNCTION IF EXISTS master_data_validate_leaf_territory_assignment();

DROP TRIGGER IF EXISTS master_data_validate_territory_hierarchy_trigger
ON master_data_territoryhierarchyassignment;
DROP FUNCTION IF EXISTS master_data_validate_territory_hierarchy();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("master_data", "0005_master_data_relationships"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
