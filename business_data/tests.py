from datetime import date
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse

from master_data.models import Product, Territory

from .admin import CalendarDateAdmin, SalesTransactionAdmin
from .models import CalendarDate, SalesTransaction


class SalesTransactionFixtures(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.calendar_date = CalendarDate.for_date(date(2026, 9, 13))
        cls.calendar_date.save()
        cls.product = Product.objects.create(code="PROD-001", name="Product One")
        cls.territory = Territory.objects.create(
            code="TER-001",
            name="Territory One",
            level=Territory.Level.TERRITORY,
        )
        cls.region = Territory.objects.create(
            code="REG-001",
            name="Region One",
            level=Territory.Level.REGION,
        )

    def sales_values(self, **overrides):
        values = {
            "calendar_date": self.calendar_date,
            "product": self.product,
            "territory": self.territory,
            "quantity": Decimal("10.000"),
            "revenue_amount": Decimal("1250.0000"),
            "currency_code": "INR",
            "source_system": "ERP",
            "source_record_id": "INV-001-LINE-1",
        }
        values.update(overrides)
        return values


class SalesTransactionModelTests(SalesTransactionFixtures):
    def test_source_and_currency_values_are_normalized(self):
        sale = SalesTransaction.objects.create(
            **self.sales_values(
                source_system="  erp  ",
                source_record_id="  Invoice-A-Line-1  ",
                currency_code=" inr ",
            )
        )

        self.assertEqual(sale.source_system, "ERP")
        self.assertEqual(sale.source_record_id, "Invoice-A-Line-1")
        self.assertEqual(sale.currency_code, "INR")

    def test_same_source_record_is_rejected_but_other_source_is_allowed(self):
        SalesTransaction.objects.create(**self.sales_values())

        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesTransaction.objects.create(**self.sales_values())

        SalesTransaction.objects.create(
            **self.sales_values(source_system="DISTRIBUTOR")
        )
        self.assertEqual(SalesTransaction.objects.count(), 2)

    def test_sale_return_and_free_sale_sign_rules(self):
        SalesTransaction.objects.create(
            **self.sales_values(revenue_amount=Decimal("0"))
        )
        SalesTransaction.objects.create(
            **self.sales_values(
                source_record_id="RETURN-001",
                quantity=Decimal("-2"),
                revenue_amount=Decimal("-250"),
            )
        )

        invalid_values = (
            self.sales_values(
                source_record_id="ZERO-QTY",
                quantity=Decimal("0"),
                revenue_amount=Decimal("0"),
            ),
            self.sales_values(
                source_record_id="BAD-SALE-SIGN",
                quantity=Decimal("1"),
                revenue_amount=Decimal("-1"),
            ),
            self.sales_values(
                source_record_id="BAD-RETURN-SIGN",
                quantity=Decimal("-1"),
                revenue_amount=Decimal("1"),
            ),
        )
        for values in invalid_values:
            with self.subTest(source_record_id=values["source_record_id"]):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    SalesTransaction.objects.bulk_create(
                        [SalesTransaction(**values)]
                    )

    def test_invalid_currency_format_is_rejected_by_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesTransaction.objects.bulk_create(
                [SalesTransaction(**self.sales_values(currency_code="inr"))]
            )

    def test_edge_whitespace_is_rejected_when_bulk_insert_bypasses_save(self):
        for overrides in (
            {"source_system": "ERP\t"},
            {"source_record_id": "INV-001-LINE-1\n"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    SalesTransaction.objects.bulk_create(
                        [SalesTransaction(**self.sales_values(**overrides))]
                    )

    def test_unicode_edge_whitespace_is_rejected_by_database(self):
        whitespace_values = ("\u0085", "\u00a0", "\u2007", "\u202f", "\u3000")
        for index, whitespace in enumerate(whitespace_values, start=1):
            with self.subTest(code_point=f"U+{ord(whitespace):04X}"):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    SalesTransaction.objects.bulk_create(
                        [
                            SalesTransaction(
                                **self.sales_values(
                                    source_system=f"ERP{whitespace}",
                                    source_record_id=f"UNICODE-{index}",
                                )
                            )
                        ]
                    )

    def test_non_finite_measures_are_rejected_by_database(self):
        measure_values = (
            ("quantity", "NaN", "1"),
            ("revenue_amount", "1", "NaN"),
        )
        for field_name, quantity, revenue_amount in measure_values:
            with self.subTest(field_name=field_name):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """
                            INSERT INTO business_data_salestransaction (
                                calendar_date_id,
                                product_id,
                                territory_id,
                                quantity,
                                revenue_amount,
                                currency_code,
                                source_system,
                                source_record_id,
                                created_at
                            ) VALUES (
                                %s, %s, %s, %s::numeric, %s::numeric,
                                %s, %s, %s, NOW()
                            )
                            """,
                            (
                                self.calendar_date.pk,
                                self.product.pk,
                                self.territory.pk,
                                quantity,
                                revenue_amount,
                                "INR",
                                "ERP",
                                f"NAN-{field_name}",
                            ),
                        )

    def test_non_leaf_territory_is_rejected_by_model_and_database(self):
        invalid_sale = SalesTransaction(
            **self.sales_values(
                territory=self.region,
                source_record_id="NON-LEAF",
            )
        )
        with self.assertRaises(ValidationError):
            invalid_sale.full_clean()

        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesTransaction.objects.bulk_create([invalid_sale])

    def test_missing_territory_is_rejected_before_deferred_fk_check(self):
        invalid_sale = SalesTransaction(**self.sales_values())
        invalid_sale.territory_id = 9_999_999

        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesTransaction.objects.bulk_create([invalid_sale])

    def test_master_data_and_calendar_references_are_protected(self):
        SalesTransaction.objects.create(**self.sales_values())

        for record in (self.calendar_date, self.product, self.territory):
            with self.subTest(record=record):
                with self.assertRaises(ProtectedError):
                    type(record).objects.filter(pk=record.pk).delete()

    def test_sales_transaction_is_immutable_through_model_and_database(self):
        sale = SalesTransaction.objects.create(**self.sales_values())
        sale.quantity = Decimal("11")

        with self.assertRaises(ValidationError):
            sale.save()

        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesTransaction.objects.filter(pk=sale.pk).update(
                quantity=Decimal("11")
            )

        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesTransaction.objects.filter(pk=sale.pk).delete()


class BusinessDataAdminTests(SalesTransactionFixtures):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin_user = get_user_model().objects.create_superuser(
            email="business-data-admin@example.com",
            password="test-only-password",
        )

    def test_calendar_admin_is_read_only(self):
        model_admin = CalendarDateAdmin(CalendarDate, admin.site)

        self.assertFalse(model_admin.has_add_permission(None))
        self.assertFalse(model_admin.has_change_permission(None, self.calendar_date))
        self.assertFalse(model_admin.has_delete_permission(None, self.calendar_date))

    def test_sales_admin_makes_existing_fact_read_only(self):
        model_admin = SalesTransactionAdmin(SalesTransaction, admin.site)
        sale = SalesTransaction(**self.sales_values())

        self.assertFalse(model_admin.has_add_permission(None))
        self.assertFalse(model_admin.has_delete_permission(None, sale))
        self.assertFalse(model_admin.has_change_permission(None, sale))
        self.assertEqual(
            set(model_admin.get_readonly_fields(None, sale)),
            {field.name for field in SalesTransaction._meta.fields},
        )

    def test_existing_sales_page_is_view_only_without_save_controls(self):
        sale = SalesTransaction.objects.create(
            **self.sales_values(created_by=self.admin_user)
        )
        self.client.force_login(self.admin_user)
        change_url = reverse(
            "admin:business_data_salestransaction_change",
            args=(sale.pk,),
        )

        response = self.client.get(change_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="_save"')
        self.assertEqual(self.client.post(change_url, {}).status_code, 403)
