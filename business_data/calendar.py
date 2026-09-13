"""Deterministic values for the reporting calendar dimension."""

from datetime import date, timedelta


CALENDAR_FIELD_NAMES = (
    "date",
    "date_key",
    "year",
    "quarter",
    "month",
    "month_name",
    "month_abbreviation",
    "day_of_month",
    "day_of_year",
    "iso_year",
    "iso_week",
    "iso_weekday",
    "weekday_name",
    "is_weekend",
    "week_start",
    "week_end",
    "month_start",
    "month_end",
    "quarter_start",
    "quarter_end",
    "year_start",
    "year_end",
    "year_month_key",
    "year_month_label",
    "year_quarter_label",
)

_MONTH_NAMES = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_MONTH_ABBREVIATIONS = (
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
_WEEKDAY_NAMES = (
    "",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def _last_day_of_month(value: date) -> date:
    if value.month == 12:
        return date(value.year, 12, 31)
    return date(value.year, value.month + 1, 1) - timedelta(days=1)


def _last_day_of_quarter(value: date, quarter_start_month: int) -> date:
    if quarter_start_month == 10:
        return date(value.year, 12, 31)
    return date(value.year, quarter_start_month + 3, 1) - timedelta(days=1)


def build_calendar_date(value: date) -> dict[str, object]:
    """Return all stored CalendarDate values for one Gregorian date."""

    iso_year, iso_week, iso_weekday = value.isocalendar()
    quarter = ((value.month - 1) // 3) + 1
    quarter_start_month = ((quarter - 1) * 3) + 1
    week_start = value - timedelta(days=iso_weekday - 1)

    return {
        "date": value,
        "date_key": (value.year * 10_000) + (value.month * 100) + value.day,
        "year": value.year,
        "quarter": quarter,
        "month": value.month,
        "month_name": _MONTH_NAMES[value.month],
        "month_abbreviation": _MONTH_ABBREVIATIONS[value.month],
        "day_of_month": value.day,
        "day_of_year": value.timetuple().tm_yday,
        "iso_year": iso_year,
        "iso_week": iso_week,
        "iso_weekday": iso_weekday,
        "weekday_name": _WEEKDAY_NAMES[iso_weekday],
        "is_weekend": iso_weekday >= 6,
        "week_start": week_start,
        "week_end": week_start + timedelta(days=6),
        "month_start": value.replace(day=1),
        "month_end": _last_day_of_month(value),
        "quarter_start": date(value.year, quarter_start_month, 1),
        "quarter_end": _last_day_of_quarter(value, quarter_start_month),
        "year_start": date(value.year, 1, 1),
        "year_end": date(value.year, 12, 31),
        "year_month_key": (value.year * 100) + value.month,
        "year_month_label": (
            f"{_MONTH_ABBREVIATIONS[value.month]} {value.year:04d}"
        ),
        "year_quarter_label": f"{value.year:04d} Q{quarter}",
    }
