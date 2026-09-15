import logging
import uuid
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import SalesImport
from .storage import OriginalFileStorageError, get_sales_import_storage
from .validation import InspectedSalesFile, normalized_filename


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SalesImportIntakeResult:
    sales_import: SalesImport
    created: bool


def _object_key(*, import_id: uuid.UUID, file_format: str) -> str:
    today = timezone.localdate()
    return (
        f"sales-imports/original/{today:%Y/%m}/"
        f"{import_id}.{file_format}"
    )


def _existing_active_import(*, source_system: str, sha256: str):
    return SalesImport.objects.filter(
        data_contract=SalesImport.DataContract.SALES_V1,
        row_contract=SalesImport.RowContract.SALES_ROWS_V1,
        source_system=source_system,
        sha256=sha256,
        status__in=(SalesImport.Status.RECEIVING, SalesImport.Status.RECEIVED),
    ).first()


def receive_sales_import(
    *,
    uploaded_file,
    inspection: InspectedSalesFile,
    source_system: str,
    uploaded_by,
    storage=None,
) -> SalesImportIntakeResult:
    """Create an auditable intake and store its original file outside a DB lock."""
    source_system = source_system.strip().upper()
    existing = _existing_active_import(
        source_system=source_system,
        sha256=inspection.sha256,
    )
    if existing is not None:
        return SalesImportIntakeResult(sales_import=existing, created=False)

    storage = storage or get_sales_import_storage()

    import_id = uuid.uuid4()
    object_key = _object_key(
        import_id=import_id,
        file_format=inspection.file_format,
    )
    try:
        with transaction.atomic():
            sales_import = SalesImport.objects.create(
                id=import_id,
                source_system=source_system,
                original_filename=normalized_filename(uploaded_file),
                content_type=str(getattr(uploaded_file, "content_type", ""))[:255],
                file_format=inspection.file_format,
                size_bytes=inspection.size_bytes,
                sha256=inspection.sha256,
                storage_bucket=storage.bucket_name,
                storage_key=object_key,
                uploaded_by=uploaded_by,
            )
    except IntegrityError:
        existing = _existing_active_import(
            source_system=source_system,
            sha256=inspection.sha256,
        )
        if existing is None:
            raise
        return SalesImportIntakeResult(sales_import=existing, created=False)

    try:
        stored = storage.store(
            uploaded_file=uploaded_file,
            object_key=object_key,
            size_bytes=inspection.size_bytes,
            sha256=inspection.sha256,
            content_type=sales_import.content_type,
            import_id=str(import_id),
            data_contract=sales_import.data_contract,
            row_contract=sales_import.row_contract,
        )
    except OriginalFileStorageError:
        logger.exception(
            "Sales import %s is awaiting storage reconciliation",
            import_id,
        )
    else:
        sales_import.mark_received(version_id=stored.version_id)

    return SalesImportIntakeResult(sales_import=sales_import, created=True)
