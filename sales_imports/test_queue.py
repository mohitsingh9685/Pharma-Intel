import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event

from django.contrib.auth import get_user_model
from django.db import close_old_connections, transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.utils import timezone

from .models import (
    SalesImport,
    SalesImportAttempt,
    SalesImportIssue,
    SalesImportJob,
)
from .queue import (
    SalesImportClaimLost,
    SalesImportJobCounts,
    SalesImportQueueStateError,
    claim_next_job,
    complete_job,
    enqueue_import,
    heartbeat_job,
    requeue_job,
    retry_delay,
    schedule_retry,
)


TEST_KMS_KEY_ARN = "arn:aws:kms:ap-south-1:123456789012:key/sales-import-test"


class QueueFixtureMixin:
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            email=f"queue-{uuid.uuid4()}@example.com",
            password="test-only-password",
        )
        self.now = timezone.now().replace(microsecond=0)
        self.import_number = 0

    def create_import(
        self,
        *,
        status=SalesImport.Status.RECEIVED,
        file_format=SalesImport.FileFormat.CSV,
    ):
        self.import_number += 1
        number = self.import_number
        values = {
            "source_system": "ERP",
            "original_filename": f"sales-{number}.{file_format}",
            "content_type": "text/csv",
            "file_format": file_format,
            "size_bytes": 100,
            "sha256": f"{number:064x}",
            "storage_bucket": "private-sales-imports",
            "storage_kms_key_arn": TEST_KMS_KEY_ARN,
            "storage_key": f"sales-imports/original/{number}.{file_format}",
            "uploaded_by": self.user,
            "status": status,
        }
        if status == SalesImport.Status.RECEIVED:
            values.update(
                storage_version_id=f"version-{number}",
                received_at=self.now,
            )
        elif status == SalesImport.Status.FAILED:
            values.update(
                failure_code=SalesImport.FailureCode.STORAGE_ERROR,
                failed_at=self.now,
            )
        return SalesImport.objects.create(**values)


class RetryDelayTests(SimpleTestCase):
    def test_exponential_delay_is_bounded(self):
        base = timedelta(seconds=10)
        maximum = timedelta(seconds=25)

        self.assertEqual(
            [
                retry_delay(
                    attempt,
                    base_delay=base,
                    maximum_delay=maximum,
                )
                for attempt in range(1, 6)
            ],
            [
                timedelta(seconds=10),
                timedelta(seconds=20),
                timedelta(seconds=25),
                timedelta(seconds=25),
                timedelta(seconds=25),
            ],
        )


