from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateRangeField, RangeOperators
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import models
from django.db.models.functions import Lower, Trim
from django.utils import timezone


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


def effective_period_expression():
    """Build an inclusive PostgreSQL date range from assignment dates."""

    return models.Func(
        models.F("effective_from"),
        models.F("effective_to"),
        models.Value("[]"),
        function="DATERANGE",
        output_field=DateRangeField(),
    )


def dated_assignment_constraints(prefix, overlap_fields, overlap_message):
    """Return date-order and concurrent-safe overlap constraints."""

    return [
        models.CheckConstraint(
            condition=(
                models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gte=models.F("effective_from"))
            ),
            name=f"{prefix}_dates_valid",
            violation_error_message=(
                "The effective end date must be on or after the start date."
            ),
        ),
        ExclusionConstraint(
            name=f"{prefix}_no_overlap",
            expressions=[
                *[
                    (field_name, RangeOperators.EQUAL)
                    for field_name in overlap_fields
                ],
                (effective_period_expression(), RangeOperators.OVERLAPS),
            ],
            violation_error_message=overlap_message,
        ),
    ]


def primary_assignment_constraint(
    prefix,
    identity_field,
    condition,
    violation_message,
):
    """Allow only one primary assignment for an identity on a given date."""

    return ExclusionConstraint(
        name=f"{prefix}_one_primary",
        expressions=[
            (identity_field, RangeOperators.EQUAL),
            (effective_period_expression(), RangeOperators.OVERLAPS),
        ],
        condition=condition,
        violation_error_message=violation_message,
    )


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


class Specialty(CodedMasterData):
    """A controlled medical-specialty identity used by HCP assignments."""

    class Meta(CodedMasterData.Meta):
        verbose_name_plural = "specialties"
        constraints = coded_master_data_constraints("master_data_specialty")


class EffectiveDatedRelationshipQuerySet(models.QuerySet):
    """Shared as-of-date queries for all historical assignments."""

    def effective_on(self, on_date):
        return self.alias(
            _effective_period=effective_period_expression()
        ).filter(
            _effective_period__contains=on_date,
        )

    def current(self):
        return self.effective_on(timezone.localdate())


