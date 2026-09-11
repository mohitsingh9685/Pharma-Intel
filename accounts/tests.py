from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase


class UserModelTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()

    def test_regular_user_uses_normalized_email_and_default_role(self):
        user = self.user_model.objects.create_user(
            email="  Learner@Example.COM ",
            password="a-test-password",
        )

        self.assertEqual(user.email, "learner@example.com")
        self.assertEqual(user.role, self.user_model.Role.BUSINESS_USER)
        self.assertTrue(user.check_password("a-test-password"))
        self.assertFalse(user.is_staff)

    def test_superuser_has_admin_role_and_django_permissions(self):
        user = self.user_model.objects.create_superuser(
            email="admin@example.com",
            password="a-test-password",
        )

        self.assertEqual(user.role, self.user_model.Role.ADMIN)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    def test_email_uniqueness_is_case_insensitive(self):
        self.user_model.objects.create_user(
            email="person@example.com",
            password="a-test-password",
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.user_model.objects.create(
                email="PERSON@example.com",
                password="unusable-for-this-constraint-test",
            )

