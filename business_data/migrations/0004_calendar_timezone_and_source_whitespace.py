from django.db import migrations


CALENDAR_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION business_data_derive_calendar_date()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    calendar_year integer;
    calendar_quarter integer;
    calendar_month integer;
    calendar_day integer;
    iso_day integer;
BEGIN
    calendar_year := EXTRACT(YEAR FROM NEW.date)::integer;
    calendar_quarter := EXTRACT(QUARTER FROM NEW.date)::integer;
    calendar_month := EXTRACT(MONTH FROM NEW.date)::integer;
    calendar_day := EXTRACT(DAY FROM NEW.date)::integer;
    iso_day := EXTRACT(ISODOW FROM NEW.date)::integer;

    NEW.date_key := calendar_year * 10000 + calendar_month * 100 + calendar_day;
    NEW.year := calendar_year;
    NEW.quarter := calendar_quarter;
    NEW.month := calendar_month;
    NEW.month_name := CASE calendar_month
        WHEN 1 THEN 'January' WHEN 2 THEN 'February' WHEN 3 THEN 'March'
        WHEN 4 THEN 'April' WHEN 5 THEN 'May' WHEN 6 THEN 'June'
        WHEN 7 THEN 'July' WHEN 8 THEN 'August' WHEN 9 THEN 'September'
        WHEN 10 THEN 'October' WHEN 11 THEN 'November' WHEN 12 THEN 'December'
    END;
    NEW.month_abbreviation := CASE calendar_month
        WHEN 1 THEN 'Jan' WHEN 2 THEN 'Feb' WHEN 3 THEN 'Mar'
        WHEN 4 THEN 'Apr' WHEN 5 THEN 'May' WHEN 6 THEN 'Jun'
        WHEN 7 THEN 'Jul' WHEN 8 THEN 'Aug' WHEN 9 THEN 'Sep'
        WHEN 10 THEN 'Oct' WHEN 11 THEN 'Nov' WHEN 12 THEN 'Dec'
    END;
    NEW.day_of_month := calendar_day;
    NEW.day_of_year := EXTRACT(DOY FROM NEW.date)::integer;
    NEW.iso_year := EXTRACT(ISOYEAR FROM NEW.date)::integer;
    NEW.iso_week := EXTRACT(WEEK FROM NEW.date)::integer;
    NEW.iso_weekday := iso_day;
    NEW.weekday_name := CASE iso_day
        WHEN 1 THEN 'Monday' WHEN 2 THEN 'Tuesday' WHEN 3 THEN 'Wednesday'
        WHEN 4 THEN 'Thursday' WHEN 5 THEN 'Friday' WHEN 6 THEN 'Saturday'
        WHEN 7 THEN 'Sunday'
    END;
    NEW.is_weekend := iso_day IN (6, 7);
    NEW.week_start := NEW.date - (iso_day - 1);
    NEW.week_end := NEW.date + (7 - iso_day);
    NEW.month_start := date_trunc(
        'month', NEW.date::timestamp without time zone
    )::date;
    NEW.month_end := (
        date_trunc('month', NEW.date::timestamp without time zone)
        + INTERVAL '1 month - 1 day'
    )::date;
    NEW.quarter_start := date_trunc(
        'quarter', NEW.date::timestamp without time zone
    )::date;
    NEW.quarter_end := (
        date_trunc('quarter', NEW.date::timestamp without time zone)
        + INTERVAL '3 months - 1 day'
    )::date;
    NEW.year_start := date_trunc(
        'year', NEW.date::timestamp without time zone
    )::date;
    NEW.year_end := (
        date_trunc('year', NEW.date::timestamp without time zone)
        + INTERVAL '1 year - 1 day'
    )::date;
    NEW.year_month_key := calendar_year * 100 + calendar_month;
    NEW.year_month_label := NEW.month_abbreviation || ' '
        || lpad(calendar_year::text, 4, '0');
    NEW.year_quarter_label := lpad(calendar_year::text, 4, '0')
        || ' Q' || calendar_quarter::text;

    RETURN NEW;
END;
$$;
"""


CALENDAR_FUNCTION_REVERSE_SQL = """
CREATE OR REPLACE FUNCTION business_data_derive_calendar_date()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    calendar_year integer;
    calendar_quarter integer;
    calendar_month integer;
    calendar_day integer;
    iso_day integer;