class EffectiveDatedRelationship(models.Model):
    """Shared inclusive date period and audit timestamps for assignments."""

    active_participant_fields = ()

    effective_from = models.DateField(
        help_text="First date on which this assignment applies.",
    )
    effective_to = models.DateField(
        null=True,
        blank=True,
        help_text=(
            "Last date on which this assignment applies; leave blank if ongoing."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = EffectiveDatedRelationshipQuerySet.as_manager()

    class Meta:
        abstract = True
        ordering = ("-effective_from",)

    def clean(self):
        super().clean()
        if (
            self.effective_from
            and self.effective_to
            and self.effective_to < self.effective_from
        ):
            raise ValidationError(
                {
                    "effective_to": (
                        "The effective end date must be on or after the start date."
                    )
                }
            )

        if not self._state.adding:
            return
        if self.effective_to and self.effective_to < timezone.localdate():
            return

        inactive_errors = {}
        for field_name in self.active_participant_fields:
            try:
                participant = getattr(self, field_name)
            except ObjectDoesNotExist:
                continue
            if participant is not None and not participant.is_active:
                inactive_errors[field_name] = (
                    "Inactive records cannot be used in current or future "
                    "assignments."
                )
        if inactive_errors:
            raise ValidationError(inactive_errors)

    @property
    def period_label(self):
        if not self.effective_from:
            return "dates not set"
        end = self.effective_to.isoformat() if self.effective_to else "ongoing"
        return f"{self.effective_from.isoformat()} to {end}"


class TerritoryHierarchyAssignment(EffectiveDatedRelationship):
    """A dated parent-child relationship between commercial geographies."""

    active_participant_fields = ("parent", "child")

    parent = models.ForeignKey(
        Territory,
        on_delete=models.PROTECT,
        related_name="child_assignments",
    )
    child = models.ForeignKey(
        Territory,
        on_delete=models.PROTECT,
        related_name="parent_assignments",
    )

    class Meta(EffectiveDatedRelationship.Meta):
        verbose_name = "territory hierarchy assignment"
        constraints = [
            *dated_assignment_constraints(
                "master_data_territory_hierarchy",
                ("child",),
                "This geography already has a parent during part of that period.",
            ),
            models.CheckConstraint(
                condition=~models.Q(parent=models.F("child")),
                name="master_data_territory_hierarchy_not_self",
                violation_error_message="A geography cannot be its own parent.",
            ),
        ]

    def clean(self):
        super().clean()
        if not self.parent_id or not self.child_id:
            return
        if self.parent_id == self.child_id:
            raise ValidationError({"child": "A geography cannot be its own parent."})

        level_rank = {
            Territory.Level.ZONE: 0,
            Territory.Level.REGION: 1,
            Territory.Level.AREA: 2,
            Territory.Level.TERRITORY: 3,
        }
        parent_rank = level_rank.get(self.parent.level)
        child_rank = level_rank.get(self.child.level)
        if parent_rank is None or child_rank is None:
            return
        if parent_rank >= child_rank:
            raise ValidationError(
                {
                    "child": (
                        "The child must be at a lower commercial level than its "
                        "parent."
                    )
                }
            )

    def __str__(self):
        return f"{self.parent.code} → {self.child.code} ({self.period_label})"


class HospitalTerritoryAssignment(EffectiveDatedRelationship):
    """A hospital's dated assignment to one leaf territory."""

    active_participant_fields = ("hospital", "territory")

    hospital = models.ForeignKey(
        Hospital,
        on_delete=models.PROTECT,
        related_name="territory_assignments",
    )
    territory = models.ForeignKey(
        Territory,
        on_delete=models.PROTECT,
        related_name="hospital_assignments",
    )

    class Meta(EffectiveDatedRelationship.Meta):
        constraints = dated_assignment_constraints(
            "master_data_hospital_territory",
            ("hospital",),
            "This hospital already has a territory during part of that period.",
        )

    def clean(self):
        super().clean()
        if self.territory_id and self.territory.level != Territory.Level.TERRITORY:
            raise ValidationError(
                {"territory": "A hospital must be assigned to Territory level."}
            )

    def __str__(self):
        return f"{self.territory.code} → {self.hospital.code} ({self.period_label})"


class HealthcareProfessionalHospitalAffiliation(EffectiveDatedRelationship):
    """A dated HCP affiliation with a hospital."""

    active_participant_fields = ("healthcare_professional", "hospital")

    healthcare_professional = models.ForeignKey(
        HealthcareProfessional,
        on_delete=models.PROTECT,
        related_name="hospital_affiliations",
    )
    hospital = models.ForeignKey(
        Hospital,
        on_delete=models.PROTECT,
        related_name="hcp_affiliations",
    )
    is_primary = models.BooleanField(
        default=False,
        help_text="Mark the HCP's main hospital for this period.",
    )

    class Meta(EffectiveDatedRelationship.Meta):
        verbose_name = "doctor / HCP hospital affiliation"
        verbose_name_plural = "doctor / HCP hospital affiliations"
        constraints = [
            *dated_assignment_constraints(
                "master_data_hcp_hospital",
                ("healthcare_professional", "hospital"),
                "This HCP and hospital already overlap during that period.",
            ),
            primary_assignment_constraint(
                "master_data_hcp_hospital",
                "healthcare_professional",
                models.Q(is_primary=True),
                "This HCP already has a primary hospital during that period.",
            ),
        ]

    def __str__(self):
        return (
            f"{self.healthcare_professional.code} → {self.hospital.code} "
            f"({self.period_label})"
        )


class HealthcareProfessionalSpecialtyAssignment(EffectiveDatedRelationship):
    """A dated controlled specialty assigned to an HCP."""

    active_participant_fields = ("healthcare_professional", "specialty")

    healthcare_professional = models.ForeignKey(
        HealthcareProfessional,
        on_delete=models.PROTECT,
        related_name="specialty_assignments",
    )
    specialty = models.ForeignKey(
        Specialty,
        on_delete=models.PROTECT,
        related_name="hcp_assignments",
    )
    is_primary = models.BooleanField(
        default=False,
        help_text="Mark the HCP's main specialty for this period.",
    )

    class Meta(EffectiveDatedRelationship.Meta):
        verbose_name = "doctor / HCP specialty assignment"
        verbose_name_plural = "doctor / HCP specialty assignments"
        constraints = [
            *dated_assignment_constraints(
                "master_data_hcp_specialty",
                ("healthcare_professional", "specialty"),
                "This HCP and specialty already overlap during that period.",
            ),
            primary_assignment_constraint(
                "master_data_hcp_specialty",
                "healthcare_professional",
                models.Q(is_primary=True),
                "This HCP already has a primary specialty during that period.",
            ),
        ]

    def __str__(self):
        return (
            f"{self.healthcare_professional.code} → {self.specialty.code} "
            f"({self.period_label})"
        )


class SalesRepresentativeTerritoryAssignment(EffectiveDatedRelationship):
    """A dated primary or support sales-representative territory assignment."""

    active_participant_fields = ("sales_representative", "territory")

    class AssignmentType(models.TextChoices):
        PRIMARY = "primary", "Primary"
        SUPPORT = "support", "Support"

    sales_representative = models.ForeignKey(
        SalesRepresentative,
        on_delete=models.PROTECT,
        related_name="territory_assignments",
    )
    territory = models.ForeignKey(
        Territory,
        on_delete=models.PROTECT,
        related_name="sales_representative_assignments",
    )
    assignment_type = models.CharField(
        max_length=7,
        choices=AssignmentType.choices,
        help_text="Primary owner or supporting coverage for this territory.",
    )

    class Meta(EffectiveDatedRelationship.Meta):
        constraints = [
            *dated_assignment_constraints(
                "master_data_sales_rep_territory",
                ("sales_representative", "territory"),
                "This representative and territory already overlap in that period.",
            ),
            primary_assignment_constraint(
                "master_data_sales_rep_territory",
                "territory",
                models.Q(assignment_type="primary"),
                "This territory already has a primary representative then.",
            ),
            models.CheckConstraint(
                condition=models.Q(assignment_type__in=("primary", "support")),
                name="master_data_sales_rep_assignment_type_valid",
                violation_error_message="Choose Primary or Support.",
            ),
        ]

    def clean(self):
        super().clean()
        if self.territory_id and self.territory.level != Territory.Level.TERRITORY:
            raise ValidationError(
                {
                    "territory": (
                        "A sales representative must be assigned to Territory level."
                    )
                }
            )

    def __str__(self):
        return (
            f"{self.sales_representative.code} → {self.territory.code} "
            f"({self.period_label})"
        )
