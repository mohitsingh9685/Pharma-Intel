from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from master_data.models import (
    Hospital,
    HospitalTerritoryAssignment,
    Product,
    SalesRepresentative,
    SalesRepresentativeTerritoryAssignment,
    Territory,
)

from .validation import validate_sales_dimensions


class SalesDimensionValidationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.product = Product.objects.create(code="PROD-001", name="Product One")
        cls.territory = Territory.objects.create(
            code="TER-001",
            name="Territory One",
            level=Territory.Level.TERRITORY,
        )
        cls.other_territory = Territory.objects.create(
            code="TER-002",
            name="Territory Two",
            level=Territory.Level.TERRITORY,
        )
        cls.region = Territory.objects.create(
            code="REG-001",
            name="Region One",
            level=Territory.Level.REGION,
        )
        cls.hospital = Hospital.objects.create(
            code="HOS-001",
            name="Hospital One",
        )
        cls.representative = SalesRepresentative.objects.create(
            code="REP-001",
            name="Representative One",
        )

    def validate(self, **overrides):
        values = {
            "calendar_date": date(2026, 1, 15),
            "product": self.product,
            "territory": self.territory,
        }
        values.update(overrides)
        return validate_sales_dimensions(**values)

    def test_hospital_assignment_is_valid_on_both_inclusive_boundaries(self):
        HospitalTerritoryAssignment.objects.create(
            hospital=self.hospital,
            territory=self.territory,
            effective_from=date(2026, 1, 1),
            effective_to=date(2026, 1, 31),
        )

        self.assertIsNone(
            self.validate(calendar_date=date(2026, 1, 1), hospital=self.hospital)
        )
        self.assertIsNone(
            self.validate(calendar_date=date(2026, 1, 31), hospital=self.hospital)
        )

    def test_non_leaf_territory_is_rejected(self):
        with self.assertRaises(ValidationError) as context:
            self.validate(territory=self.region)

        self.assertIn("territory", context.exception.message_dict)

    def test_support_representative_assignment_is_accepted(self):
        SalesRepresentativeTerritoryAssignment.objects.create(
            sales_representative=self.representative,
            territory=self.territory,
            assignment_type=(
                SalesRepresentativeTerritoryAssignment.AssignmentType.SUPPORT
            ),
            effective_from=date(2026, 1, 1),
            effective_to=date(2026, 1, 31),
        )

        self.assertIsNone(self.validate(sales_representative=self.representative))

    def test_missing_assignments_report_each_optional_dimension(self):
        with self.assertRaises(ValidationError) as context:
            self.validate(
                hospital=self.hospital,
                sales_representative=self.representative,
            )

        self.assertEqual(
            set(context.exception.message_dict),
            {"hospital", "sales_representative"},
        )

    def test_assignment_to_another_territory_is_rejected(self):
        HospitalTerritoryAssignment.objects.create(
            hospital=self.hospital,
            territory=self.other_territory,
            effective_from=date(2026, 1, 1),
        )

        with self.assertRaises(ValidationError) as context:
            self.validate(hospital=self.hospital)

        self.assertIn("hospital", context.exception.message_dict)

    def test_inactive_historical_identities_remain_valid(self):
        HospitalTerritoryAssignment.objects.create(
            hospital=self.hospital,
            territory=self.territory,
            effective_from=date(2020, 1, 1),
            effective_to=date(2020, 12, 31),
        )
        SalesRepresentativeTerritoryAssignment.objects.create(
            sales_representative=self.representative,
            territory=self.territory,
            assignment_type=(
                SalesRepresentativeTerritoryAssignment.AssignmentType.PRIMARY
            ),
            effective_from=date(2020, 1, 1),
            effective_to=date(2020, 12, 31),
        )
        for record in (self.product, self.territory, self.hospital, self.representative):
            record.is_active = False
            record.save(update_fields=("is_active", "updated_at"))

        self.assertIsNone(
            self.validate(
                calendar_date=date(2020, 6, 1),
                hospital=self.hospital,
                sales_representative=self.representative,
            )
        )