BEGIN
    calendar_year := EXTRACT(YEAR FROM NEW.date)::integer;
    calendar_quarter := EXTRACT(QUARTER FROM NEW.date)::integer;
    calendar_month := EXTRACT(MONTH FROM NEW.date)::integer;
    calendar_day := EXTRACT(DAY FROM NEW.date)::integer;
    iso_day := EXTRACT(ISODOW FROM NEW.date)::integer;

    NEW.date_key := calendar_year * 10000 + calendar_month * 100 + calendar_day;
    NEW.year := calendar_year;
    NEW.quarter := calendar_quarter;
    NEW.month := calendar_month;
    NEW.month_name := CASE calendar_month
        WHEN 1 THEN 'January' WHEN 2 THEN 'February' WHEN 3 THEN 'March'
        WHEN 4 THEN 'April' WHEN 5 THEN 'May' WHEN 6 THEN 'June'
        WHEN 7 THEN 'July' WHEN 8 THEN 'August' WHEN 9 THEN 'September'
        WHEN 10 THEN 'October' WHEN 11 THEN 'November' WHEN 12 THEN 'December'
    END;
    NEW.month_abbreviation := CASE calendar_month
        WHEN 1 THEN 'Jan' WHEN 2 THEN 'Feb' WHEN 3 THEN 'Mar'
        WHEN 4 THEN 'Apr' WHEN 5 THEN 'May' WHEN 6 THEN 'Jun'
        WHEN 7 THEN 'Jul' WHEN 8 THEN 'Aug' WHEN 9 THEN 'Sep'
        WHEN 10 THEN 'Oct' WHEN 11 THEN 'Nov' WHEN 12 THEN 'Dec'
    END;
    NEW.day_of_month := calendar_day;
    NEW.day_of_year := EXTRACT(DOY FROM NEW.date)::integer;
    NEW.iso_year := EXTRACT(ISOYEAR FROM NEW.date)::integer;
    NEW.iso_week := EXTRACT(WEEK FROM NEW.date)::integer;
    NEW.iso_weekday := iso_day;
    NEW.weekday_name := CASE iso_day
        WHEN 1 THEN 'Monday' WHEN 2 THEN 'Tuesday' WHEN 3 THEN 'Wednesday'
        WHEN 4 THEN 'Thursday' WHEN 5 THEN 'Friday' WHEN 6 THEN 'Saturday'
        WHEN 7 THEN 'Sunday'
    END;
    NEW.is_weekend := iso_day IN (6, 7);
    NEW.week_start := NEW.date - (iso_day - 1);
    NEW.week_end := NEW.date + (7 - iso_day);
    NEW.month_start := date_trunc('month', NEW.date)::date;
    NEW.month_end := (
        date_trunc('month', NEW.date) + INTERVAL '1 month - 1 day'
    )::date;
    NEW.quarter_start := date_trunc('quarter', NEW.date)::date;
    NEW.quarter_end := (
        date_trunc('quarter', NEW.date) + INTERVAL '3 months - 1 day'
    )::date;
    NEW.year_start := date_trunc('year', NEW.date)::date;
    NEW.year_end := (
        date_trunc('year', NEW.date) + INTERVAL '1 year - 1 day'
    )::date;
    NEW.year_month_key := calendar_year * 100 + calendar_month;
    NEW.year_month_label := NEW.month_abbreviation || ' '
        || lpad(calendar_year::text, 4, '0');
    NEW.year_quarter_label := lpad(calendar_year::text, 4, '0')
        || ' Q' || calendar_quarter::text;

    RETURN NEW;
END;
$$;
"""


REBUILD_EXISTING_CALENDAR_SQL = """
DROP TRIGGER bd_calendar_immutable_trigger
ON business_data_calendardate;

CREATE TRIGGER bd_calendar_derive_update_trigger
BEFORE UPDATE
ON business_data_calendardate
FOR EACH ROW
EXECUTE FUNCTION business_data_derive_calendar_date();

UPDATE business_data_calendardate SET date = date;

DROP TRIGGER bd_calendar_derive_update_trigger
ON business_data_calendardate;

CREATE TRIGGER bd_calendar_immutable_trigger
BEFORE UPDATE OR DELETE
ON business_data_calendardate
FOR EACH ROW
EXECUTE FUNCTION business_data_prevent_calendar_mutation();
"""


SOURCE_WHITESPACE_SQL = """
CREATE FUNCTION business_data_has_python_edge_whitespace(value text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
AS $$
    SELECT value IS DISTINCT FROM btrim(
        value,
        chr(9) || chr(10) || chr(11) || chr(12) || chr(13)
        || chr(28) || chr(29) || chr(30) || chr(31) || chr(32)
        || chr(133) || chr(160) || chr(5760)
        || chr(8192) || chr(8193) || chr(8194) || chr(8195)
        || chr(8196) || chr(8197) || chr(8198) || chr(8199)
        || chr(8200) || chr(8201) || chr(8202)
        || chr(8232) || chr(8233) || chr(8239) || chr(8287) || chr(12288)
    )
$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM business_data_salestransaction
        WHERE business_data_has_python_edge_whitespace(source_system)
           OR business_data_has_python_edge_whitespace(source_record_id)
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Existing Sales source keys contain edge whitespace.',
            CONSTRAINT = 'business_data_sales_source_python_trimmed';
    END IF;
END;
$$;

CREATE FUNCTION business_data_validate_sales_source_whitespace()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF business_data_has_python_edge_whitespace(NEW.source_system)
       OR business_data_has_python_edge_whitespace(NEW.source_record_id)
    THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Sales source keys cannot have leading or trailing whitespace.',
            CONSTRAINT = 'business_data_sales_source_python_trimmed';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER bd_sales_source_whitespace_trigger
BEFORE INSERT OR UPDATE OF source_system, source_record_id
ON business_data_salestransaction
FOR EACH ROW
EXECUTE FUNCTION business_data_validate_sales_source_whitespace();
"""


SOURCE_WHITESPACE_REVERSE_SQL = """
DROP TRIGGER IF EXISTS bd_sales_source_whitespace_trigger
ON business_data_salestransaction;
DROP FUNCTION IF EXISTS business_data_validate_sales_source_whitespace();
DROP FUNCTION IF EXISTS business_data_has_python_edge_whitespace(text);
"""


class Migration(migrations.Migration):
    dependencies = [
        ("business_data", "0003_alter_salestransaction_options_and_more"),
    ]

    operations = [
        migrations.RunSQL(
            sql=CALENDAR_FUNCTION_SQL,
            reverse_sql=CALENDAR_FUNCTION_REVERSE_SQL,
        ),
        migrations.RunSQL(
            sql=REBUILD_EXISTING_CALENDAR_SQL,
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql=SOURCE_WHITESPACE_SQL,
            reverse_sql=SOURCE_WHITESPACE_REVERSE_SQL,
        ),
    ]
