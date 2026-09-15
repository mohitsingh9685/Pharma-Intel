import re
import uuid

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Trim, Upper
from django.utils import timezone


class SalesImport(models.Model):
    """Audit record for one original sales file submitted for processing."""

    KMS_KEY_ARN_PATTERN = re.compile(
        r"^arn:[^:]+:kms:[^:]+:[0-9]{12}:key/[^/\s]+$"
    )

    class DataContract(models.TextChoices):
        SALES_V1 = "sales_v1", "Original sales-file intake v1"

    class RowContract(models.TextChoices):
        SALES_ROWS_V1 = "sales_rows_v1", "Sales rows v1"

    class FileFormat(models.TextChoices):
        CSV = "csv", "CSV"
        XLSX = "xlsx", "Excel (.xlsx)"

    class Status(models.TextChoices):
        RECEIVING = "receiving", "Receiving"
        RECEIVED = "received", "Received"
        FAILED = "failed", "Storage failed"

    class FailureCode(models.TextChoices):
        NONE = "", "None"
        STORAGE_ERROR = "storage_error", "Storage error"
        OBJECT_MISSING = "object_missing", "Stored object missing"
        VERIFICATION_FAILED = "verification_failed", "Storage verification failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    data_contract = models.CharField(
        max_length=32,
        choices=DataContract.choices,
        default=DataContract.SALES_V1,
        editable=False,
    )
    row_contract = models.CharField(
        max_length=32,
        choices=RowContract.choices,
        default=RowContract.SALES_ROWS_V1,
        editable=False,
    )
    source_system = models.CharField(
        max_length=64,
        help_text="Stable name of the system that produced this file.",
    )
    original_filename = models.CharField(max_length=255, editable=False)
    content_type = models.CharField(max_length=255, blank=True, editable=False)
    file_format = models.CharField(
        max_length=8,
        choices=FileFormat.choices,
        editable=False,
    )
    size_bytes = models.PositiveBigIntegerField(editable=False)
    sha256 = models.CharField(max_length=64, editable=False)
    storage_bucket = models.CharField(max_length=63, editable=False)
    storage_kms_key_arn = models.CharField(
        max_length=2048,
        null=True,
        blank=True,
        editable=False,
        help_text=(
            "Exact KMS key ARN recorded when the original object was stored; "
            "null only for imports created before key provenance was introduced."
        ),
    )
    storage_key = models.CharField(max_length=1024, unique=True, editable=False)
    storage_version_id = models.CharField(
        max_length=1024,
        blank=True,
        editable=False,
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sales_imports",
        editable=False,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.RECEIVING,
        db_index=True,
        editable=False,
    )
    failure_code = models.CharField(
        max_length=32,
        choices=FailureCode.choices,
        default=FailureCode.NONE,
        blank=True,
        editable=False,
    )
    received_at = models.DateTimeField(null=True, blank=True, editable=False)
    failed_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(data_contract="sales_v1"),
                name="sales_import_contract_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(row_contract="sales_rows_v1"),
                name="sales_import_row_contract_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(size_bytes__gt=0),
                name="sales_import_size_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(sha256__regex=r"^[0-9a-f]{64}$"),
                name="sales_import_sha256_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(source_system=Upper("source_system")),
                name="sales_import_source_uppercase",
            ),
            models.CheckConstraint(
                condition=models.Q(source_system=Trim("source_system")),
                name="sales_import_source_trimmed",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    source_system__regex=(
                        r"^[A-Z0-9]([A-Z0-9 ._:/-]*[A-Z0-9])?$"
                    )
                ),
                name="sales_import_source_valid",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(original_filename__regex=r"^\s*$")
                    & models.Q(original_filename=Trim("original_filename"))
                ),
                name="sales_import_filename_valid",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(storage_bucket="")
                    & ~models.Q(storage_key="")
                ),
                name="sales_import_storage_location_set",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(storage_kms_key_arn__isnull=True)
                    | (
                        models.Q(
                            storage_kms_key_arn=Trim("storage_kms_key_arn")
                        )
                        & models.Q(
                            storage_kms_key_arn__regex=(
                                r"^arn:[^:]+:kms:[^:]+:[0-9]{12}:key/.+$"
                            )
                        )
                    )
                ),
                name="sales_import_kms_key_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(file_format__in=("csv", "xlsx")),
                name="sales_import_format_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    failure_code__in=(
                        "",
                        "storage_error",
                        "object_missing",
                        "verification_failed",
                    )
                ),
                name="sales_import_failure_code_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="receiving",
                        received_at__isnull=True,
                        failed_at__isnull=True,
                        storage_version_id="",
                        failure_code="",
                    )
                    | (
                        models.Q(
                            status="received",
                            received_at__isnull=False,
                            failed_at__isnull=True,
                            failure_code="",
                        )
                        & ~models.Q(storage_version_id="")
                    )
                    | (
                        models.Q(
                            status="failed",
                            failed_at__isnull=False,
                        )
                        & ~models.Q(failure_code="")
                        & (
                            models.Q(
                                received_at__isnull=True,
                                storage_version_id="",
                            )
                            | (
                                models.Q(
                                    received_at__isnull=False,
                                    failure_code__in=(
                                        "object_missing",
                                        "verification_failed",
                                    ),
                                )
                                & ~models.Q(storage_version_id="")
                            )
                        )
                    )
                ),
                name="sales_import_status_metadata_consistent",
            ),
            models.UniqueConstraint(
                models.F("data_contract"),
                models.F("row_contract"),
                models.F("source_system"),
                models.F("sha256"),
                condition=models.Q(status__in=("receiving", "received")),
                name="sales_import_active_file_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=("source_system", "-created_at"),
                name="salesimp_source_created_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        self.source_system = self.source_system.strip().upper()
        if not self._state.adding:
            raise ValidationError(
                "Sales import records change only through controlled status transitions."
            )
        self.storage_kms_key_arn = str(self.storage_kms_key_arn or "").strip()
        if not self.storage_kms_key_arn:
            raise ValidationError("A sales import must record its KMS key ARN.")
        return super().save(*args, **kwargs)

    def mark_received(self, *, version_id: str) -> None:
        version_id = version_id.strip()
        if not version_id:
            raise ValueError("A stored object version is required.")
        now = timezone.now()
        using = self._state.db or "default"
        updated = type(self).objects.using(using).filter(
            pk=self.pk,
            status=self.Status.RECEIVING,
        ).update(
            status=self.Status.RECEIVED,
            storage_version_id=version_id,
            received_at=now,
            failure_code=self.FailureCode.NONE,
            failed_at=None,
            updated_at=now,
        )
        if updated != 1:
            raise RuntimeError("The import is no longer awaiting storage completion.")
        self.refresh_from_db(using=using)

    def mark_failed(self, *, failure_code: str) -> None:
        if failure_code not in self.FailureCode.values or not failure_code:
            raise ValueError("A recognized failure code is required.")
        now = timezone.now()
        using = self._state.db or "default"
        updated = type(self).objects.using(using).filter(
            pk=self.pk,
            status=self.Status.RECEIVING,
        ).update(
            status=self.Status.FAILED,
            storage_version_id="",
            received_at=None,
            failure_code=failure_code,
            failed_at=now,
            updated_at=now,
        )
        if updated != 1:
            raise RuntimeError("The import is no longer awaiting storage completion.")
        self.refresh_from_db(using=using)

    def mark_stored_object_failed(self, *, failure_code: str) -> None:
        """Record that a previously verified exact object version is unusable."""

        if failure_code not in (
            self.FailureCode.OBJECT_MISSING,
            self.FailureCode.VERIFICATION_FAILED,
        ):
            raise ValueError("A permanent stored-object failure code is required.")
        now = timezone.now()
        using = self._state.db or "default"
        updated = type(self).objects.using(using).filter(
            pk=self.pk,
            status=self.Status.RECEIVED,
        ).update(
            status=self.Status.FAILED,
            failure_code=failure_code,
            failed_at=now,
            updated_at=now,
        )
        if updated != 1:
            raise RuntimeError("The received import can no longer be failed.")
        self.refresh_from_db(using=using)

    def record_legacy_kms_key_arn(self, *, kms_key_arn: str) -> None:
        """Fill one missing historical KMS key after exact-version verification."""

        kms_key_arn = str(kms_key_arn or "").strip()
        if self.KMS_KEY_ARN_PATTERN.fullmatch(kms_key_arn) is None:
            raise ValueError("A concrete KMS key ARN is required.")
        now = timezone.now()
        using = self._state.db or "default"
        candidates = type(self).objects.using(using).filter(
            pk=self.pk,
            storage_kms_key_arn__isnull=True,
        ).filter(
            models.Q(
                status=self.Status.RECEIVING,
                received_at__isnull=True,
                storage_version_id="",
            )
            | (
                models.Q(
                    status__in=(self.Status.RECEIVED, self.Status.FAILED),
                    received_at__isnull=False,
                )
                & ~models.Q(storage_version_id="")
            )
        )
        updated = candidates.update(
            storage_kms_key_arn=kms_key_arn,
            updated_at=now,
        )
        if updated != 1:
            raise RuntimeError(
                "KMS provenance can be recorded only once for a legacy import "
                "with verifiable storage evidence."
            )
        self.refresh_from_db(using=using)

    def delete(self, *args, **kwargs):
        raise ValidationError("Sales import audit records cannot be deleted.")

    def __str__(self):
        return f"{self.original_filename} — {self.get_status_display()}"


class SalesImportJob(models.Model):
    """Durable processing state kept separate from immutable file intake."""

    class Status(models.TextChoices):
        WAITING_FOR_FILE = "waiting_file", "Waiting for file"
        AWAITING_PARSER = "awaiting_parser", "Awaiting format parser"
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        RETRY_WAIT = "retry_wait", "Waiting to retry"
        PUBLISHED = "published", "Published"
        REJECTED = "rejected", "Rejected"
        FAILED = "failed", "Processing failed"
        CANCELLED = "cancelled", "Cancelled"

    TERMINAL_STATUSES = frozenset(
        {Status.PUBLISHED, Status.REJECTED, Status.FAILED, Status.CANCELLED}
    )

    sales_import = models.OneToOneField(
        SalesImport,
        primary_key=True,
        on_delete=models.PROTECT,
        related_name="processing_job",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.WAITING_FOR_FILE,
        db_index=True,
        editable=False,
    )
    available_at = models.DateTimeField(default=timezone.now, editable=False)
    attempt_count = models.PositiveIntegerField(default=0, editable=False)
    max_attempts = models.PositiveSmallIntegerField(default=5, editable=False)
    lease_token = models.UUIDField(null=True, blank=True, editable=False)
    leased_by = models.CharField(max_length=255, blank=True, editable=False)
    lease_expires_at = models.DateTimeField(null=True, blank=True, editable=False)
    heartbeat_at = models.DateTimeField(null=True, blank=True, editable=False)
    first_started_at = models.DateTimeField(null=True, blank=True, editable=False)
    finished_at = models.DateTimeField(null=True, blank=True, editable=False)
    total_rows = models.PositiveBigIntegerField(default=0, editable=False)
    valid_rows = models.PositiveBigIntegerField(default=0, editable=False)
    invalid_rows = models.PositiveBigIntegerField(default=0, editable=False)
    issue_count = models.PositiveBigIntegerField(default=0, editable=False)
    inserted_rows = models.PositiveBigIntegerField(default=0, editable=False)
    reused_rows = models.PositiveBigIntegerField(default=0, editable=False)
    last_error_code = models.CharField(max_length=64, blank=True, editable=False)
    last_error_message = models.CharField(
        max_length=1000,
        blank=True,
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=(
                        "waiting_file",
                        "awaiting_parser",
                        "queued",
                        "processing",
                        "retry_wait",
                        "published",
                        "rejected",
                        "failed",
                        "cancelled",
                    )
                ),
                name="sales_job_status_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(max_attempts__gt=0)
                    & models.Q(attempt_count__lte=models.F("max_attempts"))
                ),
                name="sales_job_attempts_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        total_rows=(
                            models.F("valid_rows") + models.F("invalid_rows")
                        )
                    )
                    & models.Q(issue_count__gte=models.F("invalid_rows"))
                ),
                name="sales_job_counts_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="processing",
                        lease_token__isnull=False,
                        lease_expires_at__isnull=False,
                        heartbeat_at__isnull=False,
                        first_started_at__isnull=False,
                        finished_at__isnull=True,
                    )
                    & ~models.Q(leased_by="")
                    | models.Q(
                        ~models.Q(status="processing"),
                        lease_token__isnull=True,
                        leased_by="",
                        lease_expires_at__isnull=True,
                        heartbeat_at__isnull=True,
                    )
                ),
                name="sales_job_lease_consistent",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=("published", "rejected", "failed", "cancelled"),
                        finished_at__isnull=False,
                    )
                    | models.Q(
                        status__in=(
                            "waiting_file",
                            "awaiting_parser",
                            "queued",
                            "processing",
                            "retry_wait",
                        ),
                        finished_at__isnull=True,
                    )
                ),
                name="sales_job_finished_consistent",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="published",
                        invalid_rows=0,
                        issue_count=0,
                        total_rows=models.F("valid_rows"),
                        valid_rows=(
                            models.F("inserted_rows") + models.F("reused_rows")
                        ),
                    )
                    | (
                        ~models.Q(status="published")
                        & models.Q(inserted_rows=0, reused_rows=0)
                    )
                ),
                name="sales_job_publish_counts_valid",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status="rejected")
                    | models.Q(status="rejected", issue_count__gt=0)
                ),
                name="sales_job_rejection_has_issue",
            ),
            models.CheckConstraint(
                condition=models.Q(leased_by=Trim("leased_by")),
                name="sales_job_worker_trimmed",
            ),
        ]
        indexes = [
            models.Index(
                fields=("status", "available_at", "created_at"),
                condition=models.Q(status__in=("queued", "retry_wait")),
                name="sales_job_claim_idx",
            ),
            models.Index(
                fields=("lease_expires_at",),
                condition=models.Q(status="processing"),
                name="sales_job_expired_lease_idx",
            ),
        ]

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL_STATUSES

    def __str__(self):
        return f"{self.sales_import_id} — {self.get_status_display()}"


