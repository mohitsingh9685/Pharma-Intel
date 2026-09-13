"""Reporting dimensions and immutable business facts."""

from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import (
    ExtractDay,
    ExtractIsoWeekDay,
    ExtractIsoYear,
    ExtractMonth,
    ExtractQuarter,
    ExtractWeek,
    ExtractYear,
    Trim,
    Upper,
)

from master_data.models import (
    Hospital,
    Product,
    SalesRepresentative,
    Territory,
)


class CalendarDate(models.Model):
    """One reporting dimension row for each Gregorian calendar date."""

    date = models.DateField(primary_key=True)
    date_key = models.PositiveIntegerField(unique=True, editable=False)
    year = models.PositiveSmallIntegerField(editable=False)
    quarter = models.PositiveSmallIntegerField(editable=False)
    month = models.PositiveSmallIntegerField(editable=False)
    month_name = models.CharField(max_length=9, editable=False)
    month_abbreviation = models.CharField(max_length=3, editable=False)
    day_of_month = models.PositiveSmallIntegerField(editable=False)
    day_of_year = models.PositiveSmallIntegerField(editable=False)
    iso_year = models.PositiveSmallIntegerField(editable=False)
    iso_week = models.PositiveSmallIntegerField(editable=False)
    iso_weekday = models.PositiveSmallIntegerField(editable=False)
    weekday_name = models.CharField(max_length=9, editable=False)
    is_weekend = models.BooleanField(editable=False)
    week_start = models.DateField(editable=False)
    week_end = models.DateField(editable=False)
    month_start = models.DateField(editable=False)
    month_end = models.DateField(editable=False)
    quarter_start = models.DateField(editable=False)
    quarter_end = models.DateField(editable=False)
    year_start = models.DateField(editable=False)
    year_end = models.DateField(editable=False)
    year_month_key = models.PositiveIntegerField(editable=False)
    year_month_label = models.CharField(max_length=8, editable=False)
    year_quarter_label = models.CharField(max_length=7, editable=False)

    class Meta:
        ordering = ("date",)
        verbose_name = "calendar date"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    date__range=(date(1, 1, 1), date(9998, 12, 31))
                ),
                name="business_data_calendar_supported_date",
            ),
            models.CheckConstraint(
                condition=models.Q(year=ExtractYear("date")),
                name="business_data_calendar_year_matches",
            ),
            models.CheckConstraint(
                condition=models.Q(quarter=ExtractQuarter("date")),
                name="business_data_calendar_quarter_matches",
            ),
            models.CheckConstraint(
                condition=models.Q(month=ExtractMonth("date")),
                name="business_data_calendar_month_matches",
            ),
            models.CheckConstraint(
                condition=models.Q(day_of_month=ExtractDay("date")),
                name="business_data_calendar_day_matches",
            ),
            models.CheckConstraint(
                condition=models.Q(iso_year=ExtractIsoYear("date")),
                name="business_data_calendar_iso_year_matches",
            ),
            models.CheckConstraint(
                condition=models.Q(iso_week=ExtractWeek("date")),
                name="business_data_calendar_iso_week_matches",
            ),
            models.CheckConstraint(
                condition=models.Q(iso_weekday=ExtractIsoWeekDay("date")),
                name="business_data_calendar_iso_day_matches",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    date_key=(
                        models.F("year") * 10_000
                        + models.F("month") * 100
                        + models.F("day_of_month")
                    )
                ),
                name="business_data_calendar_date_key_matches",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    year_month_key=(models.F("year") * 100 + models.F("month"))
                ),
                name="business_data_calendar_month_key_matches",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(week_start__lte=models.F("date"))
                    & models.Q(week_end__gte=models.F("date"))
                    & models.Q(week_end=models.F("week_start") + timedelta(days=6))
                ),
                name="business_data_calendar_week_bounds",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(month_start__lte=models.F("date"))
                    & models.Q(month_end__gte=models.F("date"))
                ),
                name="business_data_calendar_month_bounds",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(quarter_start__lte=models.F("date"))
                    & models.Q(quarter_end__gte=models.F("date"))
                ),
                name="business_data_calendar_quarter_bounds",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(year_start__lte=models.F("date"))
                    & models.Q(year_end__gte=models.F("date"))
                ),
                name="business_data_calendar_year_bounds",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(is_weekend=True, iso_weekday__in=(6, 7))
                    | models.Q(is_weekend=False, iso_weekday__range=(1, 5))
                ),
                name="business_data_calendar_weekend_matches",
            ),
        ]
        indexes = [
            models.Index(
                fields=("year", "month"),
                name="bd_calendar_month_idx",
            ),
            models.Index(
                fields=("iso_year", "iso_week"),
                name="bd_calendar_iso_week_idx",
            ),
        ]

    @classmethod
    def for_date(cls, value):
        """Build a correctly derived, unsaved row for one date."""

        from .calendar import build_calendar_date

        return cls(**build_calendar_date(value))

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Calendar dates are immutable after creation.")
        if self.date:
            from .calendar import build_calendar_date

            for field_name, value in build_calendar_date(self.date).items():
                setattr(self, field_name, value)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Calendar dates cannot be deleted through the model.")

    def __str__(self):
        return self.date.isoformat()


