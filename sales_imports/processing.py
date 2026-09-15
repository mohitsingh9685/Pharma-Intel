"""Durable CSV validation and atomic publication of sales facts."""

from __future__ import annotations

import hashlib
import io
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import batched

from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, DatabaseError, IntegrityError, connections
from django.db import transaction
from django.db.models import Q

from business_data.models import CalendarDate, SalesTransaction
from master_data.models import (
    Hospital,
    HospitalTerritoryAssignment,
    Product,
    SalesRepresentative,
    SalesRepresentativeTerritoryAssignment,
    Territory,
)

from .contracts import SalesFileContractError, iter_sales_rows_v1_csv
from .models import (
    SalesImport,
    SalesImportAttempt,
    SalesImportIssue,
    SalesImportJob,
    SalesImportStagedRow,
)
from .queue import (
    DEFAULT_LEASE_DURATION,
    SalesImportClaim,
    SalesImportClaimLost,
    SalesImportJobCounts,
    _database_now,
    _locked_live_claim,
    advance_attempt_phase,
    complete_job,
    heartbeat_job,
    schedule_retry,
)
from .storage import (
    OriginalFileDownloadError,
    OriginalFileKMSProvenanceError,
    OriginalFileNotFoundError,
    OriginalFileStorageError,
    OriginalFileVerificationError,
    get_sales_import_storage,
)


logger = logging.getLogger(__name__)


_DOWNLOAD_HEARTBEAT_INTERVAL_SECONDS = min(
    60.0,
    DEFAULT_LEASE_DURATION.total_seconds() / 3,
)
_SOURCE_LOCK_POLL_SECONDS = 0.25


def _source_lock_wait_timeout_seconds() -> float:
    return float(settings.SALES_IMPORT_SOURCE_LOCK_WAIT_SECONDS)


@dataclass(frozen=True, slots=True)
class SalesImportProcessingResult:
    job_id: object
    status: str
    counts: SalesImportJobCounts


