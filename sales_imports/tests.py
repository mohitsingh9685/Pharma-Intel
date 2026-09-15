import base64
import hashlib
import io
import zipfile
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import SalesImportUploadForm
from .models import SalesImport
from .services import receive_sales_import
from .storage import (
    OriginalFileNotFoundError,
    OriginalFileStorageError,
    OriginalFileUploadError,
    OriginalFileVerificationError,
    S3OriginalFileStorage,
    StoredOriginalFile,
)
from .validation import inspect_sales_file


TEST_KMS_KEY_ARN = "arn:aws:kms:ap-south-1:123456789012:key/sales-import-test"


def csv_upload(
    *,
    name="sales.csv",
    content=b"transaction_id,amount\nTX-001,100\n",
):
    return SimpleUploadedFile(name, content, content_type="text/csv")


def xlsx_upload(*, extra_members=None):
    workbook = io.BytesIO()
    with zipfile.ZipFile(workbook, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("xl/workbook.xml", "<workbook />")
        for name, content in extra_members or ():
            archive.writestr(name, content)
    return SimpleUploadedFile(
        "sales.xlsx",
        workbook.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )


class RecordingOriginalFileStorage:
    bucket_name = "private-sales-imports"
    kms_key_arn = TEST_KMS_KEY_ARN

    def __init__(self, *, version_id="version-1", error=None):
        self.version_id = version_id
        self.error = error
        self.calls = []

    def store(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return StoredOriginalFile(version_id=self.version_id)


class FakeS3Client:
    def __init__(self, *, version_id="version-1", head_response=None):
        self.version_id = version_id
        self.head_response = head_response
        self.put_calls = []
        self.head_calls = []

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        return {
            "VersionId": self.version_id,
            "ChecksumSHA256": kwargs["ChecksumSHA256"],
        }

    def head_object(self, **kwargs):
        self.head_calls.append(kwargs)
        if self.head_response is not None:
            return self.head_response
        put = self.put_calls[-1]
        return {
            "ContentLength": put["ContentLength"],
            "ChecksumSHA256": put["ChecksumSHA256"],
            "Metadata": put["Metadata"],
            "ServerSideEncryption": put["ServerSideEncryption"],
            "SSEKMSKeyId": put["SSEKMSKeyId"],
            "VersionId": self.version_id,
        }


class SalesFileValidationTests(TestCase):
    def test_upload_form_accepts_csv_and_normalizes_source_system(self):
        uploaded_file = csv_upload()
        form = SalesImportUploadForm(
            data={"source_system": "  distributor-portal  "},
            files={"file": uploaded_file},
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["source_system"], "DISTRIBUTOR-PORTAL")
        self.assertEqual(form.inspection.file_format, SalesImport.FileFormat.CSV)
        self.assertEqual(form.inspection.size_bytes, uploaded_file.size)
        self.assertEqual(
            form.inspection.sha256,
            hashlib.sha256(uploaded_file.read()).hexdigest(),
        )
        self.assertEqual(uploaded_file.tell(), uploaded_file.size)
        uploaded_file.seek(0)

    def test_csv_inspection_rejects_unsafe_or_empty_content(self):
        invalid_files = (
            (b"", "empty"),
            (b"   \n\t", "no data"),
            (b"column\x00value", "null bytes"),
            (b"\xff\xfe", "UTF-8"),
        )

        for content, expected_message in invalid_files:
            with self.subTest(expected_message=expected_message):
                with self.assertRaisesMessage(ValidationError, expected_message):
                    inspect_sales_file(
                        csv_upload(content=content),
                        max_bytes=1_024,
                    )

    def test_inspection_rejects_unsupported_and_oversized_files(self):
        with self.assertRaisesMessage(ValidationError, ".csv or .xlsx"):
            inspect_sales_file(
                SimpleUploadedFile("sales.txt", b"some data"),
                max_bytes=1_024,
            )

        with self.assertRaisesMessage(ValidationError, "upload limit"):
            inspect_sales_file(csv_upload(content=b"12345"), max_bytes=4)

    def test_xlsx_inspection_accepts_required_structure_and_rejects_unsafe_path(self):
        uploaded_file = xlsx_upload()

        inspection = inspect_sales_file(uploaded_file, max_bytes=10_000)

        self.assertEqual(inspection.file_format, SalesImport.FileFormat.XLSX)
        self.assertEqual(inspection.size_bytes, uploaded_file.size)
        self.assertEqual(uploaded_file.tell(), 0)

        with self.assertRaisesMessage(ValidationError, "unsafe path"):
            inspect_sales_file(
                xlsx_upload(extra_members=(("../unexpected.xml", "data"),)),
                max_bytes=10_000,
            )


class SalesImportPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.administrator = user_model.objects.create_user(
            email="administrator@example.com",
            password="test-only-password",
            role=user_model.Role.ADMIN,
        )
        cls.business_user = user_model.objects.create_user(
            email="business-user@example.com",
            password="test-only-password",
            role=user_model.Role.BUSINESS_USER,
        )

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("sales_imports:list"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_business_user_is_forbidden(self):
        self.client.force_login(self.business_user)

        response = self.client.get(reverse("sales_imports:create"))

        self.assertEqual(response.status_code, 403)

    def test_administrator_can_open_import_pages(self):
        self.client.force_login(self.administrator)

        list_response = self.client.get(reverse("sales_imports:list"))
        create_response = self.client.get(reverse("sales_imports:create"))

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(create_response.status_code, 200)

    def test_upload_post_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.administrator)

        response = client.post(
            reverse("sales_imports:create"),
            {"source_system": "ERP", "file": csv_upload()},
        )

        self.assertEqual(response.status_code, 403)

    @override_settings(SALES_IMPORT_MAX_UPLOAD_BYTES=4)
    def test_streaming_upload_limit_stops_oversized_file(self):
        self.client.force_login(self.administrator)

        response = self.client.post(
            reverse("sales_imports:create"),
            {"source_system": "ERP", "file": csv_upload(content=b"12345")},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SalesImport.objects.count(), 0)
        self.assertContains(response, "This field is required")

    def test_request_rejects_extra_file_parts(self):
        self.client.force_login(self.administrator)

        response = self.client.post(
            reverse("sales_imports:create"),
            {
                "source_system": "ERP",
                "file": [
                    csv_upload(name="first.csv"),
                    csv_upload(name="second.csv"),
                ],
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(SalesImport.objects.count(), 0)


class SalesImportServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.administrator = user_model.objects.create_user(
            email="uploader@example.com",
            password="test-only-password",
            role=user_model.Role.ADMIN,
        )

    def test_success_stores_original_and_marks_import_received(self):
        uploaded_file = csv_upload(name="incoming/sales.csv")
        inspection = inspect_sales_file(uploaded_file, max_bytes=10_000)
        storage = RecordingOriginalFileStorage(version_id="s3-version-7")

        result = receive_sales_import(
            uploaded_file=uploaded_file,
            inspection=inspection,
            source_system="  sap  ",
            uploaded_by=self.administrator,
            storage=storage,
        )

        sales_import = result.sales_import
        self.assertTrue(result.created)
        self.assertEqual(sales_import.source_system, "SAP")
        self.assertEqual(
            sales_import.row_contract,
            SalesImport.RowContract.SALES_ROWS_V1,
        )
        self.assertEqual(sales_import.original_filename, "sales.csv")
        self.assertEqual(sales_import.status, SalesImport.Status.RECEIVED)
        self.assertEqual(sales_import.storage_version_id, "s3-version-7")
        self.assertIsNotNone(sales_import.received_at)
        self.assertIsNone(sales_import.failed_at)
        self.assertEqual(sales_import.failure_code, SalesImport.FailureCode.NONE)
        self.assertEqual(sales_import.storage_bucket, storage.bucket_name)
        self.assertEqual(sales_import.storage_kms_key_arn, storage.kms_key_arn)
        self.assertEqual(
            sales_import.storage_key,
            (
                f"sales-imports/original/{timezone.localdate():%Y/%m}/"
                f"{sales_import.pk}.csv"
            ),
        )
        self.assertEqual(len(storage.calls), 1)
        storage_call = storage.calls[0]
        self.assertIs(storage_call["uploaded_file"], uploaded_file)
        self.assertEqual(storage_call["object_key"], sales_import.storage_key)
        self.assertEqual(storage_call["sha256"], inspection.sha256)
        self.assertEqual(storage_call["size_bytes"], inspection.size_bytes)
        self.assertEqual(storage_call["import_id"], str(sales_import.pk))
        self.assertEqual(storage_call["data_contract"], sales_import.data_contract)
        self.assertEqual(storage_call["row_contract"], sales_import.row_contract)

    def test_administrator_can_submit_upload_through_the_view(self):
        self.client.force_login(self.administrator)
        storage = RecordingOriginalFileStorage(version_id="view-version")

        with patch(
            "sales_imports.services.get_sales_import_storage",
            return_value=storage,
        ):
            response = self.client.post(
                reverse("sales_imports:create"),
                {"source_system": "ERP", "file": csv_upload()},
            )

        sales_import = SalesImport.objects.get()
        self.assertRedirects(
            response,
            reverse("sales_imports:detail", args=(sales_import.pk,)),
            fetch_redirect_response=False,
        )
        self.assertEqual(sales_import.status, SalesImport.Status.RECEIVED)
        self.assertEqual(sales_import.storage_version_id, "view-version")

    def test_storage_failure_remains_receiving_without_losing_audit_row(self):
        uploaded_file = csv_upload()
        inspection = inspect_sales_file(uploaded_file, max_bytes=10_000)
        storage = RecordingOriginalFileStorage(
            error=OriginalFileUploadError("simulated S3 failure"),
        )

        with patch("sales_imports.services.logger.exception") as logged_exception:
            result = receive_sales_import(
                uploaded_file=uploaded_file,
                inspection=inspection,
                source_system="ERP",
                uploaded_by=self.administrator,
                storage=storage,
            )

        sales_import = result.sales_import
        self.assertTrue(result.created)
        self.assertEqual(sales_import.status, SalesImport.Status.RECEIVING)
        self.assertEqual(sales_import.failure_code, SalesImport.FailureCode.NONE)
        self.assertIsNone(sales_import.failed_at)
        self.assertIsNone(sales_import.received_at)
        self.assertEqual(sales_import.storage_version_id, "")
        self.assertTrue(SalesImport.objects.filter(pk=sales_import.pk).exists())
        logged_exception.assert_called_once()

    def test_verification_failure_also_remains_receiving_for_reconciliation(self):
        uploaded_file = csv_upload()
        inspection = inspect_sales_file(uploaded_file, max_bytes=10_000)
        storage = RecordingOriginalFileStorage(
            error=OriginalFileVerificationError("temporary verification failure"),
        )

        with patch("sales_imports.services.logger.exception"):
            result = receive_sales_import(
                uploaded_file=uploaded_file,
                inspection=inspection,
                source_system="ERP",
                uploaded_by=self.administrator,
                storage=storage,
            )

        self.assertEqual(result.sales_import.status, SalesImport.Status.RECEIVING)
        self.assertEqual(result.sales_import.failure_code, "")

    def test_duplicate_active_file_reuses_existing_import_without_storing_again(self):
        first_file = csv_upload()
        inspection = inspect_sales_file(first_file, max_bytes=10_000)
        first_storage = RecordingOriginalFileStorage()
        first = receive_sales_import(
            uploaded_file=first_file,
            inspection=inspection,
            source_system="ERP",
            uploaded_by=self.administrator,
            storage=first_storage,
        )
        unused_storage = RecordingOriginalFileStorage(
            error=AssertionError("duplicate file must not be stored again"),
        )

        duplicate = receive_sales_import(
            uploaded_file=csv_upload(),
            inspection=inspection,
            source_system=" erp ",
            uploaded_by=self.administrator,
            storage=unused_storage,
        )

        self.assertFalse(duplicate.created)
        self.assertEqual(duplicate.sales_import.pk, first.sales_import.pk)
        self.assertEqual(unused_storage.calls, [])
        self.assertEqual(SalesImport.objects.count(), 1)

    def test_permanently_unusable_object_allows_same_file_to_be_uploaded_again(self):
        first_file = csv_upload()
        inspection = inspect_sales_file(first_file, max_bytes=10_000)
        first = receive_sales_import(
            uploaded_file=first_file,
            inspection=inspection,
            source_system="ERP",
            uploaded_by=self.administrator,
            storage=RecordingOriginalFileStorage(version_id="bad-version"),
        )
        first.sales_import.mark_stored_object_failed(
            failure_code=SalesImport.FailureCode.VERIFICATION_FAILED
        )
        replacement_file = csv_upload()

        replacement = receive_sales_import(
            uploaded_file=replacement_file,
            inspection=inspect_sales_file(replacement_file, max_bytes=10_000),
            source_system="ERP",
            uploaded_by=self.administrator,
            storage=RecordingOriginalFileStorage(version_id="replacement-version"),
        )

        self.assertTrue(replacement.created)
        self.assertNotEqual(replacement.sales_import.pk, first.sales_import.pk)
        self.assertEqual(SalesImport.objects.count(), 2)


class S3OriginalFileStorageTests(TestCase):
    bucket_name = "private-sales-imports"
    kms_key_arn = "arn:aws:kms:ap-south-1:123456789012:key/test-key"

    def storage(self, client):
        return S3OriginalFileStorage(
            client=client,
            bucket_name=self.bucket_name,
            kms_key_arn=self.kms_key_arn,
            region_name="ap-south-1",
        )

    def test_store_uses_private_versioned_kms_contract_and_verifies_object(self):
        content = b"transaction_id,amount\nTX-001,100\n"
        sha256 = hashlib.sha256(content).hexdigest()
        uploaded_file = csv_upload(content=content)
        client = FakeS3Client(version_id="version-42")

        stored = self.storage(client).store(
            uploaded_file=uploaded_file,
            object_key="sales-imports/original/2026/09/id.csv",
            size_bytes=len(content),
            sha256=sha256,
            content_type="text/csv",
            import_id="import-id",
            data_contract=SalesImport.DataContract.SALES_V1,
            row_contract=SalesImport.RowContract.SALES_ROWS_V1,
        )

        self.assertEqual(stored.version_id, "version-42")
        self.assertEqual(len(client.put_calls), 1)
        put = client.put_calls[0]
        self.assertEqual(put["Bucket"], self.bucket_name)
        self.assertEqual(put["Key"], "sales-imports/original/2026/09/id.csv")
        self.assertIs(put["Body"], uploaded_file)
        self.assertEqual(put["ContentLength"], len(content))
        self.assertEqual(put["ContentType"], "text/csv")
        self.assertEqual(put["ChecksumAlgorithm"], "SHA256")
        self.assertEqual(
            put["ChecksumSHA256"],
            base64.b64encode(bytes.fromhex(sha256)).decode("ascii"),
        )
        self.assertEqual(put["ServerSideEncryption"], "aws:kms")
        self.assertEqual(put["SSEKMSKeyId"], self.kms_key_arn)
        self.assertEqual(
            put["Metadata"],
            {
                "sha256": sha256,
                "sales-import-id": "import-id",
                "data-contract": SalesImport.DataContract.SALES_V1,
                "row-contract": SalesImport.RowContract.SALES_ROWS_V1,
            },
        )
        self.assertEqual(
            client.head_calls,
            [
                {
                    "Bucket": self.bucket_name,
                    "Key": "sales-imports/original/2026/09/id.csv",
                    "ChecksumMode": "ENABLED",
                    "VersionId": "version-42",
                }
            ],
        )
        self.assertEqual(uploaded_file.tell(), 0)

    def test_store_rejects_missing_version_or_failed_verification(self):
        content = b"transaction_id,amount\nTX-001,100\n"
        sha256 = hashlib.sha256(content).hexdigest()
        clients = (
            (FakeS3Client(version_id=""), "did not return a version"),
            (
                FakeS3Client(
                    head_response={
                        "ContentLength": len(content) + 1,
                        "ChecksumSHA256": base64.b64encode(
                            bytes.fromhex(sha256)
                        ).decode("ascii"),
                        "Metadata": {
                            "sha256": sha256,
                            "sales-import-id": "import-id",
                            "data-contract": SalesImport.DataContract.SALES_V1,
                            "row-contract": SalesImport.RowContract.SALES_ROWS_V1,
                        },
                        "ServerSideEncryption": "aws:kms",
                        "SSEKMSKeyId": self.kms_key_arn,
                        "VersionId": "version-1",
                    }
                ),
                "unexpected object size",
            ),
            (
                FakeS3Client(
                    head_response={
                        "ContentLength": len(content),
                        "ChecksumSHA256": base64.b64encode(b"wrong").decode(
                            "ascii"
                        ),
                        "Metadata": {
                            "sha256": sha256,
                            "sales-import-id": "import-id",
                            "data-contract": SalesImport.DataContract.SALES_V1,
                            "row-contract": SalesImport.RowContract.SALES_ROWS_V1,
                        },
                        "ServerSideEncryption": "aws:kms",
                        "SSEKMSKeyId": self.kms_key_arn,
                        "VersionId": "version-1",
                    }
                ),
                "unexpected object checksum",
            ),
            (
                FakeS3Client(
                    head_response={
                        "ContentLength": len(content),
                        "ChecksumSHA256": base64.b64encode(
                            bytes.fromhex(sha256)
                        ).decode("ascii"),
                        "Metadata": {
                            "sha256": sha256,
                            "sales-import-id": "import-id",
                            "data-contract": SalesImport.DataContract.SALES_V1,
                            "row-contract": "wrong-contract",
                        },
                        "ServerSideEncryption": "aws:kms",
                        "SSEKMSKeyId": self.kms_key_arn,
                        "VersionId": "version-1",
                    }
                ),
                "sales row contract",
            ),
        )

        for client, expected_message in clients:
            with self.subTest(expected_message=expected_message):
                uploaded_file = csv_upload(content=content)
                with self.assertRaisesMessage(
                    OriginalFileStorageError,
                    expected_message,
                ):
                    self.storage(client).store(
                        uploaded_file=uploaded_file,
                        object_key="sales-imports/original/2026/09/id.csv",
                        size_bytes=len(content),
                        sha256=sha256,
                        content_type="text/csv",
                        import_id="import-id",
                        data_contract=SalesImport.DataContract.SALES_V1,
                        row_contract=SalesImport.RowContract.SALES_ROWS_V1,
                    )
                self.assertEqual(uploaded_file.tell(), 0)

    @override_settings(
        SALES_IMPORT_S3_BUCKET="",
        SALES_IMPORT_S3_KMS_KEY_ARN="",
        SALES_IMPORT_AWS_REGION="",
    )
    def test_storage_requires_bucket_kms_key_and_region(self):
        with self.assertRaisesMessage(
            OriginalFileStorageError,
            "not fully configured",
        ):
            S3OriginalFileStorage()

    def test_storage_rejects_kms_alias_instead_of_resolved_key_arn(self):
        with self.assertRaisesMessage(
            OriginalFileStorageError,
            "concrete KMS key ARN",
        ):
            S3OriginalFileStorage(
                client=FakeS3Client(),
                bucket_name=self.bucket_name,
                kms_key_arn="alias/pharma-intel/staging/sales-import",
                region_name="ap-south-1",
            )


class SalesImportAuditGuardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.administrator = user_model.objects.create_user(
            email="audit-uploader@example.com",
            password="test-only-password",
            role=user_model.Role.ADMIN,
        )

    def create_receiving_import(self, *, storage_key="sales-imports/test.csv"):
        content = b"transaction_id,amount\nTX-001,100\n"
        return SalesImport.objects.create(
            source_system="ERP",
            original_filename="sales.csv",
            content_type="text/csv",
            file_format=SalesImport.FileFormat.CSV,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            storage_bucket="private-sales-imports",
            storage_kms_key_arn=TEST_KMS_KEY_ARN,
            storage_key=storage_key,
            uploaded_by=self.administrator,
        )

    def test_normal_save_and_instance_delete_cannot_rewrite_audit_record(self):
        sales_import = self.create_receiving_import()
        sales_import.original_filename = "rewritten.csv"

        with self.assertRaisesMessage(ValidationError, "controlled status"):
            sales_import.save()
        with self.assertRaisesMessage(ValidationError, "cannot be deleted"):
            sales_import.delete()

        sales_import.refresh_from_db()
        self.assertEqual(sales_import.original_filename, "sales.csv")

    def test_uploader_cannot_be_deleted_while_audit_record_exists(self):
        self.create_receiving_import()

        with self.assertRaises(ProtectedError):
            self.administrator.delete()

    def test_database_rejects_status_without_matching_metadata(self):
        sales_import = self.create_receiving_import()

        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesImport.objects.filter(pk=sales_import.pk).update(
                status=SalesImport.Status.RECEIVED,
            )

    def test_database_rejects_lineage_updates_and_deletes(self):
        sales_import = self.create_receiving_import()

        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesImport.objects.filter(pk=sales_import.pk).update(
                original_filename="rewritten.csv",
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesImport.objects.filter(pk=sales_import.pk).delete()

    def test_database_rejects_kms_lineage_update(self):
        sales_import = self.create_receiving_import()

        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesImport.objects.filter(pk=sales_import.pk).update(
                storage_kms_key_arn=(
                    "arn:aws:kms:ap-south-1:123456789012:key/replacement"
                )
            )

    def test_database_preserves_received_storage_evidence_when_marking_failed(self):
        sales_import = self.create_receiving_import()
        sales_import.mark_received(version_id="verified-version")

        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesImport.objects.filter(pk=sales_import.pk).update(
                status=SalesImport.Status.FAILED,
                storage_version_id="different-version",
                received_at=timezone.now() + timedelta(seconds=1),
                failed_at=timezone.now(),
                failure_code=SalesImport.FailureCode.VERIFICATION_FAILED,
            )

    def test_database_rejects_fabricated_evidence_on_receiving_failure(self):
        sales_import = self.create_receiving_import()

        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesImport.objects.filter(pk=sales_import.pk).update(
                status=SalesImport.Status.FAILED,
                storage_version_id="fabricated-version",
                received_at=timezone.now(),
                failed_at=timezone.now(),
                failure_code=SalesImport.FailureCode.VERIFICATION_FAILED,
            )

    def test_database_restricts_post_receipt_failure_codes(self):
        sales_import = self.create_receiving_import()
        sales_import.mark_received(version_id="verified-version")

        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesImport.objects.filter(pk=sales_import.pk).update(
                status=SalesImport.Status.FAILED,
                failed_at=timezone.now(),
                failure_code=SalesImport.FailureCode.STORAGE_ERROR,
            )

    def test_database_rejects_duplicate_active_file(self):
        existing = self.create_receiving_import()

        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesImport.objects.create(
                source_system=existing.source_system.lower(),
                original_filename="duplicate.csv",
                content_type="text/csv",
                file_format=SalesImport.FileFormat.CSV,
                size_bytes=existing.size_bytes,
                sha256=existing.sha256,
                storage_bucket="private-sales-imports",
                storage_kms_key_arn=TEST_KMS_KEY_ARN,
                storage_key="sales-imports/duplicate.csv",
                uploaded_by=self.administrator,
            )

    def test_database_enforces_the_versioned_row_contract(self):
        sales_import = self.create_receiving_import()

        self.assertEqual(
            sales_import.row_contract,
            SalesImport.RowContract.SALES_ROWS_V1,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesImport.objects.create(
                row_contract="unknown",
                source_system="OTHER-ERP",
                original_filename="unknown.csv",
                content_type="text/csv",
                file_format=SalesImport.FileFormat.CSV,
                size_bytes=sales_import.size_bytes,
                sha256=sales_import.sha256,
                storage_bucket="private-sales-imports",
                storage_kms_key_arn=TEST_KMS_KEY_ARN,
                storage_key="sales-imports/unknown.csv",
                uploaded_by=self.administrator,
            )


class ReconcileSalesImportsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.administrator = user_model.objects.create_user(
            email="reconciliation@example.com",
            password="test-only-password",
            role=user_model.Role.ADMIN,
        )

    def create_receiving_import(self, *, storage_key="sales-imports/reconcile.csv"):
        content = b"transaction_id,amount\nTX-001,100\n"
        return SalesImport.objects.create(
            source_system="ERP",
            original_filename="sales.csv",
            content_type="text/csv",
            file_format=SalesImport.FileFormat.CSV,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            storage_bucket="private-sales-imports",
            storage_kms_key_arn=TEST_KMS_KEY_ARN,
            storage_key=storage_key,
            uploaded_by=self.administrator,
        )

    class ReconciliationStorage:
        def __init__(self, outcome):
            self.outcome = outcome
            self.calls = []

        def verify(self, **kwargs):
            self.calls.append(kwargs)
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return StoredOriginalFile(version_id=self.outcome)

    def run_reconcile(self, outcome, *, expect_deferred_error=False):
        storage = self.ReconciliationStorage(outcome)
        with (
            patch(
                "sales_imports.management.commands.reconcile_sales_imports."
                "get_sales_import_storage",
                return_value=storage,
            ),
            patch(
                "sales_imports.management.commands.reconcile_sales_imports."
                "timezone.now",
                return_value=timezone.now() + timedelta(minutes=30),
            ),
        ):
            if expect_deferred_error:
                with self.assertRaisesMessage(
                    CommandError,
                    "1 deferred for retry",
                ):
                    call_command("reconcile_sales_imports", older_than_minutes=15)
            else:
                call_command("reconcile_sales_imports", older_than_minutes=15)
        return storage

    def test_reconcile_marks_verified_object_received(self):
        sales_import = self.create_receiving_import()

        storage = self.run_reconcile("recovered-version")

        sales_import.refresh_from_db()
        self.assertEqual(sales_import.status, SalesImport.Status.RECEIVED)
        self.assertEqual(sales_import.storage_version_id, "recovered-version")
        self.assertEqual(
            storage.calls,
            [
                {
                    "bucket_name": sales_import.storage_bucket,
                    "object_key": sales_import.storage_key,
                    "version_id": None,
                    "size_bytes": sales_import.size_bytes,
                    "sha256": sales_import.sha256,
                    "import_id": str(sales_import.pk),
                    "data_contract": sales_import.data_contract,
                    "row_contract": sales_import.row_contract,
                    "expected_kms_key_arn": sales_import.storage_kms_key_arn,
                }
            ],
        )

    def test_reconcile_marks_missing_or_invalid_object_failed(self):
        outcomes = (
            (
                OriginalFileNotFoundError("missing"),
                SalesImport.FailureCode.OBJECT_MISSING,
            ),
            (
                OriginalFileVerificationError("invalid"),
                SalesImport.FailureCode.VERIFICATION_FAILED,
            ),
        )
        for index, (outcome, failure_code) in enumerate(outcomes):
            with self.subTest(failure_code=failure_code):
                sales_import = self.create_receiving_import(
                    storage_key=f"sales-imports/reconcile-{index}.csv"
                )

                self.run_reconcile(outcome)

                sales_import.refresh_from_db()
                self.assertEqual(sales_import.status, SalesImport.Status.FAILED)
                self.assertEqual(sales_import.failure_code, failure_code)

    def test_reconcile_defers_transient_storage_errors(self):
        sales_import = self.create_receiving_import()

        self.run_reconcile(
            OriginalFileStorageError("temporary outage"),
            expect_deferred_error=True,
        )

        sales_import.refresh_from_db()
        self.assertEqual(sales_import.status, SalesImport.Status.RECEIVING)
