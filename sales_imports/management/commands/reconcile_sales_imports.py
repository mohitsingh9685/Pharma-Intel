from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from sales_imports.models import SalesImport
from sales_imports.queue import enqueue_import
from sales_imports.storage import (
    OriginalFileNotFoundError,
    OriginalFileStorageError,
    OriginalFileVerificationError,
    get_sales_import_storage,
)


class Command(BaseCommand):
    help = "Recover sales imports left in receiving after an interrupted upload."

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than-minutes",
            type=int,
            default=settings.SALES_IMPORT_RECONCILE_AFTER_MINUTES,
            help="Only inspect receiving rows older than this grace period.",
        )

    def handle(self, *args, **options):
        grace_minutes = options["older_than_minutes"]
        if grace_minutes < 1:
            raise CommandError("--older-than-minutes must be at least 1.")

        try:
            storage = get_sales_import_storage()
        except OriginalFileStorageError as error:
            raise CommandError(str(error)) from error

        cutoff = timezone.now() - timedelta(minutes=grace_minutes)
        imports = SalesImport.objects.filter(
            status=SalesImport.Status.RECEIVING,
            created_at__lt=cutoff,
        ).order_by("created_at")

        received = failed = deferred = 0
        for sales_import in imports.iterator(chunk_size=100):
            try:
                stored = storage.verify(
                    bucket_name=sales_import.storage_bucket,
                    object_key=sales_import.storage_key,
                    version_id=None,
                    size_bytes=sales_import.size_bytes,
                    sha256=sales_import.sha256,
                    import_id=str(sales_import.pk),
                    data_contract=sales_import.data_contract,
                    row_contract=sales_import.row_contract,
                    expected_kms_key_arn=sales_import.storage_kms_key_arn,
                )
            except OriginalFileNotFoundError:
                if self._mark_failed(
                    sales_import,
                    SalesImport.FailureCode.OBJECT_MISSING,
                ):
                    failed += 1
            except OriginalFileVerificationError:
                if self._mark_failed(
                    sales_import,
                    SalesImport.FailureCode.VERIFICATION_FAILED,
                ):
                    failed += 1
            except OriginalFileStorageError:
                deferred += 1
            else:
                try:
                    with transaction.atomic():
                        sales_import.mark_received(version_id=stored.version_id)
                        enqueue_import(sales_import)
                except RuntimeError:
                    continue
                received += 1

        summary = (
            f"Reconciled imports: {received} received, {failed} failed, "
            f"{deferred} deferred for retry."
        )
        if deferred:
            raise CommandError(
                f"{summary} S3 could not be reached; run reconciliation again "
                "after storage recovers."
            )
        self.stdout.write(self.style.SUCCESS(summary))

    @staticmethod
    def _mark_failed(sales_import, failure_code):
        try:
            with transaction.atomic():
                sales_import.mark_failed(failure_code=failure_code)
                enqueue_import(sales_import)
        except RuntimeError:
            return False
        return True
