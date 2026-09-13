"""Cross-model validation shared by business-data entry points."""

from datetime import date, datetime

from django.core.exceptions import ValidationError
from django.db.models import Q

from master_data.models import (
    HospitalTerritoryAssignment,
    SalesRepresentativeTerritoryAssignment,
    Territory,
)


def _date_value(calendar_date):
    """Return a Python date from either a date or a CalendarDate instance."""

    if isinstance(calendar_date, datetime):
        return calendar_date.date()
    if isinstance(calendar_date, date):
        return calendar_date

    value = getattr(calendar_date, "date", None)
    return value() if callable(value) else value


def _is_effective_on(queryset, on_date):
    """Check an inclusive assignment period without using today's date."""

    return queryset.filter(
        effective_from__lte=on_date,
    ).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=on_date)
    ).exists()


def validate_sales_dimensions(
    *,
    calendar_date,
    product,
    territory,
    hospital=None,
    sales_representative=None,
):
    """Validate that optional sales dimensions match their dated territory.

    Required-field and foreign-key existence checks remain the model fields'
    responsibility. Historical facts may reference identities that have since
    been deactivated, so this function deliberately does not inspect
    ``is_active``.
    """

    del product  # No cross-dimension rule applies to Product yet.

    errors = {}
    on_date = _date_value(calendar_date)

    if territory is not None and territory.level != Territory.Level.TERRITORY:
        errors["territory"] = (
            "Sales transactions must use a Territory-level territory."
        )

    territory_id = getattr(territory, "pk", None)
    hospital_id = getattr(hospital, "pk", None)
    representative_id = getattr(sales_representative, "pk", None)

    if on_date is not None and territory_id is not None:
        if hospital is not None and (
            hospital_id is None
            or not _is_effective_on(
                HospitalTerritoryAssignment.objects.filter(
                    hospital_id=hospital_id,
                    territory_id=territory_id,
                ),
                on_date,
            )
        ):
            errors["hospital"] = (
                "This hospital was not assigned to the selected territory "
                "on the transaction date."
            )

        if sales_representative is not None and (
            representative_id is None
            or not _is_effective_on(
                SalesRepresentativeTerritoryAssignment.objects.filter(
                    sales_representative_id=representative_id,
                    territory_id=territory_id,
                ),
                on_date,
            )
        ):
            errors["sales_representative"] = (
                "This sales representative was not assigned to the selected "
                "territory on the transaction date."
            )

    if errors:
        raise ValidationError(errors)

