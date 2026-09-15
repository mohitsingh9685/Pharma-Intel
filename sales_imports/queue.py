"""PostgreSQL-backed orchestration for durable sales-import jobs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.db.models import F, Q
from django.utils import timezone

from .models import (
    SalesImport,
    SalesImportAttempt,
    SalesImportIssue,
    SalesImportJob,
)


DEFAULT_LEASE_DURATION = timedelta(minutes=5)
DEFAULT_RETRY_BASE_DELAY = timedelta(seconds=30)
DEFAULT_RETRY_MAX_DELAY = timedelta(minutes=15)
EXHAUSTED_CLEANUP_BATCH_SIZE = 100


class SalesImportQueueError(RuntimeError):
    """Base error for an invalid durable-queue operation."""


class SalesImportClaimLost(SalesImportQueueError):
    """Raised when a worker no longer owns a live job lease."""


class SalesImportQueueStateError(SalesImportQueueError):
    """Raised when persisted queue state violates an orchestration invariant."""


@dataclass(frozen=True, slots=True)
class SalesImportClaim:
    """The fencing values a worker must present for every state change."""

    job_id: object
    sales_import_id: object
    attempt_id: uuid.UUID
    attempt_number: int
    lease_token: uuid.UUID
    worker_id: str
    lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class SalesImportJobCounts:
    """Validated counters copied to a job and its terminal attempt."""

    total_rows: int = 0
    valid_rows: int = 0
    invalid_rows: int = 0
    issue_count: int = 0
    inserted_rows: int = 0
    reused_rows: int = 0

    def validate_for(self, status: str) -> None:
        values = (
            self.total_rows,
            self.valid_rows,
            self.invalid_rows,
            self.issue_count,
            self.inserted_rows,
            self.reused_rows,
        )
        if any(value < 0 for value in values):
            raise ValueError("Sales-import counters cannot be negative.")
        if self.total_rows != self.valid_rows + self.invalid_rows:
            raise ValueError("Total rows must equal valid rows plus invalid rows.")
        if self.issue_count < self.invalid_rows:
            raise ValueError("Issue count cannot be lower than invalid rows.")

        if status == SalesImportJob.Status.PUBLISHED:
            if self.invalid_rows or self.issue_count:
                raise ValueError("A published import cannot contain validation issues.")
            if self.valid_rows != self.inserted_rows + self.reused_rows:
                raise ValueError(
                    "Published valid rows must equal inserted plus reused rows."
                )
        elif self.inserted_rows or self.reused_rows:
            raise ValueError("Only a published import can report applied rows.")

        if status == SalesImportJob.Status.REJECTED and self.issue_count == 0:
            raise ValueError("A rejected import must contain at least one issue.")


def _database_now(*, using: str) -> datetime:
    with connections[using].cursor() as cursor:
        cursor.execute("SELECT CURRENT_TIMESTAMP")
        return cursor.fetchone()[0]


def _effective_now(*, using: str, now: datetime | None) -> datetime:
    if now is None:
        return _database_now(using=using)
    if timezone.is_naive(now):
        raise ValueError("Queue timestamps must be timezone-aware.")
    return now


def _validate_duration(value: timedelta, *, name: str) -> timedelta:
    if value <= timedelta(0):
        raise ValueError(f"{name} must be positive.")
    return value


def _worker_id(value: str) -> str:
    normalized = str(value).strip()
    max_length = SalesImportJob._meta.get_field("leased_by").max_length
    if not normalized:
        raise ValueError("worker_id cannot be blank.")
    if len(normalized) > max_length:
        raise ValueError(f"worker_id may contain at most {max_length} characters.")
    return normalized


def _bounded_text(model, field_name: str, value: str) -> str:
    normalized = str(value or "").strip()
    max_length = model._meta.get_field(field_name).max_length
    return normalized[:max_length]


def _as_token(value: uuid.UUID | str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as error:
        raise SalesImportClaimLost(
            "The sales-import lease token is invalid."
        ) from error


def _timedelta_microseconds(value: timedelta) -> int:
    return (
        value.days * 86_400_000_000
        + value.seconds * 1_000_000
        + value.microseconds
    )


def retry_delay(
    attempt_count: int,
    *,
    base_delay: timedelta = DEFAULT_RETRY_BASE_DELAY,
    maximum_delay: timedelta = DEFAULT_RETRY_MAX_DELAY,
) -> timedelta:
    """Return a bounded ``base * 2**(attempt-1)`` retry delay."""

    if attempt_count < 1:
        raise ValueError("attempt_count must be positive.")
    _validate_duration(base_delay, name="base_delay")
    _validate_duration(maximum_delay, name="maximum_delay")

    delay_us = min(
        _timedelta_microseconds(base_delay),
        _timedelta_microseconds(maximum_delay),
    )
    maximum_us = _timedelta_microseconds(maximum_delay)
    for _ in range(attempt_count - 1):
        if delay_us >= maximum_us:
            break
        delay_us = min(delay_us * 2, maximum_us)
    return timedelta(microseconds=delay_us)


def enqueue_import(
    sales_import: SalesImport,
    *,
    max_attempts: int = 5,
    available_at: datetime | None = None,
    using: str = DEFAULT_DB_ALIAS,
) -> SalesImportJob:
    """Create or advance the one durable job belonging to an intake record.

    A receiving intake waits for its exact S3 version. A received CSV is queued,
    while XLSX waits for its future parser without consuming worker attempts. A
    storage failure fails a waiting job without manufacturing a worker run.
    """

    if sales_import.pk is None:
        raise ValueError("The sales import must be saved before it can be queued.")
    if not 1 <= max_attempts <= 32_767:
        raise ValueError("max_attempts must be between 1 and 32767.")

    with transaction.atomic(using=using):
        now = _effective_now(using=using, now=available_at)
        intake = (
            SalesImport.objects.using(using)
            .select_for_update()
            .get(pk=sales_import.pk)
        )
        if intake.status == SalesImport.Status.RECEIVING:
            desired_status = SalesImportJob.Status.WAITING_FOR_FILE
            finished_at = None
        elif intake.status == SalesImport.Status.RECEIVED:
            desired_status = (
                SalesImportJob.Status.QUEUED
                if intake.file_format == SalesImport.FileFormat.CSV
                else SalesImportJob.Status.AWAITING_PARSER
            )
            finished_at = None
        elif intake.status == SalesImport.Status.FAILED:
            desired_status = SalesImportJob.Status.FAILED
            finished_at = now
        else:
            raise SalesImportQueueStateError(
                f"Unsupported sales-import intake status: {intake.status}."
            )

        job, created = SalesImportJob.objects.using(using).get_or_create(
            sales_import=intake,
            defaults={
                "status": desired_status,
                "available_at": now,
                "max_attempts": max_attempts,
                "finished_at": finished_at,
            },
        )
        if created:
            return job

        if (
            job.status == SalesImportJob.Status.WAITING_FOR_FILE
            and desired_status
            in (
                SalesImportJob.Status.QUEUED,
                SalesImportJob.Status.AWAITING_PARSER,
            )
        ):
            job.status = desired_status
            job.available_at = now
            job.save(update_fields=("status", "available_at", "updated_at"))
        elif (
            job.status == SalesImportJob.Status.WAITING_FOR_FILE
            and desired_status == SalesImportJob.Status.FAILED
        ):
            job.status = SalesImportJob.Status.FAILED
            job.finished_at = now
            job.last_error_code = "INTAKE_STORAGE_FAILED"
            job.last_error_message = "The original file was not stored successfully."
            job.save(
                update_fields=(
                    "status",
                    "finished_at",
                    "last_error_code",
                    "last_error_message",
                    "updated_at",
                )
            )
        return job


def requeue_job(
    job_id,
    *,
    additional_attempts: int = 1,
    available_at: datetime | None = None,
    using: str = DEFAULT_DB_ALIAS,
) -> SalesImportJob:
    """Explicitly retry a recoverable terminal job without erasing history."""

    if (
        not isinstance(additional_attempts, int)
        or isinstance(additional_attempts, bool)
        or additional_attempts < 1
    ):
        raise ValueError("additional_attempts must be a positive integer.")

    with transaction.atomic(using=using):
        queued_at = _effective_now(using=using, now=available_at)
        job = (
            SalesImportJob.objects.using(using)
            .select_for_update()
            .select_related("sales_import")
            .get(pk=job_id)
        )
        if job.status not in (
            SalesImportJob.Status.FAILED,
            SalesImportJob.Status.REJECTED,
        ):
            raise SalesImportQueueStateError(
                "Only failed or rejected sales-import jobs can be retried."
            )
        if job.sales_import.status != SalesImport.Status.RECEIVED:
            raise SalesImportQueueStateError(
                "The original sales file must be received before retrying."
            )
        if job.sales_import.file_format != SalesImport.FileFormat.CSV:
            raise SalesImportQueueStateError(
                "This sales-import file format does not have a retryable parser."
            )
        if job.status == SalesImportJob.Status.REJECTED and (
            job.last_error_code == "SOURCE_RECORD_CONFLICT"
            or SalesImportIssue.objects.using(using).filter(
                attempt__job=job,
                code="SOURCE_RECORD_CONFLICT",
            ).exists()
        ):
            raise SalesImportQueueStateError(
                "An immutable source-record conflict cannot be retried."
            )

        new_max_attempts = (
            max(job.max_attempts, job.attempt_count) + additional_attempts
        )
        maximum_supported = 32_767
        if new_max_attempts > maximum_supported:
            raise ValueError(
                f"max_attempts cannot exceed {maximum_supported}."
            )

        job.status = SalesImportJob.Status.QUEUED
        job.available_at = queued_at
        job.max_attempts = new_max_attempts
        _clear_job_lease(job)
        job.finished_at = None
        job.last_error_code = ""
        job.last_error_message = ""
        _reset_job_counts(job)
        job.save(
            update_fields=(
                "status",
                "available_at",
                "max_attempts",
                "lease_token",
                "leased_by",
                "lease_expires_at",
                "heartbeat_at",
                "finished_at",
                "last_error_code",
                "last_error_message",
                "total_rows",
                "valid_rows",
                "invalid_rows",
                "issue_count",
                "inserted_rows",
                "reused_rows",
                "updated_at",
            )
        )
        return job


def _finish_expired_attempt(
    job: SalesImportJob,
    *,
    now: datetime,
    using: str,
) -> None:
    updated = (
        SalesImportAttempt.objects.using(using)
        .filter(
            job=job,
            lease_token=job.lease_token,
            outcome=SalesImportAttempt.Outcome.RUNNING,
        )
        .update(
            outcome=SalesImportAttempt.Outcome.LEASE_EXPIRED,
            finished_at=now,
            error_code="LEASE_EXPIRED",
            error_message="The worker did not renew its lease before expiry.",
        )
    )
    if updated != 1:
        raise SalesImportQueueStateError(
            "A processing job must have exactly one matching running attempt."
        )


def _clear_job_lease(job: SalesImportJob) -> None:
    job.lease_token = None
    job.leased_by = ""
    job.lease_expires_at = None
    job.heartbeat_at = None


def _reset_job_counts(job: SalesImportJob) -> None:
    job.total_rows = 0
    job.valid_rows = 0
    job.invalid_rows = 0
    job.issue_count = 0
    job.inserted_rows = 0
    job.reused_rows = 0


def _mark_exhausted(job: SalesImportJob, *, now: datetime) -> None:
    job.status = SalesImportJob.Status.FAILED
    _clear_job_lease(job)
    job.finished_at = now
    job.last_error_code = "ATTEMPT_LIMIT_EXHAUSTED"
    job.last_error_message = "The sales import exhausted its worker attempts."
    job.save(
        update_fields=(
            "status",
            "lease_token",
            "leased_by",
            "lease_expires_at",
            "heartbeat_at",
            "finished_at",
            "last_error_code",
            "last_error_message",
            "updated_at",
        )
    )


def _mark_exhausted_batch(*, now: datetime, using: str) -> int:
    """Finish a bounded batch before another job-claim transaction begins."""

    with transaction.atomic(using=using):
        jobs = list(
            SalesImportJob.objects.using(using)
            .select_for_update(skip_locked=True)
            .filter(attempt_count__gte=F("max_attempts"))
            .filter(
                Q(
                    status__in=(
                        SalesImportJob.Status.QUEUED,
                        SalesImportJob.Status.RETRY_WAIT,
                    ),
                    available_at__lte=now,
                )
                | Q(
                    status=SalesImportJob.Status.PROCESSING,
                    lease_expires_at__lte=now,
                )
            )
            .order_by("available_at", "created_at", "pk")[
                :EXHAUSTED_CLEANUP_BATCH_SIZE
            ]
        )
        for job in jobs:
            if job.status == SalesImportJob.Status.PROCESSING:
                _finish_expired_attempt(job, now=now, using=using)
            _mark_exhausted(job, now=now)
        return len(jobs)


def claim_next_job(
    *,
    worker_id: str,
    lease_duration: timedelta = DEFAULT_LEASE_DURATION,
    now: datetime | None = None,
    using: str = DEFAULT_DB_ALIAS,
) -> SalesImportClaim | None:
    """Claim one eligible job without blocking other concurrent workers."""

    worker_id = _worker_id(worker_id)
    lease_duration = _validate_duration(lease_duration, name="lease_duration")
    claimed_at = _effective_now(using=using, now=now)

    # Keep cleanup separate and bounded so an exhausted backlog cannot retain
    # locks while this worker claims unrelated ready work.
    _mark_exhausted_batch(now=claimed_at, using=using)

    with transaction.atomic(using=using):
        job = (
            SalesImportJob.objects.using(using)
            .select_for_update(skip_locked=True)
            .select_related("sales_import")
            .filter(attempt_count__lt=F("max_attempts"))
            .filter(
                Q(
                    status__in=(
                        SalesImportJob.Status.QUEUED,
                        SalesImportJob.Status.RETRY_WAIT,
                    ),
                    available_at__lte=claimed_at,
                )
                | Q(
                    status=SalesImportJob.Status.PROCESSING,
                    lease_expires_at__lte=claimed_at,
                )
            )
            .order_by("available_at", "created_at", "pk")
            .first()
        )
        if job is None:
            return None

        if job.status == SalesImportJob.Status.PROCESSING:
            _finish_expired_attempt(job, now=claimed_at, using=using)

        lease_token = uuid.uuid4()
        lease_expires_at = claimed_at + lease_duration
        attempt_number = job.attempt_count + 1

        job.status = SalesImportJob.Status.PROCESSING
        job.attempt_count = attempt_number
        job.lease_token = lease_token
        job.leased_by = worker_id
        job.lease_expires_at = lease_expires_at
        job.heartbeat_at = claimed_at
        if job.first_started_at is None:
            job.first_started_at = claimed_at
        job.finished_at = None
        job.last_error_code = ""
        job.last_error_message = ""
        _reset_job_counts(job)
        job.save(
            update_fields=(
                "status",
                "attempt_count",
                "lease_token",
                "leased_by",
                "lease_expires_at",
                "heartbeat_at",
                "first_started_at",
                "finished_at",
                "total_rows",
                "valid_rows",
                "invalid_rows",
                "issue_count",
                "inserted_rows",
                "reused_rows",
                "last_error_code",
                "last_error_message",
                "updated_at",
            )
        )
        attempt = SalesImportAttempt.objects.using(using).create(
            job=job,
            attempt_number=attempt_number,
            lease_token=lease_token,
            worker_id=worker_id,
            started_at=claimed_at,
            heartbeat_at=claimed_at,
        )
        return SalesImportClaim(
            job_id=job.pk,
            sales_import_id=job.sales_import_id,
            attempt_id=attempt.pk,
            attempt_number=attempt_number,
            lease_token=lease_token,
            worker_id=worker_id,
            lease_expires_at=lease_expires_at,
        )


def _locked_live_claim(
    *,
    job_id,
    lease_token: uuid.UUID | str,
    now: datetime,
    using: str,
) -> tuple[SalesImportJob, SalesImportAttempt]:
    token = _as_token(lease_token)
    job = (
        SalesImportJob.objects.using(using)
        .select_for_update()
        .filter(
            pk=job_id,
            status=SalesImportJob.Status.PROCESSING,
            lease_token=token,
            lease_expires_at__gt=now,
        )
        .first()
    )
    if job is None:
        raise SalesImportClaimLost("The sales-import worker no longer owns this job.")

    attempt = (
        SalesImportAttempt.objects.using(using)
        .select_for_update()
        .filter(
            job=job,
            lease_token=token,
            outcome=SalesImportAttempt.Outcome.RUNNING,
        )
        .first()
    )
    if attempt is None:
        raise SalesImportQueueStateError(
            "The live job lease has no matching running attempt."
        )
    return job, attempt


def heartbeat_job(
    *,
    job_id,
    lease_token: uuid.UUID | str,
    lease_duration: timedelta = DEFAULT_LEASE_DURATION,
    now: datetime | None = None,
    using: str = DEFAULT_DB_ALIAS,
) -> datetime:
    """Renew a live claim; an expired or replaced token cannot be revived."""

    lease_duration = _validate_duration(lease_duration, name="lease_duration")
    with transaction.atomic(using=using):
        heartbeat_at = _effective_now(using=using, now=now)
        job, attempt = _locked_live_claim(
            job_id=job_id,
            lease_token=lease_token,
            now=heartbeat_at,
            using=using,
        )
        lease_expires_at = heartbeat_at + lease_duration
        job.heartbeat_at = heartbeat_at
        job.lease_expires_at = lease_expires_at
        job.save(
            update_fields=("heartbeat_at", "lease_expires_at", "updated_at")
        )
        attempt.heartbeat_at = heartbeat_at
        attempt.save(update_fields=("heartbeat_at",))
        return lease_expires_at


def advance_attempt_phase(
    *,
    job_id,
    lease_token: uuid.UUID | str,
    phase: str,
    now: datetime | None = None,
    using: str = DEFAULT_DB_ALIAS,
) -> None:
    """Move the current attempt phase while enforcing the lease fence."""

    if phase not in SalesImportAttempt.Phase.values:
        raise ValueError("A recognized sales-import attempt phase is required.")
    with transaction.atomic(using=using):
        checked_at = _effective_now(using=using, now=now)
        _, attempt = _locked_live_claim(
            job_id=job_id,
            lease_token=lease_token,
            now=checked_at,
            using=using,
        )
        attempt.phase = phase
        attempt.save(update_fields=("phase",))


def _counts_from_job(job: SalesImportJob) -> SalesImportJobCounts:
    return SalesImportJobCounts(
        total_rows=job.total_rows,
        valid_rows=job.valid_rows,
        invalid_rows=job.invalid_rows,
        issue_count=job.issue_count,
        inserted_rows=job.inserted_rows,
        reused_rows=job.reused_rows,
    )


def _apply_counts(target, counts: SalesImportJobCounts) -> None:
    target.total_rows = counts.total_rows
    target.valid_rows = counts.valid_rows
    target.invalid_rows = counts.invalid_rows
    target.issue_count = counts.issue_count
    target.inserted_rows = counts.inserted_rows
    target.reused_rows = counts.reused_rows


def schedule_retry(
    *,
    job_id,
    lease_token: uuid.UUID | str,
    error_code: str,
    error_message: str,
    base_delay: timedelta = DEFAULT_RETRY_BASE_DELAY,
    maximum_delay: timedelta = DEFAULT_RETRY_MAX_DELAY,
    now: datetime | None = None,
    using: str = DEFAULT_DB_ALIAS,
) -> SalesImportJob:
    """Finish the current attempt and retry later, or fail at the limit."""

    with transaction.atomic(using=using):
        failed_at = _effective_now(using=using, now=now)
        job, attempt = _locked_live_claim(
            job_id=job_id,
            lease_token=lease_token,
            now=failed_at,
            using=using,
        )
        job_error_code = _bounded_text(
            SalesImportJob, "last_error_code", error_code
        )
        job_error_message = _bounded_text(
            SalesImportJob, "last_error_message", error_message
        )
        attempt_error_code = _bounded_text(
            SalesImportAttempt, "error_code", error_code
        )
        attempt_error_message = _bounded_text(
            SalesImportAttempt, "error_message", error_message
        )

        counts = _counts_from_job(job)
        counts.validate_for(SalesImportJob.Status.FAILED)
        _apply_counts(attempt, counts)
        attempt.outcome = SalesImportAttempt.Outcome.RETRYABLE_FAILURE
        attempt.finished_at = failed_at
        attempt.error_code = attempt_error_code
        attempt.error_message = attempt_error_message
        attempt.save(
            update_fields=(
                "outcome",
                "finished_at",
                "error_code",
                "error_message",
                "total_rows",
                "valid_rows",
                "invalid_rows",
                "issue_count",
                "inserted_rows",
                "reused_rows",
            )
        )

        _clear_job_lease(job)
        job.last_error_code = job_error_code
        job.last_error_message = job_error_message
        if job.attempt_count >= job.max_attempts:
            job.status = SalesImportJob.Status.FAILED
            job.finished_at = failed_at
        else:
            delay = retry_delay(
                job.attempt_count,
                base_delay=base_delay,
                maximum_delay=maximum_delay,
            )
            job.status = SalesImportJob.Status.RETRY_WAIT
            job.available_at = failed_at + delay
            job.finished_at = None
        job.save(
            update_fields=(
                "status",
                "available_at",
                "lease_token",
                "leased_by",
                "lease_expires_at",
                "heartbeat_at",
                "finished_at",
                "last_error_code",
                "last_error_message",
                "updated_at",
            )
        )
        return job


_TERMINAL_OUTCOMES = {
    SalesImportJob.Status.PUBLISHED: SalesImportAttempt.Outcome.PUBLISHED,
    SalesImportJob.Status.REJECTED: SalesImportAttempt.Outcome.REJECTED,
    SalesImportJob.Status.FAILED: SalesImportAttempt.Outcome.PERMANENT_FAILURE,
    SalesImportJob.Status.CANCELLED: SalesImportAttempt.Outcome.CANCELLED,
}


def complete_job(
    *,
    job_id,
    lease_token: uuid.UUID | str,
    status: str,
    counts: SalesImportJobCounts | None = None,
    error_code: str = "",
    error_message: str = "",
    now: datetime | None = None,
    using: str = DEFAULT_DB_ALIAS,
) -> SalesImportJob:
    """Commit a terminal job/attempt transition guarded by the lease token."""

    try:
        outcome = _TERMINAL_OUTCOMES[status]
    except KeyError as error:
        raise ValueError(
            "A recognized terminal sales-import status is required."
        ) from error

    with transaction.atomic(using=using):
        finished_at = _effective_now(using=using, now=now)
        job, attempt = _locked_live_claim(
            job_id=job_id,
            lease_token=lease_token,
            now=finished_at,
            using=using,
        )
        terminal_counts = counts if counts is not None else _counts_from_job(job)
        terminal_counts.validate_for(status)
        _apply_counts(job, terminal_counts)
        _apply_counts(attempt, terminal_counts)

        job.status = status
        _clear_job_lease(job)
        job.finished_at = finished_at
        job.last_error_code = _bounded_text(
            SalesImportJob, "last_error_code", error_code
        )
        job.last_error_message = _bounded_text(
            SalesImportJob, "last_error_message", error_message
        )

        attempt.outcome = outcome
        attempt.finished_at = finished_at
        attempt.error_code = _bounded_text(
            SalesImportAttempt, "error_code", error_code
        )
        attempt.error_message = _bounded_text(
            SalesImportAttempt, "error_message", error_message
        )
        if status == SalesImportJob.Status.PUBLISHED:
            attempt.phase = SalesImportAttempt.Phase.COMPLETE

        attempt.save(
            update_fields=(
                "phase",
                "outcome",
                "finished_at",
                "error_code",
                "error_message",
                "total_rows",
                "valid_rows",
                "invalid_rows",
                "issue_count",
                "inserted_rows",
                "reused_rows",
            )
        )
        job.save(
            update_fields=(
                "status",
                "lease_token",
                "leased_by",
                "lease_expires_at",
                "heartbeat_at",
                "finished_at",
                "last_error_code",
                "last_error_message",
                "total_rows",
                "valid_rows",
                "invalid_rows",
                "issue_count",
                "inserted_rows",
                "reused_rows",
                "updated_at",
            )
        )
        return job
