import io
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

from .models import SalesImport as CurrentSalesImport
from .models import SalesImportJob as CurrentSalesImportJob
from .storage import DiscoveredKMSProvenance


TEST_KMS_KEY_ARN = "arn:aws:kms:ap-south-1:123456789012:key/legacy-import-key"


class ProcessingPersistenceMigrationTests(TransactionTestCase):
    """Verify processing and KMS migrations preserve legacy intake state."""

    migrate_from = [("sales_imports", "0003_sales_row_contract")]
    migrate_to = [("sales_imports", "0007_harden_kms_provenance")]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        self.addCleanup(self._restore_latest_migrations)

        old_apps = executor.loader.project_state(self.migrate_from).apps
        self.import_ids = self._create_legacy_imports(old_apps)

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.migrated_apps = executor.loader.project_state(self.migrate_to).apps

    @staticmethod
    def _create_legacy_imports(apps):
        User = apps.get_model("accounts", "User")
        SalesImport = apps.get_model("sales_imports", "SalesImport")
        uploaded_by = User.objects.create(
            email="migration-test@example.com",
            password="unusable-in-migration-test",
        )
        now = timezone.now()

        common = {
            "size_bytes": 128,
            "storage_bucket": "private-sales-imports",
            "uploaded_by_id": uploaded_by.pk,
        }
        imports = {
            "received_csv": SalesImport.objects.create(
                **common,
                content_type="text/csv",
                source_system="LEGACY_CSV",
                original_filename="legacy.csv",
                file_format="csv",
                sha256="1" * 64,
                storage_key="sales-imports/legacy/received.csv",
                storage_version_id="csv-version",
                status="received",
                received_at=now,
            ),
            "received_xlsx": SalesImport.objects.create(
                **common,
                content_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                source_system="LEGACY_XLSX",
                original_filename="legacy.xlsx",
                file_format="xlsx",
                sha256="2" * 64,
                storage_key="sales-imports/legacy/received.xlsx",
                storage_version_id="xlsx-version",
                status="received",
                received_at=now,
            ),
            "receiving": SalesImport.objects.create(
                **common,
                content_type="text/csv",
                source_system="LEGACY_RECEIVING",
                original_filename="receiving.csv",
                file_format="csv",
                sha256="3" * 64,
                storage_key="sales-imports/legacy/receiving.csv",
                status="receiving",
            ),
            "failed": SalesImport.objects.create(
                **common,
                content_type="text/csv",
                source_system="LEGACY_FAILED",
                original_filename="failed.csv",
                file_format="csv",
                sha256="4" * 64,
                storage_key="sales-imports/legacy/failed.csv",
                status="failed",
                failure_code="storage_error",
                failed_at=now,
            ),
        }
        return {name: sales_import.pk for name, sales_import in imports.items()}

    @staticmethod
    def _restore_latest_migrations():
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_legacy_intakes_receive_the_correct_processing_job_status(self):
        SalesImport = self.migrated_apps.get_model("sales_imports", "SalesImport")
        SalesImportJob = self.migrated_apps.get_model(
            "sales_imports", "SalesImportJob"
        )
        expected_statuses = {
            "received_csv": "queued",
            "received_xlsx": "awaiting_parser",
            "receiving": "waiting_file",
            "failed": "failed",
        }

        self.assertEqual(SalesImportJob.objects.count(), len(expected_statuses))
        for name, expected_status in expected_statuses.items():
            with self.subTest(legacy_state=name):
                job = SalesImportJob.objects.get(
                    sales_import_id=self.import_ids[name]
                )
                self.assertEqual(job.status, expected_status)
                self.assertIsNone(
                    SalesImport.objects.get(pk=self.import_ids[name]).storage_kms_key_arn
                )

        failed_job = SalesImportJob.objects.get(
            sales_import_id=self.import_ids["failed"]
        )
        self.assertIsNotNone(failed_job.finished_at)
        self.assertEqual(failed_job.last_error_code, "INTAKE_STORAGE_FAILED")

    def test_new_raw_insert_cannot_omit_kms_provenance(self):
        uploaded_by = get_user_model().objects.get(
            email="migration-test@example.com"
        )
        import_id = uuid.uuid4()

        with self.assertRaises(IntegrityError), transaction.atomic():
            CurrentSalesImport.objects.bulk_create(
                [
                    CurrentSalesImport(
                        id=import_id,
                        source_system="NEW_RAW_IMPORT",
                        original_filename="new.csv",
                        content_type="text/csv",
                        file_format=CurrentSalesImport.FileFormat.CSV,
                        size_bytes=10,
                        sha256="9" * 64,
                        storage_bucket="private-sales-imports",
                        storage_kms_key_arn=None,
                        storage_key=f"sales-imports/new/{import_id}.csv",
                        uploaded_by=uploaded_by,
                    )
                ]
            )

    def test_command_recovers_unfinished_legacy_receiving_import(self):
        sales_import = CurrentSalesImport.objects.get(
            pk=self.import_ids["receiving"]
        )

        class LegacyStorage:
            def __init__(self):
                self.calls = []

            def discover_kms_provenance(self, **kwargs):
                self.calls.append(kwargs)
                return DiscoveredKMSProvenance(
                    kms_key_arn=TEST_KMS_KEY_ARN,
                    version_id="recovered-exact-version",
                )

        storage = LegacyStorage()
        with patch(
            "sales_imports.management.commands.backfill_sales_import_kms."
            "get_sales_import_storage",
            return_value=storage,
        ):
            call_command(
                "backfill_sales_import_kms",
                str(sales_import.pk),
                stdout=io.StringIO(),
            )

        sales_import.refresh_from_db()
        job = CurrentSalesImportJob.objects.get(pk=sales_import.pk)
        self.assertEqual(sales_import.storage_kms_key_arn, TEST_KMS_KEY_ARN)
        self.assertEqual(sales_import.status, CurrentSalesImport.Status.RECEIVED)
        self.assertEqual(
            sales_import.storage_version_id,
            "recovered-exact-version",
        )
        self.assertEqual(job.status, CurrentSalesImportJob.Status.QUEUED)
        self.assertIsNone(storage.calls[0]["version_id"])

        with self.assertRaises(IntegrityError), transaction.atomic():
            CurrentSalesImport.objects.filter(pk=sales_import.pk).update(
                storage_kms_key_arn=(
                    "arn:aws:kms:ap-south-1:123456789012:key/replacement"
                )
            )
