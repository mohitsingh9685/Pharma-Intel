"""Create deterministic local master data and a matching sales CSV."""

import os
import tempfile
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from business_data.calendar import CALENDAR_FIELD_NAMES, build_calendar_date
from business_data.models import CalendarDate
from master_data.models import (
    Hospital,
    HospitalTerritoryAssignment,
    Product,
    SalesRepresentative,
    SalesRepresentativeTerritoryAssignment,
    Territory,
)
from sales_imports.synthetic import (
    DEMO_HOSPITAL_CODE,
    DEMO_PRODUCT_CODE,
    DEMO_REPRESENTATIVE_CODE,
    DEMO_SALES_ROWS,
    DEMO_SOURCE_SYSTEM,
    DEMO_TERRITORY_CODE,
    sales_demo_csv_bytes,
)


DEMO_EFFECTIVE_FROM = date(2026, 1, 1)


def _ensure_master(model, *, code, name, **expected_fields):
    existing = model.objects.filter(code__iexact=code).first()
    if existing is None:
        return model.objects.create(code=code, name=name, **expected_fields), True

    mismatches = []
    if existing.code != code:
        mismatches.append("code")
    if existing.name != name:
        mismatches.append("name")
    for field_name, expected in expected_fields.items():
        if getattr(existing, field_name) != expected:
            mismatches.append(field_name)
    if mismatches:
        raise CommandError(
            f"Existing {model._meta.verbose_name} {code} conflicts in: "
            f"{', '.join(mismatches)}. No existing business data was changed."
        )
    return existing, False


def _ensure_calendar_dates():
    created_count = 0
    for raw_row in DEMO_SALES_ROWS:
        value = date.fromisoformat(raw_row[1])
        expected = build_calendar_date(value)
        calendar_date, created = CalendarDate.objects.get_or_create(
            date=value,
            defaults={key: item for key, item in expected.items() if key != "date"},
        )
        if created:
            created_count += 1
            continue
        inconsistent = [
            field_name
            for field_name in CALENDAR_FIELD_NAMES
            if getattr(calendar_date, field_name) != expected[field_name]
        ]
        if inconsistent:
            raise CommandError(
                f"Calendar date {value.isoformat()} is inconsistent in: "
                f"{', '.join(inconsistent)}."
            )
    return created_count


def _ensure_relationship(model, **values):
    existing = model.objects.filter(**values).first()
    if existing is not None:
        return False
    relationship = model(**values)
    relationship.full_clean()
    relationship.save()
    return True


def _write_unchanged_or_new(path: Path, content: bytes) -> bool:
    if path.exists():
        if not path.is_file():
            raise CommandError(f"The output path is not a file: {path}")
        if path.read_bytes() == content:
            return False
        raise CommandError(
            f"The output file already contains different data: {path}. "
            "Move or rename it before regenerating the demo."
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return True


def _check_existing_output(path: Path, content: bytes) -> bool:
    """Reject a conflicting path before any database records are created."""

    if not path.exists():
        return False
    if not path.is_file():
        raise CommandError(f"The output path is not a file: {path}")
    if path.read_bytes() != content:
        raise CommandError(
            f"The output file already contains different data: {path}. "
            "Move or rename it before regenerating the demo."
        )
    return True


class Command(BaseCommand):
    help = "Create deterministic local demo dimensions and a sales_rows_v1 CSV."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default="samples/sales/sales_rows_v1_demo.csv",
            help="Output path, relative to the project root unless absolute.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "Demo data creation is disabled when DJANGO_DEBUG is false."
            )

        output_path = Path(options["output"]).expanduser()
        if not output_path.is_absolute():
            output_path = settings.BASE_DIR / output_path
        output_path = output_path.resolve()
        csv_content = sales_demo_csv_bytes()
        output_already_matches = _check_existing_output(output_path, csv_content)

        with transaction.atomic():
            product, product_created = _ensure_master(
                Product,
                code=DEMO_PRODUCT_CODE,
                name="Demo Medicine",
                is_active=True,
            )
            territory, territory_created = _ensure_master(
                Territory,
                code=DEMO_TERRITORY_CODE,
                name="Demo Territory",
                is_active=True,
                level=Territory.Level.TERRITORY,
            )
            hospital, hospital_created = _ensure_master(
                Hospital,
                code=DEMO_HOSPITAL_CODE,
                name="Demo Hospital",
                is_active=True,
            )
            representative, representative_created = _ensure_master(
                SalesRepresentative,
                code=DEMO_REPRESENTATIVE_CODE,
                name="Demo Representative",
                is_active=True,
                user=None,
            )
            calendar_created = _ensure_calendar_dates()
            hospital_assignment_created = _ensure_relationship(
                HospitalTerritoryAssignment,
                hospital=hospital,
                territory=territory,
                effective_from=DEMO_EFFECTIVE_FROM,
                effective_to=None,
            )
            representative_assignment_created = _ensure_relationship(
                SalesRepresentativeTerritoryAssignment,
                sales_representative=representative,
                territory=territory,
                assignment_type=(
                    SalesRepresentativeTerritoryAssignment.AssignmentType.PRIMARY
                ),
                effective_from=DEMO_EFFECTIVE_FROM,
                effective_to=None,
            )

        file_created = False
        if not output_already_matches:
            file_created = _write_unchanged_or_new(output_path, csv_content)
        master_created = sum(
            (
                product_created,
                territory_created,
                hospital_created,
                representative_created,
            )
        )
        relationships_created = sum(
            (hospital_assignment_created, representative_assignment_created)
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Sales demo ready: {len(DEMO_SALES_ROWS)} rows at {output_path}. "
                f"Created {master_created} master records, {calendar_created} "
                f"calendar dates, and {relationships_created} assignments. "
                f"Use source system {DEMO_SOURCE_SYSTEM}. "
                f"File {'created' if file_created else 'already matched'}."
            )
        )
