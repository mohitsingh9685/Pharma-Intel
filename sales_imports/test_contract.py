import hashlib
import io
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from .contracts import (
    SALES_ROWS_V1_COLUMNS,
    SalesContractIssueCode,
    SalesFileContractError,
    iter_sales_rows_v1_csv,
    validate_sales_headers,
)
from .synthetic import (
    DEMO_SALES_ROWS,
    sales_demo_csv_bytes,
)


def _csv(*rows):
    return ",".join(SALES_ROWS_V1_COLUMNS) + "\n" + "\n".join(rows) + "\n"


VALID_ROW = (
    "SALE-001,2026-01-05,PROD-001,TER-001,HOS-001,REP-001,"
    "10,1250.0000,INR"
)


class SalesRowsV1ContractTests(TestCase):
    def test_committed_demo_is_deterministic_and_valid(self):
        sample_path = (
            Path(__file__).resolve().parents[1]
            / "samples"
            / "sales"
            / "sales_rows_v1_demo.csv"
        )

        generated_bytes = sales_demo_csv_bytes()
        self.assertEqual(sample_path.read_bytes(), generated_bytes)
        self.assertEqual(len(generated_bytes), 1_071)
        self.assertEqual(
            hashlib.sha256(generated_bytes).hexdigest(),
            "93baef35820567e846126608080bfc60967066d89345e8800c08e10abf4d546e",
        )
        results = list(
            iter_sales_rows_v1_csv(
                io.StringIO(sample_path.read_text(encoding="utf-8")),
                max_rows=100,
            )
        )

        self.assertEqual(len(results), len(DEMO_SALES_ROWS))
        self.assertTrue(all(result.is_valid for result in results))
        self.assertIsNone(results[2].row.hospital_code)
        self.assertEqual(results[3].row.quantity, Decimal("-1"))
        self.assertEqual(results[3].row.revenue_amount, Decimal("-125.0000"))
        self.assertIsNone(results[4].row.sales_representative_code)

    def test_column_order_bom_and_normalization_are_supported(self):
        text = (
            "\ufeffcurrency_code,quantity,revenue_amount,territory_code,"
            "product_code,transaction_date,source_record_id,hospital_code,"
            "sales_representative_code\n"
            " inr ,1.250,50.0000, ter-001 , prod-001 ,2026-02-03,"
            " sale-9 ,, rep-1 \n"
        )

        result = list(iter_sales_rows_v1_csv(io.StringIO(text), max_rows=1))[0]

        self.assertTrue(result.is_valid)
        self.assertEqual(result.row.source_record_id, "sale-9")
        self.assertEqual(result.row.transaction_date, date(2026, 2, 3))
        self.assertEqual(result.row.product_code, "PROD-001")
        self.assertEqual(result.row.territory_code, "TER-001")
        self.assertIsNone(result.row.hospital_code)
        self.assertEqual(result.row.sales_representative_code, "REP-1")
        self.assertEqual(result.row.currency_code, "INR")

    def test_headers_must_match_the_versioned_contract_exactly(self):
        cases = (
            (
                (*SALES_ROWS_V1_COLUMNS[:-1],),
                SalesContractIssueCode.SCHEMA_MISSING_COLUMN,
            ),
            (
                (*SALES_ROWS_V1_COLUMNS, "unexpected"),
                SalesContractIssueCode.SCHEMA_UNKNOWN_COLUMN,
            ),
            (
                (*SALES_ROWS_V1_COLUMNS, "currency_code"),
                SalesContractIssueCode.SCHEMA_DUPLICATE_COLUMN,
            ),
        )

        for headers, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(SalesFileContractError) as raised:
                    validate_sales_headers(headers)
                self.assertEqual(raised.exception.code, expected_code)

    def test_invalid_values_return_stable_issue_codes(self):
        invalid_row = (
            "SALE-001,2026-02-30,,TER-001,HOS-001,REP-001,"
            "0,1e3,US1"
        )

        result = list(
            iter_sales_rows_v1_csv(io.StringIO(_csv(invalid_row)), max_rows=10)
        )[0]

        self.assertFalse(result.is_valid)
        self.assertIsNone(result.row)
        self.assertEqual(
            {issue.code for issue in result.issues},
            {
                SalesContractIssueCode.DATE_INVALID_FORMAT,
                SalesContractIssueCode.REQUIRED_VALUE_MISSING,
                SalesContractIssueCode.QUANTITY_ZERO,
                SalesContractIssueCode.DECIMAL_INVALID_FORMAT,
                SalesContractIssueCode.CURRENCY_CODE_INVALID,
            },
        )

    def test_blank_required_values_and_unicode_currency_are_rejected(self):
        row = ",2026-01-05," + ("ß" * 64) + ",TER-001,,,1,,ßa"

        result = list(
            iter_sales_rows_v1_csv(io.StringIO(_csv(row)), max_rows=10)
        )[0]

        codes_by_column = {
            (issue.column, issue.code) for issue in result.issues
        }
        self.assertIn(
            ("source_record_id", SalesContractIssueCode.REQUIRED_VALUE_MISSING),
            codes_by_column,
        )
        self.assertIn(
            ("product_code", SalesContractIssueCode.VALUE_TOO_LONG),
            codes_by_column,
        )
        self.assertIn(
            ("revenue_amount", SalesContractIssueCode.REQUIRED_VALUE_MISSING),
            codes_by_column,
        )
        self.assertIn(
            ("currency_code", SalesContractIssueCode.CURRENCY_CODE_INVALID),
            codes_by_column,
        )

    def test_row_number_is_the_physical_start_line_for_multiline_records(self):
        multiline_row = (
            '"SALE\n001",2026-01-05,PROD-001,TER-001,,,1,10.0000,INR'
        )

        results = list(
            iter_sales_rows_v1_csv(
                io.StringIO(_csv(multiline_row, VALID_ROW)),
                max_rows=10,
            )
        )

        self.assertEqual(results[0].row_number, 2)
        self.assertEqual(results[1].row_number, 4)

    def test_duplicate_source_id_and_wrong_cell_count_are_reported(self):
        normalized_duplicate = VALID_ROW.replace("SALE-001", " SALE-001 ", 1)
        results = list(
            iter_sales_rows_v1_csv(
                io.StringIO(
                    _csv(VALID_ROW, normalized_duplicate, "too,few,cells")
                ),
                max_rows=3,
            )
        )

        self.assertTrue(results[0].is_valid)
        self.assertEqual(
            results[1].issues[0].code,
            SalesContractIssueCode.DUPLICATE_SOURCE_RECORD_ID,
        )
        self.assertEqual(
            results[2].issues[0].code,
            SalesContractIssueCode.ROW_COLUMN_COUNT_MISMATCH,
        )

    def test_empty_malformed_and_oversized_files_fail_at_file_level(self):
        cases = (
            (
                ",".join(SALES_ROWS_V1_COLUMNS) + "\n",
                10,
                SalesContractIssueCode.FILE_NO_DATA_ROWS,
            ),
            (
                _csv('"unterminated'),
                10,
                SalesContractIssueCode.CSV_MALFORMED,
            ),
            (
                _csv("too,few", "still,too,few"),
                1,
                SalesContractIssueCode.FILE_ROW_LIMIT_EXCEEDED,
            ),
        )

        for text, max_rows, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(SalesFileContractError) as raised:
                    list(
                        iter_sales_rows_v1_csv(
                            io.StringIO(text),
                            max_rows=max_rows,
                        )
                    )
                self.assertEqual(raised.exception.code, expected_code)
