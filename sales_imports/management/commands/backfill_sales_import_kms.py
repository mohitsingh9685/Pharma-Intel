import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from sales_imports.models import SalesImport
from sales_imports.queue import enqueue_import
from sales_imports.storage import OriginalFileStorageError, get_sales_import_storage


class Command(BaseCommand):
    help = (
        "Verify one legacy import's exact S3 version and record its historical "
        "KMS key ARN."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "import_id",
            type=uuid.UUID,
            help="UUID of one legacy received import with missing KMS provenance.",
        )

    def handle(self, *args, **options):
        import_id = options["import_id"]
        try:
            sales_import = SalesImport.objects.get(pk=import_id)
        except SalesImport.DoesNotExist as error:
            raise CommandError(f"No sales import exists for {import_id}.") from error

        if sales_import.storage_kms_key_arn:
            raise CommandError("This sales import already records its KMS key ARN.")
        has_received_evidence = (
            sales_import.received_at is not None
            and bool(sales_import.storage_version_id)
        )
        is_unfinished_receiving = (
            sales_import.status == SalesImport.Status.RECEIVING
            and sales_import.received_at is None
            and not sales_import.storage_version_id
        )
        if not is_unfinished_receiving and not (
            sales_import.status
            in (SalesImport.Status.RECEIVED, SalesImport.Status.FAILED)
            and has_received_evidence
        ):
            raise CommandError(
                "This legacy import has no recoverable stored-object evidence."
            )

        try:
            storage = get_sales_import_storage()
            provenance = storage.discover_kms_provenance(
                bucket_name=sales_import.storage_bucket,
                object_key=sales_import.storage_key,
                version_id=sales_import.storage_version_id or None,
                size_bytes=sales_import.size_bytes,
                sha256=sales_import.sha256,
                import_id=str(sales_import.pk),
                data_contract=sales_import.data_contract,
                row_contract=sales_import.row_contract,
            )
        except OriginalFileStorageError as error:
            raise CommandError(str(error)) from error

        try:
            with transaction.atomic():
                locked_import = SalesImport.objects.select_for_update().get(
                    pk=sales_import.pk
                )
                locked_import.record_legacy_kms_key_arn(
                    kms_key_arn=provenance.kms_key_arn
                )
                if locked_import.status == SalesImport.Status.RECEIVING:
                    locked_import.mark_received(version_id=provenance.version_id)
                    enqueue_import(locked_import)
        except RuntimeError as error:
            raise CommandError(str(error)) from error

        self.stdout.write(
            self.style.SUCCESS(
                f"Recorded exact-version KMS provenance for sales import "
                f"{sales_import.pk} using object version {provenance.version_id}."
            )
        )