class SalesImportAttempt(models.Model):
    """Immutable history around each leased execution of an import job."""

    class Phase(models.TextChoices):
        CLAIMED = "claimed", "Claimed"
        DOWNLOAD = "download", "Download"
        PARSE = "parse", "Parse"
        VALIDATE = "validate", "Validate"
        PUBLISH = "publish", "Publish"
        COMPLETE = "complete", "Complete"

    class Outcome(models.TextChoices):
        RUNNING = "running", "Running"
        PUBLISHED = "published", "Published"
        REJECTED = "rejected", "Rejected"
        RETRYABLE_FAILURE = "retryable_failure", "Retryable failure"
        PERMANENT_FAILURE = "permanent_failure", "Permanent failure"
        LEASE_EXPIRED = "lease_expired", "Lease expired"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        SalesImportJob,
        on_delete=models.PROTECT,
        related_name="attempts",
        editable=False,
    )
    attempt_number = models.PositiveIntegerField(editable=False)
    lease_token = models.UUIDField(editable=False)
    worker_id = models.CharField(max_length=255, editable=False)
    phase = models.CharField(
        max_length=16,
        choices=Phase.choices,
        default=Phase.CLAIMED,
        editable=False,
    )
    outcome = models.CharField(
        max_length=24,
        choices=Outcome.choices,
        default=Outcome.RUNNING,
        db_index=True,
        editable=False,
    )
    started_at = models.DateTimeField(default=timezone.now, editable=False)
    heartbeat_at = models.DateTimeField(default=timezone.now, editable=False)
    finished_at = models.DateTimeField(null=True, blank=True, editable=False)
    total_rows = models.PositiveBigIntegerField(default=0, editable=False)
    valid_rows = models.PositiveBigIntegerField(default=0, editable=False)
    invalid_rows = models.PositiveBigIntegerField(default=0, editable=False)
    issue_count = models.PositiveBigIntegerField(default=0, editable=False)
    inserted_rows = models.PositiveBigIntegerField(default=0, editable=False)
    reused_rows = models.PositiveBigIntegerField(default=0, editable=False)
    error_code = models.CharField(max_length=64, blank=True, editable=False)
    error_message = models.CharField(max_length=1000, blank=True, editable=False)

    class Meta:
        ordering = ("job_id", "-attempt_number")
        constraints = [
            models.UniqueConstraint(
                fields=("job", "attempt_number"),
                name="sales_attempt_number_unique",
            ),
            models.UniqueConstraint(
                fields=("job",),
                condition=models.Q(outcome="running"),
                name="sales_attempt_one_running",
            ),
            models.CheckConstraint(
                condition=models.Q(attempt_number__gt=0),
                name="sales_attempt_number_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(worker_id=Trim("worker_id")) & ~models.Q(worker_id=""),
                name="sales_attempt_worker_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    phase__in=(
                        "claimed",
                        "download",
                        "parse",
                        "validate",
                        "publish",
                        "complete",
                    )
                ),
                name="sales_attempt_phase_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    outcome__in=(
                        "running",
                        "published",
                        "rejected",
                        "retryable_failure",
                        "permanent_failure",
                        "lease_expired",
                        "cancelled",
                    )
                ),
                name="sales_attempt_outcome_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(outcome="running", finished_at__isnull=True)
                    | (
                        ~models.Q(outcome="running")
                        & models.Q(finished_at__isnull=False)
                    )
                ),
                name="sales_attempt_finished_consistent",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        total_rows=(
                            models.F("valid_rows") + models.F("invalid_rows")
                        )
                    )
                    & models.Q(issue_count__gte=models.F("invalid_rows"))
                ),
                name="sales_attempt_counts_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        outcome="published",
                        invalid_rows=0,
                        issue_count=0,
                        total_rows=models.F("valid_rows"),
                        valid_rows=(
                            models.F("inserted_rows") + models.F("reused_rows")
                        ),
                    )
                    | (
                        ~models.Q(outcome="published")
                        & models.Q(inserted_rows=0, reused_rows=0)
                    )
                ),
                name="sales_attempt_publish_counts_valid",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(outcome="rejected")
                    | models.Q(outcome="rejected", issue_count__gt=0)
                ),
                name="sales_attempt_rejection_has_issue",
            ),
        ]
        indexes = [
            models.Index(
                fields=("job", "-attempt_number"),
                name="sales_attempt_job_number_idx",
            ),
            models.Index(
                fields=("outcome", "started_at"),
                name="sales_attempt_outcome_idx",
            ),
        ]

    def __str__(self):
        return f"{self.job_id} attempt {self.attempt_number}"


