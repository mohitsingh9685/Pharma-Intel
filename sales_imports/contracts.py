"""Versioned, database-independent sales row contracts."""

import csv
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Iterable, Iterator, Mapping, TextIO

from django.core.exceptions import ValidationError
from django.core.validators import DecimalValidator


SALES_ROWS_V1 = "sales_rows_v1"
SALES_ROWS_V1_COLUMNS = (
    "source_record_id",
    "transaction_date",
    "product_code",
    "territory_code",
    "hospital_code",
    "sales_representative_code",
    "quantity",
    "revenue_amount",
    "currency_code",
)

_DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_DECIMAL_PATTERN = re.compile(r"^-?[0-9]+(?:\.[0-9]+)?$")
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
_QUANTITY_VALIDATOR = DecimalValidator(max_digits=18, decimal_places=3)
_REVENUE_VALIDATOR = DecimalValidator(max_digits=20, decimal_places=4)


class SalesContractIssueCode(StrEnum):
    CSV_MALFORMED = "CSV_MALFORMED"
    SCHEMA_MISSING_COLUMN = "SCHEMA_MISSING_COLUMN"
    SCHEMA_DUPLICATE_COLUMN = "SCHEMA_DUPLICATE_COLUMN"
    SCHEMA_UNKNOWN_COLUMN = "SCHEMA_UNKNOWN_COLUMN"
    FILE_NO_DATA_ROWS = "FILE_NO_DATA_ROWS"
    FILE_ROW_LIMIT_EXCEEDED = "FILE_ROW_LIMIT_EXCEEDED"
    ROW_COLUMN_COUNT_MISMATCH = "ROW_COLUMN_COUNT_MISMATCH"
    REQUIRED_VALUE_MISSING = "REQUIRED_VALUE_MISSING"
    VALUE_TOO_LONG = "VALUE_TOO_LONG"
    INVALID_CONTROL_CHARACTER = "INVALID_CONTROL_CHARACTER"
    DATE_INVALID_FORMAT = "DATE_INVALID_FORMAT"
    DECIMAL_INVALID_FORMAT = "DECIMAL_INVALID_FORMAT"
    DECIMAL_PRECISION_EXCEEDED = "DECIMAL_PRECISION_EXCEEDED"
    QUANTITY_ZERO = "QUANTITY_ZERO"
    MEASURE_SIGN_MISMATCH = "MEASURE_SIGN_MISMATCH"
    CURRENCY_CODE_INVALID = "CURRENCY_CODE_INVALID"
    DUPLICATE_SOURCE_RECORD_ID = "DUPLICATE_SOURCE_RECORD_ID"