class _DownloadLeaseHeartbeat:
    """Rate-limit lease renewals requested by streamed download progress."""

    def __init__(
        self,
        *,
        claim: SalesImportClaim,
        using: str,
        interval_seconds: float = _DOWNLOAD_HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("Download heartbeat interval must be positive.")
        self.claim = claim
        self.using = using
        self.interval_seconds = interval_seconds
        self._last_heartbeat = time.monotonic()

    def __call__(self, downloaded_bytes: int) -> None:
        del downloaded_bytes
        checked_at = time.monotonic()
        if checked_at - self._last_heartbeat < self.interval_seconds:
            return
        heartbeat_job(
            job_id=self.claim.job_id,
            lease_token=self.claim.lease_token,
            using=self.using,
        )
        self._last_heartbeat = checked_at


@dataclass(slots=True)
class _Progress:
    total_rows: int = 0
    valid_rows: int = 0
    invalid_rows: int = 0
    issue_count: int = 0

    def counts(self, *, inserted_rows: int = 0, reused_rows: int = 0):
        return SalesImportJobCounts(
            total_rows=self.total_rows,
            valid_rows=self.valid_rows,
            invalid_rows=self.invalid_rows,
            issue_count=self.issue_count,
            inserted_rows=inserted_rows,
            reused_rows=reused_rows,
        )


class _IssueCollector:
    """Count every issue while bounding retained detail rows and memory."""

    def __init__(self, *, attempt_id, maximum: int):
        self.attempt_id = attempt_id
        self.maximum = maximum
        self.total = 0
        self._issues: list[SalesImportIssue] = []
        self._pending_identities: set[tuple[int | None, str, str]] = set()
        self._detail_count = 0
        self._last_phase = SalesImportIssue.Phase.CONTRACT
        self.truncated = False

    def add(
        self,
        *,
        row_number: int | None,
        column: str,
        code: str,
        message: str,
        phase: str,
    ) -> None:
        identity = (row_number, column, code)
        if identity in self._pending_identities:
            return
        self._pending_identities.add(identity)
        self.total += 1
        self._last_phase = phase
        reserve_for_summary = 1 if self.maximum > 1 else 0
        detail_limit = self.maximum - reserve_for_summary
        if self._detail_count < detail_limit:
            self._issues.append(
                SalesImportIssue(
                    attempt_id=self.attempt_id,
                    row_number=row_number,
                    column=column,
                    code=code,
                    message=message,
                    phase=phase,
                )
            )
            self._detail_count += 1
        else:
            self.truncated = True

    def drain(self) -> list[SalesImportIssue]:
        """Release retained details so callers can persist them by batch."""

        issues = self._issues
        self._issues = []
        self._pending_identities.clear()
        return issues

    def persisted(self) -> list[SalesImportIssue]:
        issues = self.drain()
        if self.truncated and self.maximum:
            summary = SalesImportIssue(
                attempt_id=self.attempt_id,
                row_number=None,
                column="__file__",
                code="ISSUE_LIMIT_REACHED",
                message=(
                    f"Only the first {self._detail_count} detailed validation "
                    "issues were stored. Correct those issues and submit a new file."
                ),
                phase=self._last_phase,
            )
            if self.maximum == 1:
                return [summary]
            issues.append(summary)
        return issues


def _chunk_size() -> int:
    return settings.SALES_IMPORT_DB_BATCH_SIZE


def _max_issues() -> int:
    return settings.SALES_IMPORT_MAX_ISSUES


def _claim_matches(attempt: SalesImportAttempt, claim: SalesImportClaim) -> None:
    if attempt.pk != claim.attempt_id:
        raise SalesImportClaimLost(
            "The sales-import claim no longer matches its running attempt."
        )


def _record_parse_batch(
    *,
    claim: SalesImportClaim,
    staged_rows: list[SalesImportStagedRow],
    issues: list[SalesImportIssue],
    progress: _Progress,
    using: str,
) -> None:
    """Persist one parse batch only while the fencing token remains live."""

    with transaction.atomic(using=using):
        now = _database_now(using=using)
        job, attempt = _locked_live_claim(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            now=now,
            using=using,
        )
        _claim_matches(attempt, claim)
        if staged_rows:
            SalesImportStagedRow.objects.using(using).bulk_create(
                staged_rows,
                batch_size=_chunk_size(),
            )
        if issues:
            SalesImportIssue.objects.using(using).bulk_create(
                issues,
                batch_size=_chunk_size(),
            )

        job.total_rows = progress.total_rows
        job.valid_rows = progress.valid_rows
        job.invalid_rows = progress.invalid_rows
        job.issue_count = progress.issue_count
        job.heartbeat_at = now
        job.lease_expires_at = now + DEFAULT_LEASE_DURATION
        job.save(
            update_fields=(
                "total_rows",
                "valid_rows",
                "invalid_rows",
                "issue_count",
                "heartbeat_at",
                "lease_expires_at",
                "updated_at",
            )
        )
        attempt.total_rows = progress.total_rows
        attempt.valid_rows = progress.valid_rows
        attempt.invalid_rows = progress.invalid_rows
        attempt.issue_count = progress.issue_count
        attempt.heartbeat_at = now
        attempt.phase = SalesImportAttempt.Phase.PARSE
        attempt.save(
            update_fields=(
                "total_rows",
                "valid_rows",
                "invalid_rows",
                "issue_count",
                "heartbeat_at",
                "phase",
            )
        )


def _stage_csv(
    *,
    claim: SalesImportClaim,
    binary_file,
    using: str,
) -> tuple[_Progress, bool]:
    progress = _Progress()
    collector = _IssueCollector(attempt_id=claim.attempt_id, maximum=_max_issues())
    pending_rows: list[SalesImportStagedRow] = []
    file_rejected = False

    text_file = io.TextIOWrapper(binary_file, encoding="utf-8", newline="")
    try:
        try:
            for result in iter_sales_rows_v1_csv(
                text_file,
                max_rows=settings.SALES_IMPORT_MAX_ROWS,
            ):
                progress.total_rows += 1
                if result.is_valid:
                    progress.valid_rows += 1
                    row = result.row
                    pending_rows.append(
                        SalesImportStagedRow(
                            attempt_id=claim.attempt_id,
                            row_number=result.row_number,
                            source_record_id=row.source_record_id,
                            transaction_date=row.transaction_date,
                            product_code=row.product_code,
                            territory_code=row.territory_code,
                            hospital_code=row.hospital_code or "",
                            sales_representative_code=(
                                row.sales_representative_code or ""
                            ),
                            quantity=row.quantity,
                            revenue_amount=row.revenue_amount,
                            currency_code=row.currency_code,
                        )
                    )
                else:
                    progress.invalid_rows += 1
                    for issue in result.issues:
                        collector.add(
                            row_number=issue.row_number,
                            column=issue.column,
                            code=str(issue.code),
                            message=issue.message,
                            phase=SalesImportIssue.Phase.CONTRACT,
                        )
                    progress.issue_count = collector.total

                if progress.total_rows % _chunk_size() == 0:
                    _record_parse_batch(
                        claim=claim,
                        staged_rows=pending_rows,
                        issues=collector.drain(),
                        progress=progress,
                        using=using,
                    )
                    pending_rows = []
        except SalesFileContractError as error:
            file_rejected = True
            collector.add(
                row_number=None,
                column="__file__",
                code=str(error.code),
                message=str(error),
                phase=SalesImportIssue.Phase.CONTRACT,
            )
        except UnicodeDecodeError:
            file_rejected = True
            collector.add(
                row_number=None,
                column="__file__",
                code="FILE_ENCODING_INVALID",
                message="The CSV must contain valid UTF-8 text.",
                phase=SalesImportIssue.Phase.CONTRACT,
            )
    finally:
        # The storage context owns the binary stream.
        try:
            text_file.detach()
        except (ValueError, OSError):
            pass

    progress.issue_count = collector.total
    file_rejected = file_rejected or progress.invalid_rows > 0

    _record_parse_batch(
        claim=claim,
        staged_rows=pending_rows,
        issues=collector.persisted(),
        progress=progress,
        using=using,
    )
    return progress, file_rejected


def _master_map(model, codes: set[str], *, using: str):
    return {
        item.code: item
        for item in model.objects.using(using).filter(code__in=codes)
    }


def _effective_assignment_ids(
    model,
    *,
    identity_field: str,
    identity_ids: set[int],
    territory_ids: set[int],
    minimum_date,
    maximum_date,
    using: str,
) -> dict[tuple[int, int], tuple[tuple[object, object | None], ...]]:
    if not identity_ids or not territory_ids:
        return {}
    queryset = (
        model.objects.using(using)
        .filter(
            **{
                f"{identity_field}_id__in": identity_ids,
                "territory_id__in": territory_ids,
                "effective_from__lte": maximum_date,
            }
        )
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=minimum_date))
        .values_list(
            f"{identity_field}_id",
            "territory_id",
            "effective_from",
            "effective_to",
        )
    )
    grouped: dict[tuple[int, int], list[tuple[object, object | None]]] = {}
    for identity_id, territory_id, effective_from, effective_to in queryset:
        grouped.setdefault((identity_id, territory_id), []).append(
            (effective_from, effective_to)
        )
    return {key: tuple(periods) for key, periods in grouped.items()}


