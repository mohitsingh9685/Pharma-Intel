import io
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from .models import SalesImport, SalesImportAttempt, SalesImportJob
from .processing import SalesImportProcessingResult
from .queue import (
    SalesImportJobCounts,
    claim_next_job,
    enqueue_import,
    schedule_retry,
)


TEST_KMS_KEY_ARN = "arn:aws:kms:ap-south-1:123456789012:key/sales-import-test"


class RetrySalesImportCommandTests(TestCase):
    def setUp(self):
        super().setUp()
        self.now = timezone.now().replace(microsecond=0)
        self.user = get_user_model().objects.create_user(
            email="retry-command@example.com",
            password="test-only-password",
        )

    def create_received_import(self):
        sales_import_id = uuid.uuid4()
        return SalesImport.objects.create(
            id=sales_import_id,
            source_system="ERP",
            original_filename="sales.csv",
            content_type="text/csv",
            file_format=SalesImport.FileFormat.CSV,
            size_bytes=100,
            sha256="a" * 64,
            storage_bucket="private-sales-imports",
            storage_kms_key_arn=TEST_KMS_KEY_ARN,
            storage_key=f"sales-imports/original/{sales_import_id}.csv",
            storage_version_id="version-1",
            uploaded_by=self.user,
            status=SalesImport.Status.RECEIVED,
            received_at=self.now,
        )

    def create_failed_job(self):
        sales_import = self.create_received_import()
        job = enqueue_import(
            sales_import,
            max_attempts=1,
            available_at=self.now,
        )
        claim = claim_next_job(worker_id="failed-command-worker", now=self.now)
        schedule_retry(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            error_code="S3_DOWNLOAD_RETRYABLE",
            error_message="Temporary download failure.",
            now=self.now + timedelta(seconds=1),
        )
        job.refresh_from_db()
        return job, claim

    def test_requeues_job_and_reports_preserved_attempt_history(self):
        job, first_claim = self.create_failed_job()
        stdout = io.StringIO()

        call_command(
            "retry_sales_import",
            str(job.pk),
            additional_attempts=2,
            stdout=stdout,
        )

        job.refresh_from_db()
        self.assertEqual(job.status, SalesImportJob.Status.QUEUED)
        self.assertEqual(job.attempt_count, 1)
        self.assertEqual(job.max_attempts, 3)
        self.assertTrue(
            SalesImportAttempt.objects.filter(pk=first_claim.attempt_id).exists()
        )
        self.assertIn(f"Sales-import job {job.pk} queued", stdout.getvalue())
        self.assertIn("attempts used=1, attempt limit=3", stdout.getvalue())

    def test_rejects_invalid_or_missing_job_id(self):
        with self.assertRaisesMessage(CommandError, "must be a valid UUID"):
            call_command("retry_sales_import", "not-a-uuid")

        missing_id = uuid.uuid4()
        with self.assertRaisesMessage(
            CommandError,
            f"No sales-import job exists for {missing_id}",
        ):
            call_command("retry_sales_import", str(missing_id))

    def test_converts_invalid_attempt_extension_to_command_error(self):
        job, _ = self.create_failed_job()

        with self.assertRaisesMessage(
            CommandError,
            "additional_attempts must be a positive integer",
        ):
            call_command(
                "retry_sales_import",
                str(job.pk),
                additional_attempts=0,
            )

        job.refresh_from_db()
        self.assertEqual(job.status, SalesImportJob.Status.FAILED)
        self.assertEqual(job.max_attempts, 1)

    def test_converts_nonretryable_state_to_command_error(self):
        sales_import = self.create_received_import()
        job = enqueue_import(sales_import, available_at=self.now)

        with self.assertRaisesMessage(
            CommandError,
            "Only failed or rejected sales-import jobs can be retried",
        ):
            call_command("retry_sales_import", str(job.pk))

        job.refresh_from_db()
        self.assertEqual(job.status, SalesImportJob.Status.QUEUED)


class ProcessSalesImportsCommandTests(SimpleTestCase):
    def test_processes_only_the_requested_number_of_jobs(self):
        first_id = uuid.uuid4()
        second_id = uuid.uuid4()
        claims = [
            SimpleNamespace(job_id=first_id),
            SimpleNamespace(job_id=second_id),
        ]
        results = [
            SalesImportProcessingResult(
                job_id=first_id,
                status=SalesImportJob.Status.PUBLISHED,
                counts=SalesImportJobCounts(
                    total_rows=2,
                    valid_rows=2,
                    inserted_rows=2,
                ),
            ),
            SalesImportProcessingResult(
                job_id=second_id,
                status=SalesImportJob.Status.REJECTED,
                counts=SalesImportJobCounts(
                    total_rows=1,
                    invalid_rows=1,
                    issue_count=1,
                ),
            ),
        ]
        stdout = io.StringIO()

        with (
            patch(
                "sales_imports.management.commands.process_sales_imports."
                "get_sales_import_storage",
                return_value=object(),
            ),
            patch(
                "sales_imports.management.commands.process_sales_imports."
                "claim_next_job",
                side_effect=claims,
            ) as claim_next,
            patch(
                "sales_imports.management.commands.process_sales_imports."
                "process_claim",
                side_effect=results,
            ) as process,
        ):
            call_command(
                "process_sales_imports",
                max_jobs=2,
                worker_id="command-test-worker",
                stdout=stdout,
            )

        self.assertEqual(claim_next.call_count, 2)
        self.assertEqual(process.call_count, 2)
        self.assertIn(f"{first_id}: published", stdout.getvalue())
        self.assertIn(f"{second_id}: rejected", stdout.getvalue())
        self.assertIn("Sales-import jobs processed: 2", stdout.getvalue())

    def test_terminal_processing_failure_returns_nonzero_command_status(self):
        job_id = uuid.uuid4()
        result = SalesImportProcessingResult(
            job_id=job_id,
            status=SalesImportJob.Status.FAILED,
            counts=SalesImportJobCounts(),
        )

        with (
            patch(
                "sales_imports.management.commands.process_sales_imports."
                "get_sales_import_storage",
                return_value=object(),
            ),
            patch(
                "sales_imports.management.commands.process_sales_imports."
                "claim_next_job",
                return_value=SimpleNamespace(job_id=job_id),
            ),
            patch(
                "sales_imports.management.commands.process_sales_imports."
                "process_claim",
                return_value=result,
            ),
        ):
            with self.assertRaisesMessage(CommandError, str(job_id)):
                call_command(
                    "process_sales_imports",
                    max_jobs=1,
                    worker_id="command-test-worker",
                )

    def test_rejects_invalid_limits_and_worker_ids_cleanly(self):
        with self.assertRaisesMessage(CommandError, "--max-jobs must be at least 1"):
            call_command("process_sales_imports", max_jobs=0)

        with (
            patch(
                "sales_imports.management.commands.process_sales_imports."
                "get_sales_import_storage",
                return_value=object(),
            ),
            patch(
                "sales_imports.management.commands.process_sales_imports."
                "claim_next_job",
                side_effect=ValueError(
                    "worker_id may contain at most 255 characters."
                ),
            ),
        ):
            with self.assertRaisesMessage(CommandError, "at most 255 characters"):
                call_command(
                    "process_sales_imports",
                    worker_id="x" * 256,
                )
