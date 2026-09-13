"""Populate and validate complete years in the reporting calendar."""

from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from business_data.calendar import CALENDAR_FIELD_NAMES, build_calendar_date
from business_data.models import CalendarDate


MAX_YEAR_SPAN = 100
MAX_SUPPORTED_YEAR = 9998
BATCH_SIZE = 1_000
# One transaction-scoped PostgreSQL lock serializes concurrent Calendar runs.
CALENDAR_POPULATION_LOCK_ID = 7_212_025_000_001


def _date_range(start: date, end: date):
    current = start
    while True:
        yield current
        if current == end:
            return
        current += timedelta(days=1)


class Command(BaseCommand):
    help = "Populate and validate complete years in the reporting calendar."

    def add_arguments(self, parser):
        parser.add_argument(
            "--start-year",
            type=int,
            required=True,
            help="First complete Gregorian calendar year to populate.",
        )
        parser.add_argument(
            "--end-year",
            type=int,
            required=True,
            help="Last complete Gregorian calendar year to populate (inclusive).",
        )

    def handle(self, *args, **options):
        start_year = options["start_year"]
        end_year = options["end_year"]
        self._validate_years(start_year, end_year)

        start_date = date(start_year, 1, 1)
        end_date = date(end_year, 12, 31)
        expected_count = (end_date - start_date).days + 1

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    [CALENDAR_POPULATION_LOCK_ID],
                )
            before_count = CalendarDate.objects.filter(
                date__range=(start_date, end_date)
            ).count()
            self._populate(start_date, end_date)
            self._verify(start_date, end_date, expected_count)

        created_count = expected_count - before_count
        self.stdout.write(
            self.style.SUCCESS(
                "Calendar is complete and valid from "
                f"{start_date.isoformat()} through {end_date.isoformat()} "
                f"({expected_count} dates; {created_count} created, "
                f"{before_count} already present)."
            )
        )

    @staticmethod
    def _validate_years(start_year, end_year):
        if not 1 <= start_year <= MAX_SUPPORTED_YEAR:
            raise CommandError(
                f"--start-year must be between 1 and {MAX_SUPPORTED_YEAR}."
            )
        if not 1 <= end_year <= MAX_SUPPORTED_YEAR:
            raise CommandError(
                f"--end-year must be between 1 and {MAX_SUPPORTED_YEAR}."
            )
        if start_year > end_year:
            raise CommandError("--start-year cannot be after --end-year.")
        if (end_year - start_year) + 1 > MAX_YEAR_SPAN:
            raise CommandError(
                f"A single run can populate at most {MAX_YEAR_SPAN} years."
            )

    @staticmethod
    def _populate(start_date, end_date):
        batch = []
        for value in _date_range(start_date, end_date):
            batch.append(CalendarDate(**build_calendar_date(value)))
            if len(batch) == BATCH_SIZE:
                CalendarDate.objects.bulk_create(
                    batch,
                    batch_size=BATCH_SIZE,
                    ignore_conflicts=True,
                )
                batch.clear()

        if batch:
            CalendarDate.objects.bulk_create(
                batch,
                batch_size=BATCH_SIZE,
                ignore_conflicts=True,
            )

    @staticmethod
    def _verify(start_date, end_date, expected_count):
        queryset = CalendarDate.objects.filter(
            date__range=(start_date, end_date)
        ).order_by("date")
        actual_count = queryset.count()
        if actual_count != expected_count:
            raise CommandError(
                "Calendar population did not produce a continuous range: "
                f"expected {expected_count} dates, found {actual_count}."
            )

        stored_rows = queryset.values(*CALENDAR_FIELD_NAMES).iterator(
            chunk_size=BATCH_SIZE
        )
        first_inconsistent_date = None
        inconsistent_fields = None
        for expected_date, stored_values in zip(
            _date_range(start_date, end_date),
            stored_rows,
            strict=True,
        ):
            expected_values = build_calendar_date(expected_date)
            if stored_values != expected_values and inconsistent_fields is None:
                first_inconsistent_date = expected_date
                inconsistent_fields = [
                    field_name
                    for field_name in CALENDAR_FIELD_NAMES
                    if stored_values[field_name] != expected_values[field_name]
                ]

        if inconsistent_fields is not None:
            raise CommandError(
                "Calendar verification failed for "
                f"{first_inconsistent_date.isoformat()}; inconsistent fields: "
                f"{', '.join(inconsistent_fields)}."
            )