def _is_effective(periods, transaction_date) -> bool:
    return any(
        effective_from <= transaction_date
        and (effective_to is None or effective_to >= transaction_date)
        for effective_from, effective_to in periods
    )


def _resolve_and_validate_chunk(
    rows: tuple[SalesImportStagedRow, ...],
    *,
    source_system: str,
    collector: _IssueCollector,
    using: str,
) -> int:
    dates = {row.transaction_date for row in rows}
    calendars = CalendarDate.objects.using(using).in_bulk(dates)
    products = _master_map(Product, {row.product_code for row in rows}, using=using)
    territories = _master_map(
        Territory,
        {row.territory_code for row in rows},
        using=using,
    )
    hospitals = _master_map(
        Hospital,
        {row.hospital_code for row in rows if row.hospital_code},
        using=using,
    )
    representatives = _master_map(
        SalesRepresentative,
        {
            row.sales_representative_code
            for row in rows
            if row.sales_representative_code
        },
        using=using,
    )

    minimum_date = min(dates)
    maximum_date = max(dates)
    hospital_assignments = _effective_assignment_ids(
        HospitalTerritoryAssignment,
        identity_field="hospital",
        identity_ids={item.pk for item in hospitals.values()},
        territory_ids={item.pk for item in territories.values()},
        minimum_date=minimum_date,
        maximum_date=maximum_date,
        using=using,
    )
    representative_assignments = _effective_assignment_ids(
        SalesRepresentativeTerritoryAssignment,
        identity_field="sales_representative",
        identity_ids={item.pk for item in representatives.values()},
        territory_ids={item.pk for item in territories.values()},
        minimum_date=minimum_date,
        maximum_date=maximum_date,
        using=using,
    )
    existing_facts = {
        fact.source_record_id: fact
        for fact in SalesTransaction.objects.using(using).filter(
            source_system=source_system,
            source_record_id__in=[row.source_record_id for row in rows],
        )
    }

    invalid_rows = 0
    changed_rows: list[SalesImportStagedRow] = []
    for row in rows:
        calendar = calendars.get(row.transaction_date)
        product = products.get(row.product_code)
        territory = territories.get(row.territory_code)
        hospital = hospitals.get(row.hospital_code) if row.hospital_code else None
        representative = (
            representatives.get(row.sales_representative_code)
            if row.sales_representative_code
            else None
        )
        row_issue_count = collector.total

        if calendar is None:
            collector.add(
                row_number=row.row_number,
                column="transaction_date",
                code="CALENDAR_DATE_NOT_FOUND",
                message="The transaction date is missing from the reporting calendar.",
                phase=SalesImportIssue.Phase.MASTER_DATA,
            )
        if product is None:
            collector.add(
                row_number=row.row_number,
                column="product_code",
                code="PRODUCT_NOT_FOUND",
                message="The product code does not exist.",
                phase=SalesImportIssue.Phase.MASTER_DATA,
            )
        if territory is None:
            collector.add(
                row_number=row.row_number,
                column="territory_code",
                code="TERRITORY_NOT_FOUND",
                message="The territory code does not exist.",
                phase=SalesImportIssue.Phase.MASTER_DATA,
            )
        elif territory.level != Territory.Level.TERRITORY:
            collector.add(
                row_number=row.row_number,
                column="territory_code",
                code="TERRITORY_NOT_LEAF",
                message="Sales rows must use a Territory-level territory.",
                phase=SalesImportIssue.Phase.MASTER_DATA,
            )
        if row.hospital_code and hospital is None:
            collector.add(
                row_number=row.row_number,
                column="hospital_code",
                code="HOSPITAL_NOT_FOUND",
                message="The hospital code does not exist.",
                phase=SalesImportIssue.Phase.MASTER_DATA,
            )
        if row.sales_representative_code and representative is None:
            collector.add(
                row_number=row.row_number,
                column="sales_representative_code",
                code="SALES_REPRESENTATIVE_NOT_FOUND",
                message="The sales representative code does not exist.",
                phase=SalesImportIssue.Phase.MASTER_DATA,
            )

        if hospital is not None and territory is not None and not _is_effective(
            hospital_assignments.get((hospital.pk, territory.pk), ()),
            row.transaction_date,
        ):
            collector.add(
                row_number=row.row_number,
                column="hospital_code",
                code="HOSPITAL_TERRITORY_MISMATCH",
                message=(
                    "The hospital was not assigned to this territory on the "
                    "transaction date."
                ),
                phase=SalesImportIssue.Phase.MASTER_DATA,
            )
        if (
            representative is not None
            and territory is not None
            and not _is_effective(
                representative_assignments.get(
                    (representative.pk, territory.pk),
                    (),
                ),
                row.transaction_date,
            )
        ):
            collector.add(
                row_number=row.row_number,
                column="sales_representative_code",
                code="SALES_REPRESENTATIVE_TERRITORY_MISMATCH",
                message=(
                    "The sales representative was not assigned to this territory "
                    "on the transaction date."
                ),
                phase=SalesImportIssue.Phase.MASTER_DATA,
            )

        if collector.total > row_issue_count:
            invalid_rows += 1
            continue

        row.calendar_date = calendar
        row.product = product
        row.territory = territory
        row.hospital = hospital
        row.sales_representative = representative
        existing_fact = existing_facts.get(row.source_record_id)
        if existing_fact is not None and not _fact_matches(row, existing_fact):
            collector.add(
                row_number=row.row_number,
                column="source_record_id",
                code="SOURCE_RECORD_CONFLICT",
                message=(
                    "This source record ID already exists with different sales values."
                ),
                phase=SalesImportIssue.Phase.IDEMPOTENCY,
            )
            invalid_rows += 1
            continue
        changed_rows.append(row)

    if changed_rows:
        SalesImportStagedRow.objects.using(using).bulk_update(
            changed_rows,
            fields=(
                "calendar_date",
                "product",
                "territory",
                "hospital",
                "sales_representative",
            ),
            batch_size=_chunk_size(),
        )
    return invalid_rows


