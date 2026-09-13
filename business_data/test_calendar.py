from datetime import date
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase, TestCase

from .calendar import build_calendar_date
from .models import CalendarDate


class CalendarBuilderTests(SimpleTestCase):
    def test_leap_day_values_and_period_boundaries(self):
        values = build_calendar_date(date(2024, 2, 29))

        self.assertEqual(values["date_key"], 20240229)
        self.assertEqual(values["year"], 2024)
        self.assertEqual(values["quarter"], 1)
        self.assertEqual(values["month"], 2)
        self.assertEqual(values["month_name"], "February")
        self.assertEqual(values["month_abbreviation"], "Feb")
        self.assertEqual(values["day_of_month"], 29)
        self.assertEqual(values["day_of_year"], 60)
        self.assertEqual(values["weekday_name"], "Thursday")
        self.assertFalse(values["is_weekend"])
        self.assertEqual(values["week_start"], date(2024, 2, 26))
        self.assertEqual(values["week_end"], date(2024, 3, 3))
        self.assertEqual(values["month_start"], date(2024, 2, 1))
        self.assertEqual(values["month_end"], date(2024, 2, 29))
        self.assertEqual(values["quarter_start"], date(2024, 1, 1))
        self.assertEqual(values["quarter_end"], date(2024, 3, 31))
        self.assertEqual(values["year_start"], date(2024, 1, 1))
        self.assertEqual(values["year_end"], date(2024, 12, 31))
        self.assertEqual(values["year_month_key"], 202402)
        self.assertEqual(values["year_month_label"], "Feb 2024")
        self.assertEqual(values["year_quarter_label"], "2024 Q1")

    def test_iso_week_rolls_into_previous_iso_year(self):
        values = build_calendar_date(date(2021, 1, 1))

        self.assertEqual(values["year"], 2021)
        self.assertEqual(values["iso_year"], 2020)
        self.assertEqual(values["iso_week"], 53)
        self.assertEqual(values["iso_weekday"], 5)
        self.assertEqual(values["week_start"], date(2020, 12, 28))
        self.assertEqual(values["week_end"], date(2021, 1, 3))

    def test_weekend_and_last_quarter_boundaries(self):
        values = build_calendar_date(date(2023, 12, 31))

        self.assertEqual(values["quarter"], 4)
        self.assertEqual(values["iso_weekday"], 7)
        self.assertTrue(values["is_weekend"])
        self.assertEqual(values["month_end"], date(2023, 12, 31))
        self.assertEqual(values["quarter_end"], date(2023, 12, 31))
        self.assertEqual(values["year_end"], date(2023, 12, 31))


class PopulateCalendarCommandTests(TestCase):
    def run_command(self, start_year, end_year):
        output = StringIO()
        call_command(
            "populate_calendar",
            start_year=start_year,
            end_year=end_year,
            stdout=output,
        )
        return output.getvalue()

    def test_populates_a_complete_leap_year_and_is_idempotent(self):
        first_output = self.run_command(2024, 2024)

        self.assertEqual(CalendarDate.objects.count(), 366)
        self.assertTrue(CalendarDate.objects.filter(date=date(2024, 2, 29)).exists())
        self.assertIn("366 dates; 366 created, 0 already present", first_output)

        second_output = self.run_command(2024, 2024)

        self.assertEqual(CalendarDate.objects.count(), 366)
        self.assertIn("366 dates; 0 created, 366 already present", second_output)

    def test_database_prevents_calendar_update_and_delete(self):
        self.run_command(2023, 2023)

        with self.assertRaises(IntegrityError), transaction.atomic():
            CalendarDate.objects.filter(date=date(2023, 6, 15)).update(
                month_name="Incorrect"
            )

        with self.assertRaises(IntegrityError), transaction.atomic():
            CalendarDate.objects.filter(date=date(2023, 6, 15)).delete()

    def test_database_derives_fields_for_direct_bulk_insert(self):
        values = build_calendar_date(date(2023, 6, 15))
        values["month_name"] = "Incorrect"
        values["day_of_year"] = 1
        CalendarDate.objects.bulk_create(
            [CalendarDate(**values)]
        )

        record = CalendarDate.objects.get(date=date(2023, 6, 15))
        self.assertEqual(record.month_name, "June")
        self.assertEqual(record.day_of_year, 166)

    def test_database_calendar_derivation_ignores_session_timezone(self):
        values = build_calendar_date(date(1994, 12, 31))
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL TIME ZONE 'Pacific/Kiritimati'")
        CalendarDate.objects.bulk_create([CalendarDate(**values)])

        record = CalendarDate.objects.get(date=date(1994, 12, 31))
        self.assertEqual(record.month_start, date(1994, 12, 1))
        self.assertEqual(record.month_end, date(1994, 12, 31))
        self.assertEqual(record.quarter_start, date(1994, 10, 1))
        self.assertEqual(record.year_start, date(1994, 1, 1))

    def test_rejects_invalid_year(self):
        with self.assertRaisesMessage(CommandError, "--start-year must be between"):
            self.run_command(0, 2024)

    def test_rejects_reversed_years(self):
        with self.assertRaisesMessage(
            CommandError,
            "--start-year cannot be after --end-year",
        ):
            self.run_command(2025, 2024)

    def test_rejects_ranges_over_one_hundred_years(self):
        with self.assertRaisesMessage(CommandError, "at most 100 years"):
            self.run_command(2000, 2100)