class SalesImportQueueTests(QueueFixtureMixin, TestCase):
    def test_enqueue_is_idempotent_and_tracks_intake_readiness(self):
        intake = self.create_import(status=SalesImport.Status.RECEIVING)

        waiting = enqueue_import(intake, available_at=self.now)
        duplicate = enqueue_import(intake, available_at=self.now)

        self.assertEqual(waiting.pk, duplicate.pk)
        self.assertEqual(waiting.status, SalesImportJob.Status.WAITING_FOR_FILE)
        self.assertEqual(SalesImportJob.objects.count(), 1)

        intake.mark_received(version_id="version-ready")
        queued = enqueue_import(intake, available_at=self.now)

        self.assertEqual(queued.status, SalesImportJob.Status.QUEUED)
        self.assertIsNone(queued.finished_at)

    def test_failed_intake_fails_a_waiting_job(self):
        intake = self.create_import(status=SalesImport.Status.RECEIVING)
        enqueue_import(intake, available_at=self.now)

        intake.mark_failed(failure_code=SalesImport.FailureCode.STORAGE_ERROR)
        failed = enqueue_import(intake, available_at=self.now)

        self.assertEqual(failed.status, SalesImportJob.Status.FAILED)
        self.assertEqual(failed.last_error_code, "INTAKE_STORAGE_FAILED")
        self.assertEqual(failed.finished_at, self.now)

    def test_received_xlsx_waits_for_a_parser_and_is_not_claimed(self):
        intake = self.create_import(file_format=SalesImport.FileFormat.XLSX)

        job = enqueue_import(intake, available_at=self.now)

        self.assertEqual(job.status, SalesImportJob.Status.AWAITING_PARSER)
        self.assertIsNone(
            claim_next_job(worker_id="worker-one", now=self.now)
        )
        self.assertEqual(SalesImportAttempt.objects.count(), 0)

    def test_claim_sets_a_fenced_lease_and_attempt(self):
        intake = self.create_import()
        job = enqueue_import(intake, available_at=self.now)

        claim = claim_next_job(
            worker_id=" worker-one ",
            lease_duration=timedelta(minutes=2),
            now=self.now,
        )

        self.assertEqual(claim.job_id, job.pk)
        self.assertEqual(claim.worker_id, "worker-one")
        self.assertEqual(claim.attempt_number, 1)
        self.assertEqual(
            claim.lease_expires_at,
            self.now + timedelta(minutes=2),
        )
        job.refresh_from_db()
        self.assertEqual(job.status, SalesImportJob.Status.PROCESSING)
        self.assertEqual(job.lease_token, claim.lease_token)
        self.assertEqual(job.leased_by, "worker-one")
        self.assertEqual(job.attempt_count, 1)
        attempt = SalesImportAttempt.objects.get(pk=claim.attempt_id)
        self.assertEqual(attempt.outcome, SalesImportAttempt.Outcome.RUNNING)
        self.assertEqual(attempt.lease_token, claim.lease_token)
        self.assertIsNone(
            claim_next_job(worker_id="worker-two", now=self.now)
        )

    def test_heartbeat_requires_the_live_unexpired_token(self):
        intake = self.create_import()
        enqueue_import(intake, available_at=self.now)
        claim = claim_next_job(
            worker_id="worker-one",
            lease_duration=timedelta(minutes=2),
            now=self.now,
        )
        heartbeat_at = self.now + timedelta(minutes=1)

        with self.assertRaises(SalesImportClaimLost):
            heartbeat_job(
                job_id=claim.job_id,
                lease_token=uuid.uuid4(),
                now=heartbeat_at,
            )

        new_expiry = heartbeat_job(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            lease_duration=timedelta(minutes=3),
            now=heartbeat_at,
        )

        self.assertEqual(new_expiry, heartbeat_at + timedelta(minutes=3))
        job = SalesImportJob.objects.get(pk=claim.job_id)
        attempt = SalesImportAttempt.objects.get(pk=claim.attempt_id)
        self.assertEqual(job.heartbeat_at, heartbeat_at)
        self.assertEqual(attempt.heartbeat_at, heartbeat_at)

        with self.assertRaises(SalesImportClaimLost):
            heartbeat_job(
                job_id=claim.job_id,
                lease_token=claim.lease_token,
                now=new_expiry,
            )

    def test_expired_lease_is_reclaimed_and_old_token_is_fenced(self):
        intake = self.create_import()
        enqueue_import(intake, available_at=self.now, max_attempts=3)
        first = claim_next_job(
            worker_id="worker-one",
            lease_duration=timedelta(minutes=1),
            now=self.now,
        )
        reclaimed_at = self.now + timedelta(minutes=1, seconds=1)

        second = claim_next_job(
            worker_id="worker-two",
            lease_duration=timedelta(minutes=2),
            now=reclaimed_at,
        )

        self.assertEqual(second.job_id, first.job_id)
        self.assertEqual(second.attempt_number, 2)
        self.assertNotEqual(second.lease_token, first.lease_token)
        first_attempt = SalesImportAttempt.objects.get(pk=first.attempt_id)
        self.assertEqual(
            first_attempt.outcome,
            SalesImportAttempt.Outcome.LEASE_EXPIRED,
        )
        self.assertEqual(first_attempt.finished_at, reclaimed_at)

        with self.assertRaises(SalesImportClaimLost):
            complete_job(
                job_id=first.job_id,
                lease_token=first.lease_token,
                status=SalesImportJob.Status.PUBLISHED,
                now=reclaimed_at + timedelta(seconds=1),
            )

    def test_expired_last_attempt_marks_job_failed(self):
        intake = self.create_import()
        enqueue_import(intake, available_at=self.now, max_attempts=1)
        claim = claim_next_job(
            worker_id="worker-one",
            lease_duration=timedelta(minutes=1),
            now=self.now,
        )

        next_claim = claim_next_job(
            worker_id="worker-two",
            now=self.now + timedelta(minutes=2),
        )

        self.assertIsNone(next_claim)
        job = SalesImportJob.objects.get(pk=claim.job_id)
        self.assertEqual(job.status, SalesImportJob.Status.FAILED)
        self.assertEqual(job.last_error_code, "ATTEMPT_LIMIT_EXHAUSTED")
        self.assertIsNotNone(job.finished_at)

    def test_retry_wait_uses_bounded_backoff_and_preserves_attempt_history(self):
        intake = self.create_import()
        enqueue_import(intake, available_at=self.now, max_attempts=3)
        first = claim_next_job(worker_id="worker-one", now=self.now)
        failed_at = self.now + timedelta(seconds=1)

        retrying = schedule_retry(
            job_id=first.job_id,
            lease_token=first.lease_token,
            error_code="S3_TIMEOUT",
            error_message="Temporary read timeout.",
            base_delay=timedelta(seconds=10),
            maximum_delay=timedelta(seconds=25),
            now=failed_at,
        )

        self.assertEqual(retrying.status, SalesImportJob.Status.RETRY_WAIT)
        self.assertEqual(retrying.available_at, failed_at + timedelta(seconds=10))
        self.assertIsNone(retrying.lease_token)
        first_attempt = SalesImportAttempt.objects.get(pk=first.attempt_id)
        self.assertEqual(
            first_attempt.outcome,
            SalesImportAttempt.Outcome.RETRYABLE_FAILURE,
        )
        self.assertEqual(first_attempt.error_code, "S3_TIMEOUT")
        self.assertIsNone(
            claim_next_job(
                worker_id="worker-two",
                now=failed_at + timedelta(seconds=9),
            )
        )

        second = claim_next_job(
            worker_id="worker-two",
            now=failed_at + timedelta(seconds=10),
        )
        self.assertEqual(second.attempt_number, 2)
        self.assertNotEqual(second.lease_token, first.lease_token)

    def test_retry_at_attempt_limit_becomes_terminal_failure(self):
        intake = self.create_import()
        enqueue_import(intake, available_at=self.now, max_attempts=1)
        claim = claim_next_job(worker_id="worker-one", now=self.now)

        failed = schedule_retry(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            error_code="DATABASE_UNAVAILABLE",
            error_message="Temporary database failure.",
            now=self.now + timedelta(seconds=1),
        )

        self.assertEqual(failed.status, SalesImportJob.Status.FAILED)
        self.assertIsNotNone(failed.finished_at)
        self.assertIsNone(failed.lease_token)

    def test_terminal_completion_updates_job_and_attempt_atomically(self):
        intake = self.create_import()
        enqueue_import(intake, available_at=self.now)
        claim = claim_next_job(worker_id="worker-one", now=self.now)
        counts = SalesImportJobCounts(
            total_rows=3,
            valid_rows=3,
            inserted_rows=2,
            reused_rows=1,
        )

        completed = complete_job(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            status=SalesImportJob.Status.PUBLISHED,
            counts=counts,
            now=self.now + timedelta(seconds=1),
        )

        self.assertEqual(completed.status, SalesImportJob.Status.PUBLISHED)
        self.assertEqual(completed.inserted_rows, 2)
        self.assertEqual(completed.reused_rows, 1)
        self.assertIsNone(completed.lease_token)
        attempt = SalesImportAttempt.objects.get(pk=claim.attempt_id)
        self.assertEqual(attempt.outcome, SalesImportAttempt.Outcome.PUBLISHED)
        self.assertEqual(attempt.phase, SalesImportAttempt.Phase.COMPLETE)
        self.assertEqual(attempt.inserted_rows, 2)
        self.assertEqual(attempt.reused_rows, 1)

        with self.assertRaises(SalesImportClaimLost):
            complete_job(
                job_id=claim.job_id,
                lease_token=claim.lease_token,
                status=SalesImportJob.Status.PUBLISHED,
                counts=counts,
                now=self.now + timedelta(seconds=2),
            )

    def test_rejection_requires_a_persisted_issue_count(self):
        intake = self.create_import()
        enqueue_import(intake, available_at=self.now)
        claim = claim_next_job(worker_id="worker-one", now=self.now)

        with self.assertRaises(ValueError):
            complete_job(
                job_id=claim.job_id,
                lease_token=claim.lease_token,
                status=SalesImportJob.Status.REJECTED,
                now=self.now + timedelta(seconds=1),
            )

        rejected = complete_job(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            status=SalesImportJob.Status.REJECTED,
            counts=SalesImportJobCounts(
                total_rows=1,
                valid_rows=0,
                invalid_rows=1,
                issue_count=1,
            ),
            error_code="ROW_INVALID",
            error_message="The file contains an invalid row.",
            now=self.now + timedelta(seconds=1),
        )

        self.assertEqual(rejected.status, SalesImportJob.Status.REJECTED)
        self.assertEqual(rejected.issue_count, 1)

    def test_failed_job_can_be_manually_requeued_without_resetting_attempts(self):
        intake = self.create_import()
        enqueue_import(intake, available_at=self.now, max_attempts=1)
        first = claim_next_job(worker_id="worker-one", now=self.now)
        SalesImportJob.objects.filter(pk=first.job_id).update(
            total_rows=2,
            valid_rows=2,
        )
        failed = schedule_retry(
            job_id=first.job_id,
            lease_token=first.lease_token,
            error_code="S3_DOWNLOAD_RETRYABLE",
            error_message="The exact object version could not be read.",
            now=self.now + timedelta(seconds=1),
        )
        self.assertEqual(failed.status, SalesImportJob.Status.FAILED)
        original_attempt_ids = list(
            SalesImportAttempt.objects.filter(job=failed).values_list(
                "pk", flat=True
            )
        )
        requeued_at = self.now + timedelta(minutes=5)

        requeued = requeue_job(
            failed.pk,
            additional_attempts=2,
            available_at=requeued_at,
        )

        self.assertEqual(requeued.status, SalesImportJob.Status.QUEUED)
        self.assertEqual(requeued.available_at, requeued_at)
        self.assertEqual(requeued.attempt_count, 1)
        self.assertEqual(requeued.max_attempts, 3)
        self.assertIsNone(requeued.finished_at)
        self.assertEqual(requeued.last_error_code, "")
        self.assertEqual(requeued.last_error_message, "")
        self.assertEqual(requeued.total_rows, 0)
        self.assertIsNone(requeued.lease_token)
        self.assertEqual(
            list(
                SalesImportAttempt.objects.filter(job=requeued).values_list(
                    "pk", flat=True
                )
            ),
            original_attempt_ids,
        )
        first_attempt = SalesImportAttempt.objects.get(pk=first.attempt_id)
        self.assertEqual(first_attempt.total_rows, 2)

        self.assertIsNone(
            claim_next_job(
                worker_id="worker-two",
                now=requeued_at - timedelta(seconds=1),
            )
        )
        second = claim_next_job(worker_id="worker-two", now=requeued_at)
        self.assertEqual(second.attempt_number, 2)

    def test_validation_rejection_can_be_requeued_and_keeps_its_audit_rows(self):
        intake = self.create_import()
        job = enqueue_import(intake, available_at=self.now)
        first = claim_next_job(worker_id="worker-one", now=self.now)
        SalesImportIssue.objects.create(
            attempt_id=first.attempt_id,
            row_number=2,
            column="product_code",
            code="PRODUCT_NOT_FOUND",
            message="The supplied product code does not exist.",
            phase=SalesImportIssue.Phase.MASTER_DATA,
        )
        complete_job(
            job_id=job.pk,
            lease_token=first.lease_token,
            status=SalesImportJob.Status.REJECTED,
            counts=SalesImportJobCounts(
                total_rows=1,
                invalid_rows=1,
                issue_count=1,
            ),
            error_code="VALIDATION_REJECTED",
            error_message="Database-backed validation rejected the file.",
            now=self.now + timedelta(seconds=1),
        )

        requeued = requeue_job(
            job.pk,
            available_at=self.now + timedelta(minutes=1),
        )

        self.assertEqual(requeued.status, SalesImportJob.Status.QUEUED)
        self.assertEqual(requeued.attempt_count, 1)
        self.assertEqual(requeued.max_attempts, 6)
        self.assertEqual(requeued.issue_count, 0)
        self.assertTrue(
            SalesImportIssue.objects.filter(
                attempt_id=first.attempt_id,
                code="PRODUCT_NOT_FOUND",
            ).exists()
        )
        first_attempt = SalesImportAttempt.objects.get(pk=first.attempt_id)
        self.assertEqual(first_attempt.outcome, SalesImportAttempt.Outcome.REJECTED)

    def test_source_record_conflict_cannot_be_manually_requeued(self):
        intake = self.create_import()
        job = enqueue_import(intake, available_at=self.now)
        claim = claim_next_job(worker_id="worker-one", now=self.now)
        SalesImportIssue.objects.create(
            attempt_id=claim.attempt_id,
            row_number=2,
            column="source_record_id",
            code="SOURCE_RECORD_CONFLICT",
            message="The source record conflicts with an immutable sales fact.",
            phase=SalesImportIssue.Phase.IDEMPOTENCY,
        )
        complete_job(
            job_id=job.pk,
            lease_token=claim.lease_token,
            status=SalesImportJob.Status.REJECTED,
            counts=SalesImportJobCounts(
                total_rows=1,
                invalid_rows=1,
                issue_count=1,
            ),
            error_code="SOURCE_RECORD_CONFLICT",
            error_message="An immutable sales fact conflicts with this row.",
            now=self.now + timedelta(seconds=1),
        )

        with self.assertRaisesMessage(
            SalesImportQueueStateError,
            "immutable source-record conflict",
        ):
            requeue_job(job.pk, available_at=self.now + timedelta(minutes=1))

        job.refresh_from_db()
        self.assertEqual(job.status, SalesImportJob.Status.REJECTED)
        self.assertEqual(job.attempt_count, 1)

    def test_manual_requeue_rejects_nonretryable_job_states_and_intakes(self):
        queued_import = self.create_import()
        queued_job = enqueue_import(queued_import, available_at=self.now)

        receiving_import = self.create_import(status=SalesImport.Status.RECEIVING)
        waiting_job = enqueue_import(receiving_import, available_at=self.now)

        xlsx_import = self.create_import(file_format=SalesImport.FileFormat.XLSX)
        xlsx_job = enqueue_import(xlsx_import, available_at=self.now)

        failed_intake = self.create_import(status=SalesImport.Status.FAILED)
        failed_intake_job = enqueue_import(failed_intake, available_at=self.now)

        for job in (queued_job, waiting_job, xlsx_job, failed_intake_job):
            with self.subTest(
                status=job.status,
                file_format=job.sales_import.file_format,
            ):
                with self.assertRaises(SalesImportQueueStateError):
                    requeue_job(job.pk, available_at=self.now)

        claim = claim_next_job(worker_id="worker-one", now=self.now)
        self.assertEqual(claim.job_id, queued_job.pk)
        with self.assertRaises(SalesImportQueueStateError):
            requeue_job(claim.job_id, available_at=self.now)
        complete_job(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            status=SalesImportJob.Status.PUBLISHED,
            now=self.now + timedelta(seconds=1),
        )
        with self.assertRaises(SalesImportQueueStateError):
            requeue_job(queued_job.pk, available_at=self.now)

    def test_manual_requeue_validates_attempt_extension(self):
        intake = self.create_import()
        enqueue_import(intake, available_at=self.now, max_attempts=1)
        claim = claim_next_job(worker_id="worker-one", now=self.now)
        schedule_retry(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            error_code="TRANSIENT",
            error_message="Temporary failure.",
            now=self.now + timedelta(seconds=1),
        )

        for invalid in (0, -1, True, "1"):
            with self.subTest(additional_attempts=invalid):
                with self.assertRaises(ValueError):
                    requeue_job(
                        claim.job_id,
                        additional_attempts=invalid,
                        available_at=self.now + timedelta(minutes=1),
                    )