def _fact_matches(row: SalesImportStagedRow, fact: SalesTransaction) -> bool:
    return (
        fact.calendar_date_id == row.transaction_date
        and fact.product_id == row.product_id
        and fact.territory_id == row.territory_id
        and fact.hospital_id == row.hospital_id
        and fact.sales_representative_id == row.sales_representative_id
        and fact.quantity == row.quantity
        and fact.revenue_amount == row.revenue_amount
        and fact.currency_code == row.currency_code
    )


def _source_lock_key(source_system: str) -> int:
    digest = hashlib.sha256(source_system.encode("utf-8")).digest()[:8]
    return int.from_bytes(digest, byteorder="big", signed=True)


@contextmanager
def _source_session_lock(
    *,
    claim: SalesImportClaim,
    source_system: str,
    using: str,
):
    """Serialize one source before opening the publication snapshot.

    The session lock is acquired with short polls so a waiting worker can renew
    its lease. Acquiring it before ``REPEATABLE READ`` prevents a waiter from
    publishing against a snapshot taken before the preceding import committed.
    """

    connection = connections[using]
    lock_key = _source_lock_key(source_system)
    started_at = time.monotonic()
    heartbeat_at = started_at + _DOWNLOAD_HEARTBEAT_INTERVAL_SECONDS
    deadline = started_at + _source_lock_wait_timeout_seconds()
    acquired = False
    try:
        while not acquired:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_key])
                acquired = bool(cursor.fetchone()[0])
            if acquired:
                break
            checked_at = time.monotonic()
            if checked_at >= deadline:
                raise DatabaseError(
                    "Timed out waiting to publish another import from this source."
                )
            if checked_at >= heartbeat_at:
                heartbeat_job(
                    job_id=claim.job_id,
                    lease_token=claim.lease_token,
                    using=using,
                )
                heartbeat_at = checked_at + _DOWNLOAD_HEARTBEAT_INTERVAL_SECONDS
            time.sleep(_SOURCE_LOCK_POLL_SECONDS)

        heartbeat_job(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            using=using,
        )
        yield
    finally:
        if acquired:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_key])
                    if not cursor.fetchone()[0]:
                        raise DatabaseError(
                            "The sales-import source lock was lost unexpectedly."
                        )
            except DatabaseError:
                logger.exception(
                    "Could not release the sales-import source lock for %s.",
                    source_system,
                )
                try:
                    # PostgreSQL releases session advisory locks when their
                    # connection closes. Cleanup failure must not replace a
                    # publication result that has already committed.
                    connection.close()
                except DatabaseError:
                    logger.exception(
                        "Could not close the database connection after the "
                        "sales-import source lock release failed for %s.",
                        source_system,
                    )


