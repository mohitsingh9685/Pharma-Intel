import io
import tempfile
from datetime import date
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from business_data.models import CalendarDate, SalesTransaction
from master_data.models import (
    Hospital,
    HospitalTerritoryAssignment,
    Product,
    SalesRepresentative,
    SalesRepresentativeTerritoryAssignment,
    Territory,
)

from .contracts import iter_sales_rows_v1_csv
from .synthetic import (
    DEMO_HOSPITAL_CODE,
    DEMO_PRODUCT_CODE,
    DEMO_REPRESENTATIVE_CODE,
    DEMO_SALES_ROWS,
    DEMO_TERRITORY_CODE,
    sales_demo_csv_bytes,
)


@override_settings(DEBUG=True)
class PrepareSalesDemoCommandTests(TestCase):
    def test_command_creates_matching_file_dimensions_and_assignments_idempotently(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "demo.csv"
            first_output = io.StringIO()
            second_output = io.StringIO()

            call_command("prepare_sales_demo", output=output_path, stdout=first_output)
            call_command("prepare_sales_demo", output=output_path, stdout=second_output)

            generated_bytes = output_path.read_bytes()
            results = list(
                iter_sales_rows_v1_csv(
                    io.StringIO(generated_bytes.decode("utf-8")),
                    max_rows=100,
                )
            )
            self.assertEqual(generated_bytes, sales_demo_csv_bytes())
            self.assertEqual(len(results), len(DEMO_SALES_ROWS))
            self.assertTrue(all(result.is_valid for result in results))
            self.assertEqual(
                [result.row_number for result in results],
                list(range(2, 12)),
            )
            self.assertIn("10 rows", first_output.getvalue())
            self.assertIn("already matched", second_output.getvalue())

        product = Product.objects.get(code=DEMO_PRODUCT_CODE)
        territory = Territory.objects.get(code=DEMO_TERRITORY_CODE)
        hospital = Hospital.objects.get(code=DEMO_HOSPITAL_CODE)
        representative = SalesRepresentative.objects.get(
            code=DEMO_REPRESENTATIVE_CODE
        )
        self.assertEqual(product.name, "Demo Medicine")
        self.assertEqual(territory.level, Territory.Level.TERRITORY)
        self.assertEqual(
            CalendarDate.objects.filter(
                date__range=(date(2026, 1, 5), date(2026, 1, 14))
            ).count(),
            10,
        )
        self.assertTrue(
            HospitalTerritoryAssignment.objects.filter(
                hospital=hospital,
                territory=territory,
                effective_from=date(2026, 1, 1),
                effective_to__isnull=True,
            ).exists()
        )
        self.assertTrue(
            SalesRepresentativeTerritoryAssignment.objects.filter(
                sales_representative=representative,
                territory=territory,
                assignment_type=(
                    SalesRepresentativeTerritoryAssignment.AssignmentType.PRIMARY
                ),
                effective_from=date(2026, 1, 1),
                effective_to__isnull=True,
            ).exists()
        )
        self.assertEqual(Product.objects.filter(code=DEMO_PRODUCT_CODE).count(), 1)
        self.assertEqual(SalesTransaction.objects.count(), 0)

    def test_conflicting_output_is_rejected_before_database_changes(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "demo.csv"
            output_path.write_text("different data", encoding="utf-8")

            with self.assertRaisesMessage(CommandError, "different data"):
                call_command("prepare_sales_demo", output=output_path)

        self.assertFalse(Product.objects.filter(code=DEMO_PRODUCT_CODE).exists())
        self.assertFalse(
            Territory.objects.filter(code=DEMO_TERRITORY_CODE).exists()
        )

    @override_settings(DEBUG=False)
    def test_command_is_disabled_outside_debug_mode(self):
        with self.assertRaisesMessage(CommandError, "disabled"):
            call_command("prepare_sales_demo")
