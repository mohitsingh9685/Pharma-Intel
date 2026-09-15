import csv
import hashlib
import io
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import DatabaseError, close_old_connections, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from business_data.models import CalendarDate, SalesTransaction
from master_data.models import Hospital, Product, SalesRepresentative, Territory

from .contracts import SALES_ROWS_V1_COLUMNS
from .models import (
    SalesImport,
    SalesImportAttempt,
    SalesImportIssue,
    SalesImportJob,
    SalesImportStagedRow,
)
from .processing import (
    _DownloadLeaseHeartbeat,
    _complete_stored_object_failure,
    _source_session_lock,
    _stage_csv,
    process_claim,
)
from .queue import SalesImportClaim, SalesImportClaimLost, claim_next_job, enqueue_import
from .storage import (
    OriginalFileDownloadError,
    OriginalFileKMSProvenanceError,
    OriginalFileNotFoundError,
    OriginalFileVerificationError,
)
from .synthetic import (
    DEMO_HOSPITAL_CODE,
    DEMO_PRODUCT_CODE,
    DEMO_REPRESENTATIVE_CODE,
    DEMO_SALES_ROWS,
    DEMO_SOURCE_SYSTEM,
    DEMO_TERRITORY_CODE,
    sales_demo_csv_bytes,
)


TEST_KMS_KEY_ARN = "arn:aws:kms:ap-south-1:123456789012:key/sales-import-test"


class InMemoryOriginalFileStorage:
    """Expose test bytes through the same context-manager API as S3 storage."""

    def __init__(self, content: bytes | None = None, *, error=None):
        self.content = content
        self.error = error
        self.open_calls = []

    @contextmanager
    def open_version(self, **kwargs):
        self.open_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        with io.BytesIO(self.content) as binary_file:
            yield binary_file


def _csv_bytes(*rows) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(SALES_ROWS_V1_COLUMNS)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


