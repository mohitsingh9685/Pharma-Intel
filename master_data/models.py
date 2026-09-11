from django.conf import settings
from django.db import models
from django.db.models.functions import Lower, Trim


def coded_master_data_constraints(prefix):
    """Return database rules shared by every coded master-data model."""

    return [
        models.UniqueConstraint(
            Lower(Trim("code")),
            name=f"{prefix}_code_case_insensitive_unique",
        ),
        models.CheckConstraint(
            condition=~models.Q(code__regex=r"^\s*$"),
            name=f"{prefix}_code_not_blank",
        ),
        models.CheckConstraint(
            condition=~models.Q(name__regex=r"^\s*$"),
            name=f"{prefix}_name_not_blank",
        ),
        models.CheckConstraint(
            condition=models.Q(code=Trim("code")),
            name=f"{prefix}_code_trimmed",
        ),
        models.CheckConstraint(
            condition=models.Q(name=Trim("name")),
            name=f"{prefix}_name_trimmed",
        ),
    ]


class CodedMasterData(models.Model):
    """Shared identity and lifecycle fields for company-owned master data."""

    code = models.CharField(
        max_length=64,
        help_text="Stable company code used in files and integrations.",
    )
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Deactivate records that should no longer be used.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ("name", "code")

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        self.name = self.name.strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} — {self.name}"


class Product(CodedMasterData):
    """A product that can be referenced consistently by future business data."""

    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Deactivate products that should no longer be used.",
    )

    class Meta(CodedMasterData.Meta):
        constraints = coded_master_data_constraints("master_data_product")


class Territory(CodedMasterData):
    """A stable commercial geography identity used by future assignments."""

    class Level(models.TextChoices):
        ZONE = "zone", "Zone"
        REGION = "region", "Region"
        AREA = "area", "Area"
        TERRITORY = "territory", "Territory"

    level = models.CharField(
        max_length=16,
        choices=Level.choices,
        db_index=True,
        help_text="Commercial level represented by this record.",
    )

    class Meta(CodedMasterData.Meta):
        verbose_name_plural = "territories"
        constraints = [
            *coded_master_data_constraints("master_data_territory"),
            models.CheckConstraint(
                condition=models.Q(
                    level__in=("zone", "region", "area", "territory")
                ),
                name="master_data_territory_level_valid",
            ),
        ]


class Hospital(CodedMasterData):
    """A stable healthcare-organization identity."""

    class Meta(CodedMasterData.Meta):
        constraints = coded_master_data_constraints("master_data_hospital")


class HealthcareProfessional(CodedMasterData):
    """A stable doctor or healthcare-professional identity."""

    class Meta(CodedMasterData.Meta):
        verbose_name = "doctor / HCP"
        verbose_name_plural = "doctors / HCPs"
        constraints = coded_master_data_constraints("master_data_hcp")


class SalesRepresentative(CodedMasterData):
    """A stable sales-representative identity, optionally linked to a login."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="sales_representative",
        help_text="Optional application login belonging to this representative.",
    )

    class Meta(CodedMasterData.Meta):
        constraints = coded_master_data_constraints("master_data_sales_rep")