def _database_wall_clock(*, using: str):
    """Return actual PostgreSQL wall time inside a long transaction."""

    with connections[using].cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        return cursor.fetchone()[0]


def _integrity_constraint_name(error: IntegrityError) -> str:
    cause = getattr(error, "__cause__", None)
    diagnostic = getattr(cause, "diag", None)
    return str(getattr(diagnostic, "constraint_name", "") or "")


class _PublicationRejected(Exception):
    def __init__(self, *, collector: _IssueCollector, invalid_rows: int):
        self.collector = collector
        self.invalid_rows = invalid_rows


def _link_or_insert_chunk(
    rows: tuple[SalesImportStagedRow, ...],
    *,
    source_system: str,
    created_by_id: int,
    collector: _IssueCollector,
    using: str,
) -> tuple[int, int]:
    source_ids = [row.source_record_id for row in rows]
    existing = {
        fact.source_record_id: fact
        for fact in SalesTransaction.objects.using(using).filter(
            source_system=source_system,
            source_record_id__in=source_ids,
        )
    }
    to_insert: list[SalesTransaction] = []
    row_by_source_id = {row.source_record_id: row for row in rows}
    reused = 0

    for row in rows:
        fact = existing.get(row.source_record_id)
        if fact is None:
            to_insert.append(
                SalesTransaction(
                    calendar_date_id=row.calendar_date_id,
                    product_id=row.product_id,
                    territory_id=row.territory_id,
                    hospital_id=row.hospital_id,
                    sales_representative_id=row.sales_representative_id,
                    quantity=row.quantity,
                    revenue_amount=row.revenue_amount,
                    currency_code=row.currency_code,
                    source_system=source_system,
                    source_record_id=row.source_record_id,
                    created_by_id=created_by_id,
                )
            )
        elif _fact_matches(row, fact):
            row.outcome = SalesImportStagedRow.Outcome.REUSED
            row.sales_transaction = fact
            reused += 1
        else:
            collector.add(
                row_number=row.row_number,
                column="source_record_id",
                code="SOURCE_RECORD_CONFLICT",
                message=(
                    "This source record ID already exists with different sales values."
                ),
                phase=SalesImportIssue.Phase.IDEMPOTENCY,
            )

    if collector.total:
        raise _PublicationRejected(collector=collector, invalid_rows=collector.total)

    if to_insert:
        SalesTransaction.objects.using(using).bulk_create(
            to_insert,
            batch_size=_chunk_size(),
        )
        for fact in to_insert:
            row = row_by_source_id[fact.source_record_id]
            row.outcome = SalesImportStagedRow.Outcome.PUBLISHED
            row.sales_transaction = fact

    SalesImportStagedRow.objects.using(using).bulk_update(
        rows,
        fields=("outcome", "sales_transaction"),
        batch_size=_chunk_size(),
    )
    return len(to_insert), reused