class SalesImportStagedRow(models.Model):
    """One syntactically valid, normalized row awaiting atomic publication."""

    class Outcome(models.TextChoices):
        STAGED = "staged", "Staged"
        PUBLISHED = "published", "Published"
        REUSED = "reused", "Reused existing fact"

    attempt = models.ForeignKey(
        SalesImportAttempt,
        on_delete=models.PROTECT,
        related_name="staged_rows",
        editable=False,
    )
    row_number = models.PositiveBigIntegerField(editable=False)
    source_record_id = models.CharField(max_length=255, editable=False)
    transaction_date = models.DateField(editable=False)
    product_code = models.CharField(max_length=64, editable=False)
    territory_code = models.CharField(max_length=64, editable=False)
    hospital_code = models.CharField(max_length=64, blank=True, editable=False)
    sales_representative_code = models.CharField(
        max_length=64,
        blank=True,
        editable=False,
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=3, editable=False)
    revenue_amount = models.DecimalField(
        max_digits=20,
        decimal_places=4,
        editable=False,
    )
    currency_code = models.CharField(max_length=3, editable=False)
    calendar_date = models.ForeignKey(
        "business_data.CalendarDate",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="staged_sales_import_rows",
        db_index=False,
        editable=False,
    )
    product = models.ForeignKey(
        "master_data.Product",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="staged_sales_import_rows",
        db_index=False,
        editable=False,
    )
    territory = models.ForeignKey(
        "master_data.Territory",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="staged_sales_import_rows",
        db_index=False,
        editable=False,
    )
    hospital = models.ForeignKey(
        "master_data.Hospital",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="staged_sales_import_rows",
        db_index=False,
        editable=False,
    )
    sales_representative = models.ForeignKey(
        "master_data.SalesRepresentative",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="staged_sales_import_rows",
        db_index=False,
        editable=False,
    )
    outcome = models.CharField(
        max_length=12,
        choices=Outcome.choices,
        default=Outcome.STAGED,
        editable=False,
    )
    sales_transaction = models.ForeignKey(
        "business_data.SalesTransaction",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="sales_import_rows",
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True, editable=False)

    class Meta:
        ordering = ("attempt_id", "row_number")
        constraints = [
            models.UniqueConstraint(
                fields=("attempt", "row_number"),
                name="sales_staged_row_number_unique",
            ),
            models.UniqueConstraint(
                fields=("attempt", "source_record_id"),
                name="sales_staged_source_id_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(row_number__gte=2),
                name="sales_staged_row_number_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(source_record_id=Trim("source_record_id"))
                    & ~models.Q(source_record_id="")
                ),
                name="sales_staged_source_id_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(product_code=Upper("product_code"))
                    & models.Q(product_code=Trim("product_code"))
                    & ~models.Q(product_code="")
                    & models.Q(territory_code=Upper("territory_code"))
                    & models.Q(territory_code=Trim("territory_code"))
                    & ~models.Q(territory_code="")
                ),
                name="sales_staged_required_codes_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(hospital_code=Upper("hospital_code"))
                    & models.Q(hospital_code=Trim("hospital_code"))
                    & models.Q(
                        sales_representative_code=Upper(
                            "sales_representative_code"
                        )
                    )
                    & models.Q(
                        sales_representative_code=Trim(
                            "sales_representative_code"
                        )
                    )
                ),
                name="sales_staged_optional_codes_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(currency_code__regex=r"^[A-Z]{3}$"),
                name="sales_staged_currency_valid",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(quantity=0)
                    & (
                        models.Q(quantity__gt=Decimal("0"), revenue_amount__gte=0)
                        | models.Q(
                            quantity__lt=Decimal("0"),
                            revenue_amount__lte=0,
                        )
                    )
                ),
                name="sales_staged_measures_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(outcome__in=("staged", "published", "reused")),
                name="sales_staged_outcome_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(outcome="staged", sales_transaction__isnull=True)
                    | (
                        models.Q(
                            outcome__in=("published", "reused"),
                            sales_transaction__isnull=False,
                            calendar_date__isnull=False,
                            product__isnull=False,
                            territory__isnull=False,
                        )
                        & (
                            models.Q(hospital_code="", hospital__isnull=True)
                            | (
                                ~models.Q(hospital_code="")
                                & models.Q(hospital__isnull=False)
                            )
                        )
                        & (
                            models.Q(
                                sales_representative_code="",
                                sales_representative__isnull=True,
                            )
                            | (
                                ~models.Q(sales_representative_code="")
                                & models.Q(sales_representative__isnull=False)
                            )
                        )
                    )
                ),
                name="sales_staged_result_consistent",
            ),
        ]
        indexes = [
            models.Index(
                fields=("attempt", "source_record_id"),
                name="sales_staged_source_idx",
            ),
            models.Index(
                fields=("attempt", "transaction_date"),
                name="sales_staged_date_idx",
            ),
        ]

    def __str__(self):
        return f"{self.attempt_id} row {self.row_number}"