class SalesImportQueueConcurrencyTests(QueueFixtureMixin, TransactionTestCase):
    reset_sequences = True

    def test_two_workers_cannot_claim_the_same_job(self):
        intake = self.create_import()
        enqueue_import(intake, available_at=self.now)
        barrier = Barrier(3)

        def claim(worker_id):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return claim_next_job(worker_id=worker_id, now=self.now)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(claim, "worker-one"),
                executor.submit(claim, "worker-two"),
            ]
            barrier.wait(timeout=10)
            claims = [future.result(timeout=10) for future in futures]

        successful = [claim for claim in claims if claim is not None]
        self.assertEqual(len(successful), 1)
        self.assertEqual(SalesImportAttempt.objects.count(), 1)
        job = SalesImportJob.objects.get(pk=intake.pk)
        self.assertEqual(job.attempt_count, 1)
        self.assertEqual(job.lease_token, successful[0].lease_token)

    def test_claim_skips_a_job_locked_by_another_worker(self):
        first_import = self.create_import()
        first_job = enqueue_import(first_import, available_at=self.now)
        second_import = self.create_import()
        second_job = enqueue_import(second_import, available_at=self.now)
        locked = Event()
        release = Event()

        def hold_first_job_lock():
            close_old_connections()
            try:
                with transaction.atomic():
                    SalesImportJob.objects.select_for_update().get(pk=first_job.pk)
                    locked.set()
                    if not release.wait(timeout=10):
                        raise TimeoutError("Timed out waiting to release the job lock.")
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(hold_first_job_lock)
            self.assertTrue(locked.wait(timeout=10))
            try:
                claim = claim_next_job(worker_id="worker-two", now=self.now)
            finally:
                release.set()
            future.result(timeout=10)

        self.assertEqual(claim.job_id, second_job.pk)
        first_job.refresh_from_db()
        self.assertEqual(first_job.status, SalesImportJob.Status.QUEUED)