def _finish_locked_job(
    *,
    job: SalesImportJob,
    attempt: SalesImportAttempt,
    status: str,
    counts: SalesImportJobCounts,
    error_code: str = "",
    error_message: str = "",
    using: str,
) -> None:
    """Finish a claim already fenced by a held job-row lock."""

    counts.validate_for(status)
    finished_at = _database_wall_clock(using=using)
    for target in (job, attempt):
        target.total_rows = counts.total_rows
        target.valid_rows = counts.valid_rows
        target.invalid_rows = counts.invalid_rows
        target.issue_count = counts.issue_count
        target.inserted_rows = counts.inserted_rows
        target.reused_rows = counts.reused_rows

    outcome = {
        SalesImportJob.Status.PUBLISHED: SalesImportAttempt.Outcome.PUBLISHED,
        SalesImportJob.Status.REJECTED: SalesImportAttempt.Outcome.REJECTED,
    }[status]
    job.status = status
    job.lease_token = None
    job.leased_by = ""
    job.lease_expires_at = None
    job.heartbeat_at = None
    job.finished_at = finished_at
    job.last_error_code = error_code
    job.last_error_message = error_message
    attempt.outcome = outcome
    attempt.finished_at = finished_at
    attempt.error_code = error_code
    attempt.error_message = error_message
    if status == SalesImportJob.Status.PUBLISHED:
        attempt.phase = SalesImportAttempt.Phase.COMPLETE

    attempt.save(
        using=using,
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
        ),
    )
    job.save(
        using=using,
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
        ),
    )