class SalesImportIssue(models.Model):
    """Stable, reviewable validation issue produced by one attempt."""

    class Phase(models.TextChoices):
        CONTRACT = "contract", "File contract"
        MASTER_DATA = "master_data", "Master data"
        IDEMPOTENCY = "idempotency", "Idempotency"

    attempt = models.ForeignKey(
        SalesImportAttempt,
        on_delete=models.PROTECT,
        related_name="issues",
        editable=False,
    )
    row_number = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    column = models.CharField(max_length=64, editable=False)
    code = models.CharField(max_length=64, editable=False)
    message = models.CharField(max_length=1000, editable=False)
    phase = models.CharField(max_length=16, choices=Phase.choices, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, editable=False)

    class Meta:
        ordering = ("attempt_id", "row_number", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("attempt", "row_number", "column", "code"),
                nulls_distinct=False,
                name="sales_issue_identity_unique",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(row_number__isnull=True)
                    | models.Q(row_number__gte=2)
                ),
                name="sales_issue_row_number_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    phase__in=("contract", "master_data", "idempotency")
                ),
                name="sales_issue_phase_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(column=Trim("column"))
                    & ~models.Q(column="")
                    & models.Q(code__regex=r"^[A-Z][A-Z0-9_]*$")
                    & models.Q(message=Trim("message"))
                    & ~models.Q(message="")
                ),
                name="sales_issue_text_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=("attempt", "row_number", "id"),
                name="sales_issue_row_idx",
            ),
            models.Index(
                fields=("attempt", "code"),
                name="sales_issue_code_idx",
            ),
        ]

    def __str__(self):
        location = "file" if self.row_number is None else f"row {self.row_number}"
        return f"{self.code} at {location}"
