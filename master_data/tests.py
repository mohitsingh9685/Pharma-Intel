from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from .admin import TerritoryAdmin
from .models import (
    HealthcareProfessional,
    Hospital,
    Product,
    SalesRepresentative,
    Territory,
)


class ProductModelTests(TestCase):
    def test_product_normalizes_code_and_surrounding_whitespace(self):
        product = Product.objects.create(code="  prod-001 ", name="  Product One  ")

        self.assertEqual(product.code, "PROD-001")
        self.assertEqual(product.name, "Product One")
        self.assertTrue(product.is_active)

    def test_product_code_is_unique_ignoring_case(self):
        Product.objects.create(code="PROD-001", name="Product One")

        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.create(code="prod-001", name="Another Product")

    def test_blank_product_code_is_rejected_by_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.create(code="   ", name="Product One")

    def test_string_representation_contains_code_and_name(self):
        product = Product(code="PROD-001", name="Product One")

        self.assertEqual(str(product), "PROD-001 — Product One")


class TerritoryModelTests(TestCase):
    def test_territory_normalizes_code_and_name(self):
        territory = Territory.objects.create(
            code="  north-01 ",
            name="  North Territory  ",
            level=Territory.Level.TERRITORY,
        )

        self.assertEqual(territory.code, "NORTH-01")
        self.assertEqual(territory.name, "North Territory")
        self.assertEqual(territory.level, Territory.Level.TERRITORY)
        self.assertTrue(territory.is_active)

    def test_territory_code_is_unique_ignoring_case(self):
        Territory.objects.create(
            code="NORTH-01",
            name="North Territory",
            level=Territory.Level.TERRITORY,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            Territory.objects.create(
                code="north-01",
                name="Another Territory",
                level=Territory.Level.TERRITORY,
            )

    def test_blank_territory_code_is_rejected_by_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Territory.objects.create(
                code="   ",
                name="North Territory",
                level=Territory.Level.TERRITORY,
            )

    def test_blank_territory_name_is_rejected_by_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Territory.objects.create(
                code="NORTH-01",
                name="   ",
                level=Territory.Level.TERRITORY,
            )

    def test_invalid_territory_level_is_rejected_by_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Territory.objects.create(
                code="NORTH-01",
                name="North Territory",
                level="invalid-level",
            )

    def test_string_representation_contains_code_and_name(self):
        territory = Territory(code="NORTH-01", name="North Territory")

        self.assertEqual(str(territory), "NORTH-01 — North Territory")

    def test_bulk_insert_cannot_bypass_trimmed_code_rule(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Territory.objects.bulk_create(
                [
                    Territory(
                        code=" NORTH-01 ",
                        name="North Territory",
                        level=Territory.Level.TERRITORY,
                    )
                ]
            )

    def test_level_is_required(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Territory.objects.create(code="NORTH-01", name="North Territory")


class TerritoryAdminTests(TestCase):
    def test_code_and_level_are_read_only_after_creation(self):
        territory_admin = TerritoryAdmin(Territory, admin.site)
        territory = Territory(
            code="NORTH-01",
            name="North Territory",
            level=Territory.Level.TERRITORY,
        )

        self.assertNotIn("code", territory_admin.get_readonly_fields(None))
        self.assertNotIn("level", territory_admin.get_readonly_fields(None))
        self.assertIn("code", territory_admin.get_readonly_fields(None, territory))
        self.assertIn("level", territory_admin.get_readonly_fields(None, territory))


class RemainingMasterDataModelTests(TestCase):
    models_under_test = (
        Hospital,
        HealthcareProfessional,
        SalesRepresentative,
    )

    def test_models_normalize_code_and_name(self):
        for index, model in enumerate(self.models_under_test, start=1):
            with self.subTest(model=model.__name__):
                record = model.objects.create(
                    code=f"  code-{index} ",
                    name=f"  Record {index}  ",
                )

                self.assertEqual(record.code, f"CODE-{index}")
                self.assertEqual(record.name, f"Record {index}")
                self.assertTrue(record.is_active)

    def test_models_reject_case_insensitive_duplicate_codes(self):
        for index, model in enumerate(self.models_under_test, start=1):
            with self.subTest(model=model.__name__):
                model.objects.create(code=f"CODE-{index}", name="First record")

                with self.assertRaises(IntegrityError), transaction.atomic():
                    model.objects.create(
                        code=f"code-{index}",
                        name="Second record",
                    )

    def test_models_allow_duplicate_names_with_different_codes(self):
        for index, model in enumerate(self.models_under_test, start=1):
            with self.subTest(model=model.__name__):
                model.objects.create(code=f"FIRST-{index}", name="Shared name")
                model.objects.create(code=f"SECOND-{index}", name="Shared name")

                self.assertEqual(
                    model.objects.filter(name="Shared name").count(),
                    2,
                )

    def test_sales_representative_login_is_optional_unique_and_protected(self):
        user = get_user_model().objects.create_user(
            email="representative@example.com",
            password="test-only-password",
        )
        representative = SalesRepresentative.objects.create(
            code="REP-001",
            name="Representative One",
            user=user,
        )
        SalesRepresentative.objects.create(
            code="REP-002",
            name="Representative Without Login",
        )

        self.assertEqual(representative.user, user)

        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesRepresentative.objects.create(
                code="REP-003",
                name="Duplicate Login Representative",
                user=user,
            )

        with self.assertRaises(ProtectedError):
            user.delete()


class RemainingMasterDataAdminTests(TestCase):
    def test_new_master_data_uses_safe_admin_lifecycle(self):
        for model in (
            Hospital,
            HealthcareProfessional,
            SalesRepresentative,
        ):
            with self.subTest(model=model.__name__):
                model_admin = admin.site._registry[model]
                record = model(code="CODE-001", name="Example record")

                self.assertFalse(model_admin.has_delete_permission(None))
                self.assertNotIn("code", model_admin.get_readonly_fields(None))
                self.assertIn(
                    "code",
                    model_admin.get_readonly_fields(None, record),
                )
