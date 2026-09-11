from django.db import models
from django.db.models.functions import Lower


class Product(models.Model):
    """A product that can be referenced consistently by future business data."""

    code = models.CharField(
        max_length=64,
        help_text="Stable company code used in files and integrations.",
    )
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Deactivate products that should no longer be used.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name", "code")
        constraints = [
            models.UniqueConstraint(
                Lower("code"),
                name="master_data_product_code_case_insensitive_unique",
            ),
            models.CheckConstraint(
                condition=~models.Q(code__regex=r"^\s*$"),
                name="master_data_product_code_not_blank",
            ),
            models.CheckConstraint(
                condition=~models.Q(name__regex=r"^\s*$"),
                name="master_data_product_name_not_blank",
            ),
        ]

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        self.name = self.name.strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} — {self.name}"