def _publish_once(
    *,
    claim: SalesImportClaim,
    using: str,
) -> SalesImportProcessingResult:
    with transaction.atomic(using=using):
        with connections[using].cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        started_at = _database_now(using=using)
        job, attempt = _locked_live_claim(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            now=started_at,
            using=using,
        )
        _claim_matches(attempt, claim)
        sales_import = SalesImport.objects.using(using).get(pk=job.sales_import_id)
        attempt.phase = SalesImportAttempt.Phase.VALIDATE
        attempt.save(update_fields=("phase",))

        staged_queryset = (
            SalesImportStagedRow.objects.using(using)
            .filter(attempt=attempt)
            .order_by("row_number")
        )
        total_rows = staged_queryset.count()
        collector = _IssueCollector(attempt_id=attempt.pk, maximum=_max_issues())
        invalid_rows = 0
        for rows in batched(
            staged_queryset.iterator(chunk_size=_chunk_size()),
            _chunk_size(),
        ):
            invalid_rows += _resolve_and_validate_chunk(
                rows,
                source_system=sales_import.source_system,
                collector=collector,
                using=using,
            )
            pending_issues = collector.drain()
            if pending_issues:
                SalesImportIssue.objects.using(using).bulk_create(
                    pending_issues,
                    batch_size=_chunk_size(),
                )

        if collector.total:
            SalesImportIssue.objects.using(using).bulk_create(
                collector.persisted(),
                batch_size=_chunk_size(),
            )
            counts = SalesImportJobCounts(
                total_rows=total_rows,
                valid_rows=total_rows - invalid_rows,
                invalid_rows=invalid_rows,
                issue_count=collector.total,
            )
            _finish_locked_job(
                job=job,
                attempt=attempt,
                status=SalesImportJob.Status.REJECTED,
                counts=counts,
                error_code="VALIDATION_REJECTED",
                error_message="Database-backed validation rejected the sales file.",
                using=using,
            )
            return SalesImportProcessingResult(job.pk, job.status, counts)

        inserted_rows = reused_rows = 0
        attempt.phase = SalesImportAttempt.Phase.PUBLISH
        attempt.save(update_fields=("phase",))
        try:
            with transaction.atomic(using=using):
                for rows in batched(
                    staged_queryset.iterator(chunk_size=_chunk_size()),
                    _chunk_size(),
                ):
                    inserted, reused = _link_or_insert_chunk(
                        rows,
                        source_system=sales_import.source_system,
                        created_by_id=sales_import.uploaded_by_id,
                        collector=collector,
                        using=using,
                    )
                    inserted_rows += inserted
                    reused_rows += reused
                if collector.total:
                    raise _PublicationRejected(
                        collector=collector,
                        invalid_rows=collector.total,
                    )
        except _PublicationRejected as rejected:
            SalesImportIssue.objects.using(using).bulk_create(
                rejected.collector.persisted(),
                batch_size=_chunk_size(),
            )
            # One conflict issue is emitted per conflicting row.
            invalid_rows = rejected.collector.total
            counts = SalesImportJobCounts(
                total_rows=total_rows,
                valid_rows=total_rows - invalid_rows,
                invalid_rows=invalid_rows,
                issue_count=rejected.collector.total,
            )
            _finish_locked_job(
                job=job,
                attempt=attempt,
                status=SalesImportJob.Status.REJECTED,
                counts=counts,
                error_code="SOURCE_RECORD_CONFLICT",
                error_message=(
                    "One or more source record IDs conflict with immutable facts."
                ),
                using=using,
            )
            return SalesImportProcessingResult(job.pk, job.status, counts)

        counts = SalesImportJobCounts(
            total_rows=total_rows,
            valid_rows=total_rows,
            invalid_rows=0,
            issue_count=0,
            inserted_rows=inserted_rows,
            reused_rows=reused_rows,
        )
        _finish_locked_job(
            job=job,
            attempt=attempt,
            status=SalesImportJob.Status.PUBLISHED,
            counts=counts,
            using=using,
        )
        return SalesImportProcessingResult(job.pk, job.status, counts)


def _publish_staged_rows(
    *,
    claim: SalesImportClaim,
    source_system: str,
    using: str,
) -> SalesImportProcessingResult:
    with _source_session_lock(
        claim=claim,
        source_system=source_system,
        using=using,
    ):
        for attempt_number in range(2):
            try:
                return _publish_once(claim=claim, using=using)
            except IntegrityError as error:
                if (
                    attempt_number == 0
                    and _integrity_constraint_name(error)
                    == "business_data_sales_source_unique"
                ):
                    continue
                raise
    raise AssertionError("Publication retry loop did not return.")


def _current_counts(job_id, *, using: str) -> SalesImportJobCounts:
    job = SalesImportJob.objects.using(using).get(pk=job_id)
    return SalesImportJobCounts(
        total_rows=job.total_rows,
        valid_rows=job.valid_rows,
        invalid_rows=job.invalid_rows,
        issue_count=job.issue_count,
        inserted_rows=job.inserted_rows,
        reused_rows=job.reused_rows,
    )


def _complete_failure(
    claim: SalesImportClaim,
    *,
    code: str,
    message: str,
    using: str,
) -> SalesImportProcessingResult:
    counts = _current_counts(claim.job_id, using=using)
    job = complete_job(
        job_id=claim.job_id,
        lease_token=claim.lease_token,
        status=SalesImportJob.Status.FAILED,
        counts=counts,
        error_code=code,
        error_message=message,
        using=using,
    )
    return SalesImportProcessingResult(job.pk, job.status, counts)


def _complete_stored_object_failure(
    claim: SalesImportClaim,
    *,
    intake_failure_code: str,
    job_error_code: str,
    message: str,
    using: str,
) -> SalesImportProcessingResult:
    """Atomically fail an unusable object lineage and its fenced worker claim."""

    with transaction.atomic(using=using):
        sales_import = (
            SalesImport.objects.using(using)
            .select_for_update()
            .get(pk=claim.sales_import_id)
        )
        sales_import.mark_stored_object_failed(failure_code=intake_failure_code)
        return _complete_failure(
            claim,
            code=job_error_code,
            message=message,
            using=using,
        )