@override_settings(
    DEBUG=True,
    SALES_IMPORT_DB_BATCH_SIZE=3,
    SALES_IMPORT_MAX_ISSUES=100,
    SALES_IMPORT_SOURCE_LOCK_WAIT_SECONDS=300,
)
class SalesImportProcessingTests(TransactionTestCase):
    """End-to-end worker tests against PostgreSQL with an in-memory S3 seam."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            email="processing-tests@example.com",
            password="test-only-password",
        )
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        call_command(
            "prepare_sales_demo",
            output=Path(self.temporary_directory.name) / "sales-demo.csv",
            stdout=io.StringIO(),
        )

    def _claim_for(self, content: bytes, *, source_system=DEMO_SOURCE_SYSTEM):
        digest = hashlib.sha256(content).hexdigest()
        sales_import = SalesImport.objects.create(
            source_system=source_system,
            original_filename="sales.csv",
            content_type="text/csv",
            file_format=SalesImport.FileFormat.CSV,
            size_bytes=len(content),
            sha256=digest,
            storage_bucket="private-sales-imports",
            storage_kms_key_arn=TEST_KMS_KEY_ARN,
            storage_key=f"sales-imports/original/{digest}-{uuid4()}.csv",
            storage_version_id="version-1",
            uploaded_by=self.user,
            status=SalesImport.Status.RECEIVED,
            received_at=timezone.now(),
        )
        enqueue_import(sales_import)
        claim = claim_next_job(worker_id="processing-test-worker")
        self.assertIsNotNone(claim)
        return sales_import, claim

    def _process(self, content: bytes, *, storage=None):
        sales_import, claim = self._claim_for(content)
        fake_storage = storage or InMemoryOriginalFileStorage(content)
        result = process_claim(claim, storage=fake_storage)
        return sales_import, result, fake_storage

    def _create_existing_fact(self, row, *, quantity=None, revenue_amount=None):
        (
            source_record_id,
            transaction_date,
            _product_code,
            _territory_code,
            hospital_code,
            representative_code,
            row_quantity,
            row_revenue,
            currency_code,
        ) = row
        return SalesTransaction.objects.create(
            calendar_date=CalendarDate.objects.get(
                date=date.fromisoformat(transaction_date)
            ),
            product=Product.objects.get(code=DEMO_PRODUCT_CODE),
            territory=Territory.objects.get(code=DEMO_TERRITORY_CODE),
            hospital=(
                Hospital.objects.get(code=DEMO_HOSPITAL_CODE)
                if hospital_code
                else None
            ),
            sales_representative=(
                SalesRepresentative.objects.get(code=DEMO_REPRESENTATIVE_CODE)
                if representative_code
                else None
            ),
            quantity=Decimal(quantity if quantity is not None else row_quantity),
            revenue_amount=Decimal(
                revenue_amount if revenue_amount is not None else row_revenue
            ),
            currency_code=currency_code,
            source_system=DEMO_SOURCE_SYSTEM,
            source_record_id=source_record_id,
            created_by=self.user,
        )

    def test_canonical_demo_publishes_all_ten_rows(self):
        content = sales_demo_csv_bytes()

        sales_import, result, storage = self._process(content)

        self.assertEqual(result.status, SalesImportJob.Status.PUBLISHED)
        self.assertEqual(result.counts.total_rows, 10)
        self.assertEqual(result.counts.inserted_rows, 10)
        self.assertEqual(result.counts.reused_rows, 0)
        self.assertEqual(SalesTransaction.objects.count(), 10)
        self.assertEqual(SalesImportIssue.objects.count(), 0)
        self.assertEqual(
            SalesImportStagedRow.objects.filter(
                attempt__job_id=sales_import.pk,
                outcome=SalesImportStagedRow.Outcome.PUBLISHED,
                sales_transaction__isnull=False,
            ).count(),
            10,
        )
        self.assertEqual(storage.open_calls[0]["version_id"], "version-1")
        self.assertTrue(callable(storage.open_calls[0]["progress_callback"]))
        job = SalesImportJob.objects.get(pk=sales_import.pk)
        self.assertEqual(job.valid_rows, 10)
        self.assertEqual(job.invalid_rows, 0)

    def test_row_contract_rejection_persists_issue_and_creates_no_facts(self):
        invalid_row = list(DEMO_SALES_ROWS[0])
        invalid_row[6] = "0"

        sales_import, result, _ = self._process(_csv_bytes(invalid_row))

        self.assertEqual(result.status, SalesImportJob.Status.REJECTED)
        self.assertEqual(result.counts.total_rows, 1)
        self.assertEqual(result.counts.invalid_rows, 1)
        self.assertEqual(SalesTransaction.objects.count(), 0)
        issue = SalesImportIssue.objects.get(attempt__job_id=sales_import.pk)
        self.assertEqual(issue.row_number, 2)
        self.assertEqual(issue.column, "quantity")
        self.assertEqual(issue.code, "QUANTITY_ZERO")
        self.assertEqual(issue.phase, SalesImportIssue.Phase.CONTRACT)

    def test_invalid_only_file_batches_progress_and_caps_issue_details(self):
        invalid_rows = []
        for index in range(5):
            row = list(DEMO_SALES_ROWS[0])
            row[0] = f"INVALID-{index}"
            row[6] = "0"
            invalid_rows.append(row)

        with self.settings(SALES_IMPORT_MAX_ISSUES=3):
            sales_import, result, _ = self._process(_csv_bytes(*invalid_rows))

        self.assertEqual(result.status, SalesImportJob.Status.REJECTED)
        self.assertEqual(result.counts.total_rows, 5)
        self.assertEqual(result.counts.invalid_rows, 5)
        self.assertEqual(result.counts.issue_count, 5)
        issues = SalesImportIssue.objects.filter(attempt__job_id=sales_import.pk)
        self.assertEqual(issues.count(), 3)
        self.assertEqual(
            issues.filter(code="ISSUE_LIMIT_REACHED").count(),
            1,
        )
        self.assertEqual(SalesImportStagedRow.objects.count(), 0)
        self.assertEqual(SalesTransaction.objects.count(), 0)

    def test_missing_master_data_rejects_entire_file(self):
        missing_product_row = list(DEMO_SALES_ROWS[0])
        missing_product_row[2] = "UNKNOWN-PRODUCT"

        sales_import, result, _ = self._process(_csv_bytes(missing_product_row))

        self.assertEqual(result.status, SalesImportJob.Status.REJECTED)
        self.assertEqual(result.counts.invalid_rows, 1)
        self.assertEqual(SalesTransaction.objects.count(), 0)
        issue = SalesImportIssue.objects.get(
            attempt__job_id=sales_import.pk,
            code="PRODUCT_NOT_FOUND",
        )
        self.assertEqual(issue.row_number, 2)
        self.assertEqual(issue.column, "product_code")
        self.assertEqual(issue.phase, SalesImportIssue.Phase.MASTER_DATA)

    def test_identical_fact_is_reused_while_new_row_is_inserted(self):
        existing = self._create_existing_fact(DEMO_SALES_ROWS[0])
        content = _csv_bytes(DEMO_SALES_ROWS[0], DEMO_SALES_ROWS[1])

        sales_import, result, _ = self._process(content)

        self.assertEqual(result.status, SalesImportJob.Status.PUBLISHED)
        self.assertEqual(result.counts.total_rows, 2)
        self.assertEqual(result.counts.reused_rows, 1)
        self.assertEqual(result.counts.inserted_rows, 1)
        self.assertEqual(SalesTransaction.objects.count(), 2)
        reused_row = SalesImportStagedRow.objects.get(
            attempt__job_id=sales_import.pk,
            source_record_id=DEMO_SALES_ROWS[0][0],
        )
        inserted_row = SalesImportStagedRow.objects.get(
            attempt__job_id=sales_import.pk,
            source_record_id=DEMO_SALES_ROWS[1][0],
        )
        self.assertEqual(reused_row.outcome, SalesImportStagedRow.Outcome.REUSED)
        self.assertEqual(reused_row.sales_transaction_id, existing.pk)
        self.assertEqual(
            inserted_row.outcome,
            SalesImportStagedRow.Outcome.PUBLISHED,
        )
        self.assertIsNotNone(inserted_row.sales_transaction_id)

    def test_three_concurrent_imports_serialize_shared_source_record(self):
        shared_row = DEMO_SALES_ROWS[0]
        contents = [
            _csv_bytes(shared_row, unique_row)
            for unique_row in DEMO_SALES_ROWS[1:4]
        ]
        imports_and_claims = [self._claim_for(content) for content in contents]
        staged = Barrier(3)

        def stage_then_wait(*args, **kwargs):
            result = _stage_csv(*args, **kwargs)
            staged.wait(timeout=20)
            return result

        def run_worker(claim, content):
            close_old_connections()
            try:
                return process_claim(
                    claim,
                    storage=InMemoryOriginalFileStorage(content),
                )
            finally:
                close_old_connections()

        with patch(
            "sales_imports.processing._stage_csv",
            side_effect=stage_then_wait,
        ):
            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = [
                    executor.submit(run_worker, claim, content)
                    for (_, claim), content in zip(imports_and_claims, contents)
                ]
                results = [future.result(timeout=30) for future in futures]

        import_ids = [sales_import.pk for sales_import, _ in imports_and_claims]
        jobs = list(
            SalesImportJob.objects.filter(pk__in=import_ids).order_by("pk")
        )
        attempts = SalesImportAttempt.objects.filter(job_id__in=import_ids)

        self.assertEqual(len(jobs), 3)
        self.assertTrue(
            all(job.status == SalesImportJob.Status.PUBLISHED for job in jobs)
        )
        self.assertTrue(
            all(result.status == SalesImportJob.Status.PUBLISHED for result in results)
        )
        self.assertEqual(sum(job.inserted_rows for job in jobs), 4)
        self.assertEqual(sum(job.reused_rows for job in jobs), 2)
        self.assertTrue(
            all(
                job.total_rows == 2
                and job.valid_rows == 2
                and job.invalid_rows == 0
                and job.issue_count == 0
                and job.inserted_rows + job.reused_rows == 2
                and job.attempt_count == 1
                and job.last_error_code == ""
                for job in jobs
            )
        )
        self.assertEqual(
            SalesTransaction.objects.filter(
                source_system=DEMO_SOURCE_SYSTEM,
                source_record_id=shared_row[0],
            ).count(),
            1,
        )
        self.assertEqual(SalesTransaction.objects.count(), 4)
        self.assertEqual(attempts.count(), 3)
        self.assertEqual(
            attempts.filter(
                outcome=SalesImportAttempt.Outcome.PUBLISHED,
                error_code="",
            ).count(),
            3,
        )
        self.assertEqual(SalesImportIssue.objects.count(), 0)

    def test_conflicting_existing_fact_rolls_back_every_new_row(self):
        existing = self._create_existing_fact(
            DEMO_SALES_ROWS[0],
            quantity="11",
            revenue_amount="1375.0000",
        )
        content = _csv_bytes(DEMO_SALES_ROWS[0], DEMO_SALES_ROWS[1])

        sales_import, result, _ = self._process(content)

        self.assertEqual(result.status, SalesImportJob.Status.REJECTED)
        self.assertEqual(result.counts.invalid_rows, 1)
        self.assertEqual(result.counts.inserted_rows, 0)
        self.assertEqual(SalesTransaction.objects.count(), 1)
        self.assertEqual(
            SalesTransaction.objects.get().pk,
            existing.pk,
        )
        self.assertFalse(
            SalesTransaction.objects.filter(
                source_system=DEMO_SOURCE_SYSTEM,
                source_record_id=DEMO_SALES_ROWS[1][0],
            ).exists()
        )
        issue = SalesImportIssue.objects.get(
            attempt__job_id=sales_import.pk,
            code="SOURCE_RECORD_CONFLICT",
        )
        self.assertEqual(issue.row_number, 2)
        self.assertEqual(issue.phase, SalesImportIssue.Phase.IDEMPOTENCY)

    def test_retryable_download_error_schedules_retry_without_staging(self):
        content = sales_demo_csv_bytes()
        sales_import, claim = self._claim_for(content)
        storage = InMemoryOriginalFileStorage(
            error=OriginalFileDownloadError("temporary S3 outage")
        )

        result = process_claim(claim, storage=storage)

        self.assertEqual(result.status, SalesImportJob.Status.RETRY_WAIT)
        self.assertEqual(SalesTransaction.objects.count(), 0)
        self.assertEqual(SalesImportStagedRow.objects.count(), 0)
        job = SalesImportJob.objects.get(pk=sales_import.pk)
        attempt = SalesImportAttempt.objects.get(pk=claim.attempt_id)
        self.assertEqual(job.last_error_code, "S3_DOWNLOAD_RETRYABLE")
        self.assertEqual(job.attempt_count, 1)
        self.assertGreater(job.available_at, attempt.finished_at)
        self.assertEqual(
            attempt.outcome,
            SalesImportAttempt.Outcome.RETRYABLE_FAILURE,
        )

    def test_missing_kms_provenance_is_operational_and_preserves_intake(self):
        content = sales_demo_csv_bytes()
        sales_import, claim = self._claim_for(content)
        received_at = sales_import.received_at
        version_id = sales_import.storage_version_id

        result = process_claim(
            claim,
            storage=InMemoryOriginalFileStorage(
                error=OriginalFileKMSProvenanceError(
                    "legacy import needs KMS backfill"
                )
            ),
        )

        sales_import.refresh_from_db()
        job = SalesImportJob.objects.get(pk=sales_import.pk)
        self.assertEqual(result.status, SalesImportJob.Status.FAILED)
        self.assertEqual(job.last_error_code, "KMS_PROVENANCE_MISSING")
        self.assertEqual(sales_import.status, SalesImport.Status.RECEIVED)
        self.assertEqual(sales_import.failure_code, SalesImport.FailureCode.NONE)
        self.assertEqual(sales_import.received_at, received_at)
        self.assertEqual(sales_import.storage_version_id, version_id)

    def test_permanent_stored_object_failures_release_duplicate_guard(self):
        content = sales_demo_csv_bytes()
        cases = (
            (
                OriginalFileNotFoundError("exact version missing"),
                SalesImport.FailureCode.OBJECT_MISSING,
                "S3_VERSION_NOT_FOUND",
            ),
            (
                OriginalFileVerificationError("object metadata changed"),
                SalesImport.FailureCode.VERIFICATION_FAILED,
                "S3_VERIFICATION_FAILED",
            ),
        )

        for error, intake_code, job_code in cases:
            with self.subTest(intake_code=intake_code):
                sales_import, claim = self._claim_for(
                    content,
                    source_system=f"{DEMO_SOURCE_SYSTEM}-{intake_code}",
                )
                received_at = sales_import.received_at
                version_id = sales_import.storage_version_id
                kms_key_arn = sales_import.storage_kms_key_arn

                result = process_claim(
                    claim,
                    storage=InMemoryOriginalFileStorage(error=error),
                )

                sales_import.refresh_from_db()
                job = SalesImportJob.objects.get(pk=sales_import.pk)
                self.assertEqual(result.status, SalesImportJob.Status.FAILED)
                self.assertEqual(job.last_error_code, job_code)
                self.assertEqual(sales_import.status, SalesImport.Status.FAILED)
                self.assertEqual(sales_import.failure_code, intake_code)
                self.assertEqual(sales_import.received_at, received_at)
                self.assertEqual(sales_import.storage_version_id, version_id)
                self.assertEqual(sales_import.storage_kms_key_arn, kms_key_arn)
                self.assertFalse(
                    SalesImport.objects.filter(
                        source_system=sales_import.source_system,
                        sha256=sales_import.sha256,
                        status__in=(
                            SalesImport.Status.RECEIVING,
                            SalesImport.Status.RECEIVED,
                        ),
                    ).exists()
                )

    def test_lost_claim_rolls_back_stored_object_failure(self):
        content = sales_demo_csv_bytes()
        sales_import, claim = self._claim_for(content)

        with (
            patch(
                "sales_imports.processing.complete_job",
                side_effect=SalesImportClaimLost("claim replaced"),
            ),
            self.assertRaises(SalesImportClaimLost),
        ):
            _complete_stored_object_failure(
                claim,
                intake_failure_code=SalesImport.FailureCode.OBJECT_MISSING,
                job_error_code="S3_VERSION_NOT_FOUND",
                message="exact version missing",
                using="default",
            )

        sales_import.refresh_from_db()
        self.assertEqual(sales_import.status, SalesImport.Status.RECEIVED)
        self.assertEqual(sales_import.failure_code, SalesImport.FailureCode.NONE)

    def test_download_progress_rate_limits_database_heartbeats(self):
        claim = SalesImportClaim(
            job_id=uuid4(),
            sales_import_id=uuid4(),
            attempt_id=uuid4(),
            attempt_number=1,
            lease_token=uuid4(),
            worker_id="processing-test-worker",
            lease_expires_at=timezone.now(),
        )

        with (
            patch(
                "sales_imports.processing.time.monotonic",
                side_effect=(0.0, 10.0, 59.0, 60.0, 61.0, 120.0),
            ),
            patch("sales_imports.processing.heartbeat_job") as heartbeat,
        ):
            progress_callback = _DownloadLeaseHeartbeat(
                claim=claim,
                using="default",
                interval_seconds=60.0,
            )
            for downloaded_bytes in (64, 128, 192, 256, 320):
                progress_callback(downloaded_bytes)

        self.assertEqual(heartbeat.call_count, 2)
        heartbeat.assert_called_with(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            using="default",
        )

    def test_source_lock_wait_is_bounded_and_renews_lease(self):
        claim = SalesImportClaim(
            job_id=uuid4(),
            sales_import_id=uuid4(),
            attempt_id=uuid4(),
            attempt_number=1,
            lease_token=uuid4(),
            worker_id="processing-test-worker",
            lease_expires_at=timezone.now(),
        )
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchone.side_effect = ((False,), (False,))
        connection = MagicMock()
        connection.cursor.return_value = cursor

        with (
            patch("sales_imports.processing.connections", {"default": connection}),
            patch(
                "sales_imports.processing.time.monotonic",
                side_effect=(0.0, 61.0, 301.0),
            ),
            patch("sales_imports.processing.time.sleep") as sleep,
            patch("sales_imports.processing.heartbeat_job") as heartbeat,
            self.assertRaisesMessage(DatabaseError, "Timed out waiting"),
        ):
            with _source_session_lock(
                claim=claim,
                source_system="ERP",
                using="default",
            ):
                self.fail("A timed-out source lock must not enter its body.")

        heartbeat.assert_called_once_with(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            using="default",
        )
        sleep.assert_called_once_with(0.25)

    def test_source_unlock_failure_preserves_committed_publication(self):
        content = sales_demo_csv_bytes()
        sales_import, claim = self._claim_for(content)

        def fail_advisory_unlock(execute, sql, params, many, context):
            if "pg_advisory_unlock" in sql:
                raise DatabaseError("simulated advisory unlock failure")
            return execute(sql, params, many, context)

        with (
            connections["default"].execute_wrapper(fail_advisory_unlock),
            self.assertLogs("sales_imports.processing", level="ERROR") as logs,
        ):
            result = process_claim(
                claim,
                storage=InMemoryOriginalFileStorage(content),
            )

        job = SalesImportJob.objects.get(pk=sales_import.pk)
        attempt = SalesImportAttempt.objects.get(pk=claim.attempt_id)
        self.assertEqual(result.status, SalesImportJob.Status.PUBLISHED)
        self.assertEqual(job.status, SalesImportJob.Status.PUBLISHED)
        self.assertEqual(attempt.outcome, SalesImportAttempt.Outcome.PUBLISHED)
        self.assertEqual(job.attempt_count, 1)
        self.assertEqual(SalesTransaction.objects.count(), len(DEMO_SALES_ROWS))
        self.assertTrue(
            any(
                "Could not release the sales-import source lock" in message
                for message in logs.output
            )
        )
