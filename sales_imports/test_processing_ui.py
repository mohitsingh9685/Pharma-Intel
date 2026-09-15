import uuid

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .models import (
    SalesImport,
    SalesImportAttempt,
    SalesImportIssue,
    SalesImportJob,
)


TEST_KMS_KEY_ARN = "arn:aws:kms:ap-south-1:123456789012:key/sales-import-test"


class SalesImportProcessingVisibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.administrator = get_user_model().objects.create_user(
            email="processing-operator@example.com",
            password="test-only-password",
            role=get_user_model().Role.ADMIN,
        )

    def setUp(self):
        self.client.force_login(self.administrator)

    def create_import(self, number, *, file_format=SalesImport.FileFormat.CSV):
        now = timezone.now()
        suffix = "xlsx" if file_format == SalesImport.FileFormat.XLSX else "csv"
        return SalesImport.objects.create(
            source_system="DEMO-ERP",
            original_filename=f"sales-{number}.{suffix}",
            content_type="text/csv",
            file_format=file_format,
            size_bytes=100 + number,
            sha256=f"{number:064x}",
            storage_bucket=f"private-secret-bucket-{number}",
            storage_kms_key_arn=TEST_KMS_KEY_ARN,
            storage_key=f"secret/storage/key/{number}.{suffix}",
            storage_version_id=f"secret-version-{number}",
            uploaded_by=self.administrator,
            status=SalesImport.Status.RECEIVED,
            received_at=now,
        )

    def create_attempt(
        self,
        job,
        number,
        *,
        outcome=SalesImportAttempt.Outcome.REJECTED,
        total_rows=1,
        valid_rows=0,
        invalid_rows=1,
        issue_count=1,
        inserted_rows=0,
        reused_rows=0,
    ):
        return SalesImportAttempt.objects.create(
            job=job,
            attempt_number=number,
            lease_token=uuid.uuid4(),
            worker_id="test-worker",
            phase=SalesImportAttempt.Phase.COMPLETE,
            outcome=outcome,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            total_rows=total_rows,
            valid_rows=valid_rows,
            invalid_rows=invalid_rows,
            issue_count=issue_count,
            inserted_rows=inserted_rows,
            reused_rows=reused_rows,
        )

    def test_list_separates_intake_and_processing_status_without_storage_details(self):
        queued_import = self.create_import(1)
        SalesImportJob.objects.create(
            sales_import=queued_import,
            status=SalesImportJob.Status.QUEUED,
        )
        self.create_import(2)

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("sales_imports:list"))

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 4)
        self.assertContains(response, "Intake status")
        self.assertContains(response, "Processing status")
        self.assertContains(response, "Received", count=2)
        self.assertContains(response, "Queued")
        self.assertContains(response, "Not scheduled")
        self.assertNotContains(response, "private-secret-bucket")
        self.assertNotContains(response, "secret/storage/key")
        for sales_import in response.context["page"].object_list:
            self.assertIn("uploaded_by", sales_import._state.fields_cache)
            self.assertIn("processing_job", sales_import._state.fields_cache)

    def test_detail_shows_processing_counts_and_keeps_s3_lineage_private(self):
        sales_import = self.create_import(3)
        job = SalesImportJob.objects.create(
            sales_import=sales_import,
            status=SalesImportJob.Status.PUBLISHED,
            attempt_count=1,
            total_rows=3,
            valid_rows=3,
            inserted_rows=2,
            reused_rows=1,
            finished_at=timezone.now(),
        )
        self.create_attempt(
            job,
            1,
            outcome=SalesImportAttempt.Outcome.PUBLISHED,
            total_rows=3,
            valid_rows=3,
            invalid_rows=0,
            issue_count=0,
            inserted_rows=2,
            reused_rows=1,
        )

        response = self.client.get(
            reverse("sales_imports:detail", args=(sales_import.pk,))
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "File intake")
        self.assertContains(response, "Intake status")
        self.assertContains(response, "Row processing")
        self.assertContains(response, "Processing status")
        self.assertContains(response, "Published")
        self.assertContains(response, "Total rows")
        self.assertContains(response, "Inserted sales rows")
        self.assertContains(response, "Reused sales rows")
        self.assertEqual(response.context["processing_job"].total_rows, 3)
        self.assertEqual(response.context["processing_job"].inserted_rows, 2)
        self.assertEqual(response.context["processing_job"].reused_rows, 1)
        self.assertIn(
            "uploaded_by",
            response.context["sales_import"]._state.fields_cache,
        )
        self.assertIn(
            "processing_job",
            response.context["sales_import"]._state.fields_cache,
        )
        self.assertNotContains(response, sales_import.storage_bucket)
        self.assertNotContains(response, sales_import.storage_key)
        self.assertNotContains(response, sales_import.sha256)
        self.assertNotContains(response, sales_import.storage_version_id)

    def test_detail_paginates_issues_from_only_the_latest_attempt(self):
        sales_import = self.create_import(4)
        job = SalesImportJob.objects.create(
            sales_import=sales_import,
            status=SalesImportJob.Status.REJECTED,
            attempt_count=2,
            total_rows=55,
            invalid_rows=55,
            issue_count=55,
            finished_at=timezone.now(),
        )
        earlier_attempt = self.create_attempt(job, 1)
        SalesImportIssue.objects.create(
            attempt=earlier_attempt,
            row_number=2,
            column="product_code",
            code="OLD_ATTEMPT_ONLY",
            message="This issue belongs only to the older attempt.",
            phase=SalesImportIssue.Phase.MASTER_DATA,
        )
        latest_attempt = self.create_attempt(
            job,
            2,
            total_rows=55,
            valid_rows=0,
            invalid_rows=55,
            issue_count=55,
        )
        SalesImportIssue.objects.bulk_create(
            [
                SalesImportIssue(
                    attempt=latest_attempt,
                    row_number=index + 2,
                    column="product_code",
                    code=f"MISSING_PRODUCT_{index:02d}",
                    message=f"Problem {index}",
                    phase=SalesImportIssue.Phase.MASTER_DATA,
                )
                for index in range(55)
            ]
        )

        with CaptureQueriesContext(connection) as queries:
            first_page = self.client.get(
                reverse("sales_imports:detail", args=(sales_import.pk,))
            )
        second_page = self.client.get(
            reverse("sales_imports:detail", args=(sales_import.pk,)),
            {"issues_page": 2},
        )

        self.assertLessEqual(len(queries), 6)
        self.assertEqual(first_page.context["latest_attempt"], latest_attempt)
        self.assertEqual(len(first_page.context["issue_page"].object_list), 50)
        self.assertEqual(first_page.context["issue_page"].paginator.num_pages, 2)
        self.assertContains(first_page, "Latest persisted issues")
        self.assertContains(first_page, "Issue page 1 of 2")
        self.assertNotContains(first_page, "OLD_ATTEMPT_ONLY")
        self.assertEqual(len(second_page.context["issue_page"].object_list), 5)
        self.assertContains(second_page, "Issue page 2 of 2")
        self.assertContains(second_page, "Problem 54")

    def test_xlsx_detail_explains_that_row_parser_is_pending(self):
        sales_import = self.create_import(
            5,
            file_format=SalesImport.FileFormat.XLSX,
        )
        SalesImportJob.objects.create(
            sales_import=sales_import,
            status=SalesImportJob.Status.AWAITING_PARSER,
        )

        response = self.client.get(
            reverse("sales_imports:detail", args=(sales_import.pk,))
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Awaiting format parser")
        self.assertContains(response, "XLSX row processing is not available yet")
        self.assertContains(response, "creates no sales facts")
