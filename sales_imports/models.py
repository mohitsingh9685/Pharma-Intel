import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Trim, Upper
from django.utils import timezone


class SalesImport(models.Model):
    """Audit record for one original sales file submitted for processing."""

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
                            received_at__isnull=True,
                            failed_at__isnull=False,
                            storage_version_id="",
                        )
                        & ~models.Q(failure_code="")
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
        return super().save(*args, **kwargs)

    def mark_received(self, *, version_id: str) -> None:
        version_id = version_id.strip()
        if not version_id:
            raise ValueError("A stored object version is required.")
        now = timezone.now()
        updated = type(self).objects.filter(
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
        self.refresh_from_db()

    def mark_failed(self, *, failure_code: str) -> None:
        if failure_code not in self.FailureCode.values or not failure_code:
            raise ValueError("A recognized failure code is required.")
        now = timezone.now()
        updated = type(self).objects.filter(
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
        self.refresh_from_db()

    def delete(self, *args, **kwargs):
        raise ValidationError("Sales import audit records cannot be deleted.")

    def __str__(self):
        return f"{self.original_filename} — {self.get_status_display()}"
