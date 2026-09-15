import hashlib
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.utils import timezone

from business_data.models import CalendarDate, SalesTransaction
from master_data.models import Product, Territory

from .models import (
    SalesImport,
    SalesImportAttempt,
    SalesImportIssue,
    SalesImportJob,
    SalesImportStagedRow,
)
from .queue import (
    advance_attempt_phase,
    claim_next_job,
    complete_job,
    enqueue_import,
    heartbeat_job,
)


TEST_KMS_KEY_ARN = "arn:aws:kms:ap-south-1:123456789012:key/sales-import-test"


class SalesImportProcessingAuditGuardTests(TestCase):
    def setUp(self):
        self.now = timezone.now().replace(microsecond=0)
        self.user = get_user_model().objects.create_user(
            email="processing-audit@example.com",
            password="test-only-password",
        )
        content = b"source_record_id,transaction_date\nAUDIT-1,2026-01-05\n"
        self.sales_import = SalesImport.objects.create(
            source_system="ERP",
            original_filename="audit.csv",
            content_type="text/csv",
            file_format=SalesImport.FileFormat.CSV,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            storage_bucket="private-sales-imports",
            storage_kms_key_arn=TEST_KMS_KEY_ARN,
            storage_key="sales-imports/original/audit.csv",
            storage_version_id="audit-version-1",
            uploaded_by=self.user,
            status=SalesImport.Status.RECEIVED,
            received_at=self.now,
        )
        self.job = enqueue_import(self.sales_import, available_at=self.now)
        self.claim = claim_next_job(worker_id="audit-worker", now=self.now)
        self.attempt = SalesImportAttempt.objects.get(pk=self.claim.attempt_id)

    def assert_constraint(self, expected_name, operation):
        with self.assertRaises(IntegrityError) as raised:
            with transaction.atomic():
                operation()
        diagnostic = getattr(getattr(raised.exception, "__cause__", None), "diag", None)
        self.assertEqual(getattr(diagnostic, "constraint_name", None), expected_name)

    @staticmethod
    def raw_delete(model, primary_key):
        table_name = connection.ops.quote_name(model._meta.db_table)
        primary_key_column = connection.ops.quote_name(model._meta.pk.column)
        with connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {table_name} WHERE {primary_key_column} = %s",
                [primary_key],
            )

    def create_staged_row(self, *, source_record_id="AUDIT-1", row_number=2):
        return SalesImportStagedRow.objects.create(
            attempt=self.attempt,
            row_number=row_number,
            source_record_id=source_record_id,
            transaction_date=date(2026, 1, 5),
            product_code="P-AUDIT",
            territory_code="T-AUDIT",
            hospital_code="",
            sales_representative_code="",
            quantity=Decimal("2.000"),
            revenue_amount=Decimal("25.0000"),
            currency_code="INR",
        )

    def create_dimensions(self):
        calendar_date = CalendarDate.for_date(date(2026, 1, 5))
        calendar_date.save()
        product = Product.objects.create(code="P-AUDIT", name="Audit Product")
        territory = Territory.objects.create(
            code="T-AUDIT",
            name="Audit Territory",
            level=Territory.Level.TERRITORY,
        )
        return calendar_date, product, territory

    def create_fact(
        self,
        *,
        source_record_id,
        calendar_date,
        product,
        territory,
    ):
        return SalesTransaction.objects.create(
            calendar_date=calendar_date,
            product=product,
            territory=territory,
            quantity=Decimal("2.000"),
            revenue_amount=Decimal("25.0000"),
            currency_code="INR",
            source_system=self.sales_import.source_system,
            source_record_id=source_record_id,
            created_by=self.user,
        )

    def test_job_identity_and_delete_are_database_protected(self):
        created_at = self.job.created_at
        self.assert_constraint(
            "sales_job_identity_immutable",
            lambda: SalesImportJob.objects.filter(pk=self.job.pk).update(
                created_at=created_at + timedelta(seconds=1)
            ),
        )
        self.assert_constraint(
            "sales_job_audit_delete_denied",
            lambda: self.raw_delete(
                SalesImportJob,
                self.job.pk,
            ),
        )

    def test_running_attempt_allows_progress_but_freezes_identity_and_terminal_state(self):
        self.assert_constraint(
            "sales_attempt_identity_immutable",
            lambda: SalesImportAttempt.objects.filter(pk=self.attempt.pk).update(
                worker_id="rewritten-worker"
            ),
        )

        advance_attempt_phase(
            job_id=self.job.pk,
            lease_token=self.claim.lease_token,
            phase=SalesImportAttempt.Phase.DOWNLOAD,
            now=self.now + timedelta(seconds=1),
        )
        heartbeat_job(
            job_id=self.job.pk,
            lease_token=self.claim.lease_token,
            now=self.now + timedelta(seconds=2),
        )
        complete_job(
            job_id=self.job.pk,
            lease_token=self.claim.lease_token,
            status=SalesImportJob.Status.FAILED,
            error_code="AUDIT_TEST",
            error_message="Expected test completion.",
            now=self.now + timedelta(seconds=3),
        )

        self.assert_constraint(
            "sales_attempt_terminal_immutable",
            lambda: SalesImportAttempt.objects.filter(pk=self.attempt.pk).update(
                error_message="rewritten"
            ),
        )
        self.assert_constraint(
            "sales_attempt_audit_delete_denied",
            lambda: self.raw_delete(
                SalesImportAttempt,
                self.attempt.pk,
            ),
        )

    def test_issues_are_database_immutable_and_cannot_be_deleted(self):
        issue = SalesImportIssue.objects.create(
            attempt=self.attempt,
            row_number=2,
            column="quantity",
            code="QUANTITY_INVALID",
            message="The quantity is invalid.",
            phase=SalesImportIssue.Phase.CONTRACT,
        )

        self.assert_constraint(
            "sales_issue_audit_immutable",
            lambda: SalesImportIssue.objects.filter(pk=issue.pk).update(
                message="Rewritten issue."
            ),
        )
        self.assert_constraint(
            "sales_issue_audit_delete_denied",
            lambda: self.raw_delete(SalesImportIssue, issue.pk),
        )

    def test_staged_values_are_immutable_and_only_a_matching_fact_can_finalize(self):
        row = self.create_staged_row()
        calendar_date, product, territory = self.create_dimensions()

        self.assert_constraint(
            "sales_staged_values_immutable",
            lambda: SalesImportStagedRow.objects.filter(pk=row.pk).update(
                quantity=Decimal("3.000")
            ),
        )

        updated = SalesImportStagedRow.objects.filter(pk=row.pk).update(
            calendar_date=calendar_date,
            product=product,
            territory=territory,
        )
        self.assertEqual(updated, 1)

        unrelated_fact = self.create_fact(
            source_record_id="OTHER-1",
            calendar_date=calendar_date,
            product=product,
            territory=territory,
        )
        self.assert_constraint(
            "sales_staged_transaction_mismatch",
            lambda: SalesImportStagedRow.objects.filter(pk=row.pk).update(
                outcome=SalesImportStagedRow.Outcome.REUSED,
                sales_transaction=unrelated_fact,
            ),
        )

        matching_fact = self.create_fact(
            source_record_id=row.source_record_id,
            calendar_date=calendar_date,
            product=product,
            territory=territory,
        )
        updated = SalesImportStagedRow.objects.filter(pk=row.pk).update(
            outcome=SalesImportStagedRow.Outcome.PUBLISHED,
            sales_transaction=matching_fact,
        )
        self.assertEqual(updated, 1)

        self.assert_constraint(
            "sales_staged_terminal_immutable",
            lambda: SalesImportStagedRow.objects.filter(pk=row.pk).update(
                outcome=SalesImportStagedRow.Outcome.REUSED
            ),
        )
        self.assert_constraint(
            "sales_staged_audit_delete_denied",
            lambda: self.raw_delete(SalesImportStagedRow, row.pk),
        )

    def test_unfinished_staged_row_freezes_when_its_attempt_finishes(self):
        row = self.create_staged_row()
        calendar_date, product, territory = self.create_dimensions()
        complete_job(
            job_id=self.job.pk,
            lease_token=self.claim.lease_token,
            status=SalesImportJob.Status.FAILED,
            error_code="AUDIT_TEST",
            error_message="Expected test completion.",
            now=self.now + timedelta(seconds=1),
        )

        self.assert_constraint(
            "sales_staged_attempt_terminal",
            lambda: SalesImportStagedRow.objects.filter(pk=row.pk).update(
                calendar_date=calendar_date,
                product=product,
                territory=territory,
            ),
        )