class SalesFileContractError(Exception):
    """A file-level problem that prevents safe row parsing."""

    def __init__(self, code: SalesContractIssueCode, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SalesRowIssue:
    row_number: int | None
    column: str
    code: SalesContractIssueCode
    message: str


@dataclass(frozen=True, slots=True)
class SalesRowV1:
    source_record_id: str
    transaction_date: date
    product_code: str
    territory_code: str
    hospital_code: str | None
    sales_representative_code: str | None
    quantity: Decimal
    revenue_amount: Decimal
    currency_code: str


@dataclass(frozen=True, slots=True)
class SalesRowValidationResult:
    row_number: int
    row: SalesRowV1 | None
    issues: tuple[SalesRowIssue, ...]

    @property
    def is_valid(self) -> bool:
        return self.row is not None and not self.issues


def _contains_control_character(value: str) -> bool:
    return any(unicodedata.category(character).startswith("C") for character in value)


def _issue(row_number, column, code, message):
    return SalesRowIssue(
        row_number=row_number,
        column=column,
        code=code,
        message=message,
    )


def validate_sales_headers(headers: Iterable[str]) -> tuple[str, ...]:
    """Validate an exact V1 header set while allowing any column order."""

    normalized_headers = list(headers)
    if normalized_headers:
        normalized_headers[0] = normalized_headers[0].removeprefix("\ufeff")

    duplicate_headers = sorted(
        {
            header
            for header in normalized_headers
            if normalized_headers.count(header) > 1
        }
    )
    if duplicate_headers:
        raise SalesFileContractError(
            SalesContractIssueCode.SCHEMA_DUPLICATE_COLUMN,
            "Duplicate sales columns: " + ", ".join(duplicate_headers) + ".",
        )

    expected = set(SALES_ROWS_V1_COLUMNS)
    actual = set(normalized_headers)
    missing = sorted(expected - actual)
    if missing:
        raise SalesFileContractError(
            SalesContractIssueCode.SCHEMA_MISSING_COLUMN,
            "Missing required sales columns: " + ", ".join(missing) + ".",
        )
    unknown = sorted(actual - expected)
    if unknown:
        raise SalesFileContractError(
            SalesContractIssueCode.SCHEMA_UNKNOWN_COLUMN,
            "Unknown sales columns: " + ", ".join(unknown) + ".",
        )
    return tuple(normalized_headers)


class SalesRowsV1Validator:
    """Validate normalized row values and duplicate IDs without database I/O."""

    def __init__(self, *, max_rows: int):
        if max_rows < 1:
            raise ValueError("max_rows must be positive.")
        self.max_rows = max_rows
        self.row_count = 0
        self._source_record_ids: set[str] = set()

    def note_row(self) -> None:
        """Count one non-empty source row, including structurally invalid rows."""

        self.row_count += 1
        if self.row_count > self.max_rows:
            raise SalesFileContractError(
                SalesContractIssueCode.FILE_ROW_LIMIT_EXCEEDED,
                f"The file exceeds the {self.max_rows}-row safety limit.",
            )

    @staticmethod
    def _text(
        raw_value: str,
        *,
        row_number: int,
        column: str,
        required: bool,
        max_length: int,
        uppercase: bool = False,
    ) -> tuple[str | None, list[SalesRowIssue]]:
        issues = []
        value = raw_value.strip()
        if not value:
            if required:
                issues.append(
                    _issue(
                        row_number,
                        column,
                        SalesContractIssueCode.REQUIRED_VALUE_MISSING,
                        "A value is required.",
                    )
                )
            return None, issues
        if uppercase:
            value = value.upper()
        if len(value) > max_length:
            issues.append(
                _issue(
                    row_number,
                    column,
                    SalesContractIssueCode.VALUE_TOO_LONG,
                    f"The value may contain at most {max_length} characters.",
                )
            )
        if _contains_control_character(value):
            issues.append(
                _issue(
                    row_number,
                    column,
                    SalesContractIssueCode.INVALID_CONTROL_CHARACTER,
                    "Control characters are not allowed.",
                )
            )
        return value, issues

    @staticmethod
    def _date(raw_value: str, *, row_number: int):
        value = raw_value.strip()
        if not value:
            return None, [
                _issue(
                    row_number,
                    "transaction_date",
                    SalesContractIssueCode.REQUIRED_VALUE_MISSING,
                    "A value is required.",
                )
            ]
        if not _DATE_PATTERN.fullmatch(value):
            return None, [
                _issue(
                    row_number,
                    "transaction_date",
                    SalesContractIssueCode.DATE_INVALID_FORMAT,
                    "Use an exact YYYY-MM-DD date.",
                )
            ]
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            return None, [
                _issue(
                    row_number,
                    "transaction_date",
                    SalesContractIssueCode.DATE_INVALID_FORMAT,
                    "Use a real calendar date in YYYY-MM-DD format.",
                )
            ]
        if parsed.year > 9998:
            return None, [
                _issue(
                    row_number,
                    "transaction_date",
                    SalesContractIssueCode.DATE_INVALID_FORMAT,
                    "The supported calendar ends at year 9998.",
                )
            ]
        return parsed, []

    @staticmethod
    def _decimal(
        raw_value: str,
        *,
        row_number: int,
        column: str,
        validator: DecimalValidator,
    ):
        value = raw_value.strip()
        if not value:
            return None, [
                _issue(
                    row_number,
                    column,
                    SalesContractIssueCode.REQUIRED_VALUE_MISSING,
                    "A value is required.",
                )
            ]
        if len(value) > 64:
            return None, [
                _issue(
                    row_number,
                    column,
                    SalesContractIssueCode.DECIMAL_PRECISION_EXCEEDED,
                    "The value exceeds the supported digits or decimal places.",
                )
            ]
        if not _DECIMAL_PATTERN.fullmatch(value):
            return None, [
                _issue(
                    row_number,
                    column,
                    SalesContractIssueCode.DECIMAL_INVALID_FORMAT,
                    "Use digits and an optional decimal point; symbols and exponents are invalid.",
                )
            ]
        try:
            parsed = Decimal(value)
        except InvalidOperation:
            return None, [
                _issue(
                    row_number,
                    column,
                    SalesContractIssueCode.DECIMAL_INVALID_FORMAT,
                    "The decimal value is invalid.",
                )
            ]
        try:
            validator(parsed)
        except ValidationError:
            return None, [
                _issue(
                    row_number,
                    column,
                    SalesContractIssueCode.DECIMAL_PRECISION_EXCEEDED,
                    "The value exceeds the supported digits or decimal places.",
                )
            ]
        return parsed, []

    def validate(
        self,
        *,
        row_number: int,
        values: Mapping[str, str],
    ) -> SalesRowValidationResult:
        self.note_row()

        issues: list[SalesRowIssue] = []
        source_record_id, field_issues = self._text(
            values["source_record_id"],
            row_number=row_number,
            column="source_record_id",
            required=True,
            max_length=255,
        )
        issues.extend(field_issues)
        transaction_date, field_issues = self._date(
            values["transaction_date"],
            row_number=row_number,
        )
        issues.extend(field_issues)
        product_code, field_issues = self._text(
            values["product_code"],
            row_number=row_number,
            column="product_code",
            required=True,
            max_length=64,
            uppercase=True,
        )
        issues.extend(field_issues)
        territory_code, field_issues = self._text(
            values["territory_code"],
            row_number=row_number,
            column="territory_code",
            required=True,
            max_length=64,
            uppercase=True,
        )
        issues.extend(field_issues)
        hospital_code, field_issues = self._text(
            values["hospital_code"],
            row_number=row_number,
            column="hospital_code",
            required=False,
            max_length=64,
            uppercase=True,
        )
        issues.extend(field_issues)
        representative_code, field_issues = self._text(
            values["sales_representative_code"],
            row_number=row_number,
            column="sales_representative_code",
            required=False,
            max_length=64,
            uppercase=True,
        )
        issues.extend(field_issues)
        quantity, field_issues = self._decimal(
            values["quantity"],
            row_number=row_number,
            column="quantity",
            validator=_QUANTITY_VALIDATOR,
        )
        issues.extend(field_issues)
        revenue_amount, field_issues = self._decimal(
            values["revenue_amount"],
            row_number=row_number,
            column="revenue_amount",
            validator=_REVENUE_VALIDATOR,
        )
        issues.extend(field_issues)
        raw_currency_code = values["currency_code"].strip()
        currency_code, field_issues = self._text(
            raw_currency_code,
            row_number=row_number,
            column="currency_code",
            required=True,
            max_length=3,
            uppercase=True,
        )
        issues.extend(field_issues)

        if currency_code is not None and (
            not raw_currency_code.isascii()
            or not _CURRENCY_PATTERN.fullmatch(currency_code)
        ):
            issues.append(
                _issue(
                    row_number,
                    "currency_code",
                    SalesContractIssueCode.CURRENCY_CODE_INVALID,
                    "Use a three-letter ASCII currency code such as INR or USD.",
                )
            )
        if quantity is not None and quantity == 0:
            issues.append(
                _issue(
                    row_number,
                    "quantity",
                    SalesContractIssueCode.QUANTITY_ZERO,
                    "Quantity cannot be zero.",
                )
            )
        if (
            quantity is not None
            and revenue_amount is not None
            and (
                (quantity > 0 and revenue_amount < 0)
                or (quantity < 0 and revenue_amount > 0)
            )
        ):
            issues.append(
                _issue(
                    row_number,
                    "revenue_amount",
                    SalesContractIssueCode.MEASURE_SIGN_MISMATCH,
                    "Sales require non-negative revenue; returns require non-positive revenue.",
                )
            )

        if source_record_id is not None:
            if source_record_id in self._source_record_ids:
                issues.append(
                    _issue(
                        row_number,
                        "source_record_id",
                        SalesContractIssueCode.DUPLICATE_SOURCE_RECORD_ID,
                        "This source record ID appears more than once in the file.",
                    )
                )
            else:
                self._source_record_ids.add(source_record_id)

        if issues:
            return SalesRowValidationResult(
                row_number=row_number,
                row=None,
                issues=tuple(issues),
            )

        return SalesRowValidationResult(
            row_number=row_number,
            row=SalesRowV1(
                source_record_id=source_record_id,
                transaction_date=transaction_date,
                product_code=product_code,
                territory_code=territory_code,
                hospital_code=hospital_code,
                sales_representative_code=representative_code,
                quantity=quantity,
                revenue_amount=revenue_amount,
                currency_code=currency_code,
            ),
            issues=(),
        )


def iter_sales_rows_v1_csv(
    text_stream: TextIO,
    *,
    max_rows: int,
) -> Iterator[SalesRowValidationResult]:
    """Yield syntactic V1 validation results without loading the file at once."""

    reader = csv.reader(text_stream, dialect="excel", strict=True)
    try:
        try:
            headers = validate_sales_headers(next(reader))
        except StopIteration as error:
            raise SalesFileContractError(
                SalesContractIssueCode.FILE_NO_DATA_ROWS,
                "The sales file is empty.",
            ) from error

        validator = SalesRowsV1Validator(max_rows=max_rows)
        while True:
            row_number = reader.line_num + 1
            try:
                raw_row = next(reader)
            except StopIteration:
                break
            if not raw_row or all(not value.strip() for value in raw_row):
                continue
            if len(raw_row) != len(headers):
                validator.note_row()
                yield SalesRowValidationResult(
                    row_number=row_number,
                    row=None,
                    issues=(
                        _issue(
                            row_number,
                            "__row__",
                            SalesContractIssueCode.ROW_COLUMN_COUNT_MISMATCH,
                            f"Expected {len(headers)} cells; found {len(raw_row)}.",
                        ),
                    ),
                )
                continue
            yield validator.validate(
                row_number=row_number,
                values=dict(zip(headers, raw_row, strict=True)),
            )

        if validator.row_count == 0:
            raise SalesFileContractError(
                SalesContractIssueCode.FILE_NO_DATA_ROWS,
                "The sales file contains a header but no data rows.",
            )
    except csv.Error as error:
        raise SalesFileContractError(
            SalesContractIssueCode.CSV_MALFORMED,
            f"Malformed CSV near row {reader.line_num}.",
        ) from error