def _schedule_processing_retry(
    claim: SalesImportClaim,
    *,
    code: str,
    message: str,
    using: str,
) -> SalesImportProcessingResult:
    job = schedule_retry(
        job_id=claim.job_id,
        lease_token=claim.lease_token,
        error_code=code,
        error_message=message,
        using=using,
    )
    return SalesImportProcessingResult(
        job.pk,
        job.status,
        _current_counts(job.pk, using=using),
    )


def process_claim(
    claim: SalesImportClaim,
    *,
    storage=None,
    using: str = DEFAULT_DB_ALIAS,
) -> SalesImportProcessingResult:
    """Download, validate, and atomically publish one currently leased job."""

    job = (
        SalesImportJob.objects.using(using)
        .select_related("sales_import")
        .get(pk=claim.job_id)
    )
    sales_import = job.sales_import
    if sales_import.file_format != SalesImport.FileFormat.CSV:
        return _complete_failure(
            claim,
            code="FORMAT_PARSER_UNAVAILABLE",
            message="This file format does not have a row parser yet.",
            using=using,
        )

    storage = storage or get_sales_import_storage()
    try:
        advance_attempt_phase(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            phase=SalesImportAttempt.Phase.DOWNLOAD,
            using=using,
        )
        heartbeat_job(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            using=using,
        )
        download_heartbeat = _DownloadLeaseHeartbeat(claim=claim, using=using)
        with storage.open_version(
            bucket_name=sales_import.storage_bucket,
            object_key=sales_import.storage_key,
            version_id=sales_import.storage_version_id,
            size_bytes=sales_import.size_bytes,
            sha256=sales_import.sha256,
            import_id=str(sales_import.pk),
            data_contract=sales_import.data_contract,
            row_contract=sales_import.row_contract,
            expected_kms_key_arn=sales_import.storage_kms_key_arn,
            progress_callback=download_heartbeat,
        ) as binary_file:
            heartbeat_job(
                job_id=claim.job_id,
                lease_token=claim.lease_token,
                using=using,
            )
            progress, rejected = _stage_csv(
                claim=claim,
                binary_file=binary_file,
                using=using,
            )

        if rejected:
            counts = progress.counts()
            job = complete_job(
                job_id=claim.job_id,
                lease_token=claim.lease_token,
                status=SalesImportJob.Status.REJECTED,
                counts=counts,
                error_code="CONTRACT_REJECTED",
                error_message="The CSV row contract rejected the sales file.",
                using=using,
            )
            return SalesImportProcessingResult(job.pk, job.status, counts)

        heartbeat_job(
            job_id=claim.job_id,
            lease_token=claim.lease_token,
            using=using,
        )
        return _publish_staged_rows(
            claim=claim,
            source_system=sales_import.source_system,
            using=using,
        )
    except OriginalFileDownloadError as error:
        return _schedule_processing_retry(
            claim,
            code="S3_DOWNLOAD_RETRYABLE",
            message=str(error),
            using=using,
        )
    except OriginalFileKMSProvenanceError as error:
        return _complete_failure(
            claim,
            code="KMS_PROVENANCE_MISSING",
            message=str(error),
            using=using,
        )
    except OriginalFileNotFoundError as error:
        return _complete_stored_object_failure(
            claim,
            intake_failure_code=SalesImport.FailureCode.OBJECT_MISSING,
            job_error_code="S3_VERSION_NOT_FOUND",
            message=str(error),
            using=using,
        )
    except OriginalFileVerificationError as error:
        return _complete_stored_object_failure(
            claim,
            intake_failure_code=SalesImport.FailureCode.VERIFICATION_FAILED,
            job_error_code="S3_VERIFICATION_FAILED",
            message=str(error),
            using=using,
        )
    except OriginalFileStorageError as error:
        return _complete_failure(
            claim,
            code="S3_CONFIGURATION_ERROR",
            message=str(error),
            using=using,
        )
    except SalesImportClaimLost:
        raise
    except DatabaseError as error:
        logger.exception("Retryable database error while processing job %s", job.pk)
        return _schedule_processing_retry(
            claim,
            code="DATABASE_RETRYABLE",
            message="A transient database error interrupted processing.",
            using=using,
        )