class SalesTransaction(models.Model):
    """One immutable source-system sales line with historical attribution."""

    calendar_date = models.ForeignKey(
        CalendarDate,
        on_delete=models.PROTECT,
        related_name="sales_transactions",
        db_index=False,
        help_text="Business date on which this source line occurred.",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="sales_transactions",
        db_index=False,
    )
    territory = models.ForeignKey(
        Territory,
        on_delete=models.PROTECT,
        related_name="sales_transactions",
        db_index=False,
        help_text="Leaf Territory explicitly attributed by the source.",
    )
    hospital = models.ForeignKey(
        Hospital,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="sales_transactions",
        db_index=False,
        help_text="Leave empty only when the source line did not provide a hospital.",
    )
    sales_representative = models.ForeignKey(
        SalesRepresentative,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="sales_transactions",
        db_index=False,
        help_text=(
            "Leave empty only when the source line did not provide a representative."
        ),
    )
    quantity = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        help_text="Positive for a sale and negative for a return; zero is invalid.",
    )
    revenue_amount = models.DecimalField(
        max_digits=20,
        decimal_places=4,
        help_text="Non-negative for a sale and non-positive for a return.",
    )
    currency_code = models.CharField(
        max_length=3,
        help_text="Three-letter uppercase currency code, such as INR or USD.",
    )
    source_system = models.CharField(
        max_length=64,
        help_text="Stable name of the source system or file contract.",
    )
    source_record_id = models.CharField(
        max_length=255,
        help_text="Stable identifier for this exact line inside its source.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        editable=False,
        on_delete=models.PROTECT,
        related_name="created_sales_transactions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("source_system", "source_record_id"),
                name="business_data_sales_source_unique",
                violation_error_message=(
                    "That source system and source record ID already exist."
                ),
            ),
            models.CheckConstraint(
                condition=~models.Q(source_system__regex=r"^\s*$"),
                name="business_data_sales_source_not_blank",
            ),
            models.CheckConstraint(
                condition=models.Q(source_system=Trim("source_system")),
                name="business_data_sales_source_trimmed",
            ),
            models.CheckConstraint(
                condition=models.Q(source_system=Upper("source_system")),
                name="business_data_sales_source_uppercase",
            ),
            models.CheckConstraint(
                condition=~models.Q(
                    source_system__regex=r"^[[:space:]]|[[:space:]]$"
                ),
                name="business_data_sales_source_no_edge_space",
            ),
            models.CheckConstraint(
                condition=~models.Q(source_record_id__regex=r"^\s*$"),
                name="business_data_sales_record_id_not_blank",
            ),
            models.CheckConstraint(
                condition=models.Q(source_record_id=Trim("source_record_id")),
                name="business_data_sales_record_id_trimmed",
            ),
            models.CheckConstraint(
                condition=~models.Q(
                    source_record_id__regex=r"^[[:space:]]|[[:space:]]$"
                ),
                name="business_data_sales_record_no_edge_space",
            ),
            models.CheckConstraint(
                condition=models.Q(currency_code__regex=r"^[A-Z]{3}$"),
                name="business_data_sales_currency_format",
            ),
            models.CheckConstraint(
                condition=~models.Q(quantity=0),
                name="business_data_sales_quantity_nonzero",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(quantity__gt=Decimal("-1000000000000000"))
                    & models.Q(quantity__lt=Decimal("1000000000000000"))
                ),
                name="business_data_sales_quantity_finite",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(revenue_amount__gt=Decimal("-10000000000000000"))
                    & models.Q(revenue_amount__lt=Decimal("10000000000000000"))
                ),
                name="business_data_sales_revenue_finite",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(quantity__gt=0, revenue_amount__gte=0)
                    | models.Q(quantity__lt=0, revenue_amount__lte=0)
                ),
                name="business_data_sales_measure_signs",
            ),
        ]
        indexes = [
            models.Index(
                fields=("-calendar_date", "-id"),
                name="bd_sales_date_id_idx",
            ),
            models.Index(
                fields=("product", "calendar_date"),
                name="bd_sales_product_date_idx",
            ),
            models.Index(
                fields=("territory", "calendar_date"),
                name="bd_sales_territory_date_idx",
            ),
            models.Index(
                fields=("hospital", "calendar_date"),
                condition=models.Q(hospital__isnull=False),
                name="bd_sales_hospital_date_idx",
            ),
            models.Index(
                fields=("sales_representative", "calendar_date"),
                condition=models.Q(sales_representative__isnull=False),
                name="bd_sales_rep_date_idx",
            ),
        ]

    def _normalize_source_values(self):
        self.source_system = (self.source_system or "").strip().upper()
        self.source_record_id = (self.source_record_id or "").strip()
        self.currency_code = (self.currency_code or "").strip().upper()

    def clean(self):
        super().clean()
        self._normalize_source_values()
        if not (
            self.calendar_date_id and self.product_id and self.territory_id
        ):
            return

        from .validation import validate_sales_dimensions

        validate_sales_dimensions(
            calendar_date=self.calendar_date,
            product=self.product,
            territory=self.territory,
            hospital=self.hospital if self.hospital_id else None,
            sales_representative=(
                self.sales_representative
                if self.sales_representative_id
                else None
            ),
        )

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Sales transactions are immutable after creation.")
        self.clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Sales transactions cannot be deleted.")

    def __str__(self):
        return f"{self.source_system}:{self.source_record_id}"
