from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import Product


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

