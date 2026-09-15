import base64
import hashlib
import io
from unittest import TestCase

from .storage import (
    OriginalFileDownloadError,
    OriginalFileKMSProvenanceError,
    OriginalFileNotFoundError,
    OriginalFileVerificationError,
    S3OriginalFileStorage,
)


class FakeS3Error(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class RecordingBody:
    def __init__(self, content, *, fail_after_reads=None):
        self.stream = io.BytesIO(content)
        self.fail_after_reads = fail_after_reads
        self.read_sizes = []
        self.closed = False

    def read(self, size):
        if (
            self.fail_after_reads is not None
            and len(self.read_sizes) >= self.fail_after_reads
        ):
            raise OSError("simulated interrupted response body")
        self.read_sizes.append(size)
        return self.stream.read(size)

    def close(self):
        self.closed = True
        self.stream.close()


class FakeS3Client:
    def __init__(self, *, response=None, error=None):
        self.response = response
        self.error = error
        self.get_calls = []
        self.head_calls = []

    def get_object(self, **kwargs):
        self.get_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response

    def head_object(self, **kwargs):
        self.head_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class S3OriginalFileDownloadTests(TestCase):
    bucket_name = "private-sales-imports"
    object_key = "sales-imports/original/2026/09/import-id.csv"
    version_id = "version-42"
    import_id = "import-id"
    data_contract = "sales_v1"
    row_contract = "sales_rows_v1"
    kms_key_arn = "arn:aws:kms:ap-south-1:123456789012:key/test-key"

    def storage(self, client):
        return S3OriginalFileStorage(
            client=client,
            bucket_name=self.bucket_name,
            kms_key_arn=self.kms_key_arn,
            region_name="ap-south-1",
        )

    def download_arguments(self, content):
        return {
            "bucket_name": self.bucket_name,
            "object_key": self.object_key,
            "version_id": self.version_id,
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "import_id": self.import_id,
            "data_contract": self.data_contract,
            "row_contract": self.row_contract,
            "expected_kms_key_arn": self.kms_key_arn,
        }

    def response(self, content, body, **overrides):
        sha256 = hashlib.sha256(content).hexdigest()
        response = {
            "Body": body,
            "ContentLength": len(content),
            "ChecksumSHA256": base64.b64encode(bytes.fromhex(sha256)).decode(
                "ascii"
            ),
            "Metadata": {
                "sha256": sha256,
                "sales-import-id": self.import_id,
                "data-contract": self.data_contract,
                "row-contract": self.row_contract,
            },
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": self.kms_key_arn,
            "VersionId": self.version_id,
        }
        response.update(overrides)
        return response

    def test_open_version_downloads_exact_version_in_bounded_chunks(self):
        content = (b"source_record_id,quantity\n" + (b"SALE-001,1\n" * 12_000))
        body = RecordingBody(content)
        client = FakeS3Client(response=self.response(content, body))

        with self.storage(client).open_version(
            **self.download_arguments(content)
        ) as downloaded_file:
            self.assertTrue(downloaded_file.seekable())
            self.assertEqual(downloaded_file.tell(), 0)
            self.assertEqual(downloaded_file.read(), content)
            downloaded_file.seek(7)
            self.assertEqual(downloaded_file.read(9), content[7:16])
            self.assertTrue(body.closed)

        self.assertTrue(downloaded_file.closed)
        self.assertEqual(
            client.get_calls,
            [
                {
                    "Bucket": self.bucket_name,
                    "Key": self.object_key,
                    "VersionId": self.version_id,
                    "ChecksumMode": "ENABLED",
                }
            ],
        )
        self.assertGreater(len(body.read_sizes), 2)
        self.assertTrue(all(0 < size <= 64 * 1024 for size in body.read_sizes))

    def test_open_version_uses_recorded_key_after_configuration_rotation(self):
        content = b"historical encrypted object"
        historical_key = (
            "arn:aws:kms:ap-south-1:123456789012:key/historical-import-key"
        )
        body = RecordingBody(content)
        response = self.response(
            content,
            body,
            SSEKMSKeyId=historical_key,
        )

        arguments = self.download_arguments(content)
        arguments["expected_kms_key_arn"] = historical_key
        with self.storage(FakeS3Client(response=response)).open_version(
            **arguments
        ) as downloaded_file:
            self.assertEqual(downloaded_file.read(), content)

    def test_open_version_does_not_guess_missing_legacy_kms_provenance(self):
        content = b"legacy object"
        client = FakeS3Client(response=self.response(content, RecordingBody(content)))
        arguments = self.download_arguments(content)
        arguments["expected_kms_key_arn"] = None

        with self.assertRaisesRegex(
            OriginalFileKMSProvenanceError,
            "Backfill its exact-version KMS provenance",
        ):
            with self.storage(client).open_version(**arguments):
                self.fail("Missing KMS provenance must stop before S3 download.")

        self.assertEqual(client.get_calls, [])

    def test_discovers_kms_and_version_from_verified_legacy_object(self):
        content = b"legacy object"
        client = FakeS3Client(response=self.response(content, RecordingBody(content)))
        arguments = self.download_arguments(content)
        arguments.pop("expected_kms_key_arn")

        provenance = self.storage(client).discover_kms_provenance(**arguments)

        self.assertEqual(provenance.kms_key_arn, self.kms_key_arn)
        self.assertEqual(provenance.version_id, self.version_id)
        self.assertEqual(
            client.head_calls,
            [
                {
                    "Bucket": self.bucket_name,
                    "Key": self.object_key,
                    "VersionId": self.version_id,
                    "ChecksumMode": "ENABLED",
                }
            ],
        )

    def test_discovers_latest_version_for_unfinished_legacy_intake(self):
        content = b"unfinished legacy object"
        client = FakeS3Client(response=self.response(content, RecordingBody(content)))
        arguments = self.download_arguments(content)
        arguments.pop("expected_kms_key_arn")
        arguments["version_id"] = None

        provenance = self.storage(client).discover_kms_provenance(**arguments)

        self.assertEqual(provenance.version_id, self.version_id)
        self.assertNotIn("VersionId", client.head_calls[0])

    def test_open_version_reports_cumulative_progress_for_written_chunks(self):
        content = b"x" * ((64 * 1024 * 2) + 17)
        body = RecordingBody(content)
        client = FakeS3Client(response=self.response(content, body))
        progress = []

        with self.storage(client).open_version(
            **self.download_arguments(content),
            progress_callback=progress.append,
        ) as downloaded_file:
            self.assertEqual(downloaded_file.read(), content)

        self.assertEqual(progress, [64 * 1024, 128 * 1024, len(content)])

    def test_open_version_stops_and_closes_resources_when_progress_fails(self):
        content = b"x" * (80 * 1024)
        body = RecordingBody(content)
        client = FakeS3Client(response=self.response(content, body))

        def reject_progress(_downloaded_size):
            raise RuntimeError("lease was lost")

        with self.assertRaisesRegex(RuntimeError, "lease was lost"):
            with self.storage(client).open_version(
                **self.download_arguments(content),
                progress_callback=reject_progress,
            ):
                self.fail("A failed progress callback must stop the download.")

        self.assertTrue(body.closed)
        self.assertEqual(body.read_sizes, [64 * 1024])

    def test_open_version_rejects_untrusted_s3_response_metadata(self):
        content = b"verified content"
        expected_sha256 = hashlib.sha256(content).hexdigest()
        expected_checksum = base64.b64encode(
            bytes.fromhex(expected_sha256)
        ).decode("ascii")
        cases = (
            ({"VersionId": "wrong-version"}, "unexpected object version"),
            ({"ContentLength": len(content) + 1}, "unexpected object size"),
            ({"ChecksumSHA256": "wrong"}, "unexpected object checksum"),
            (
                {
                    "Metadata": {
                        "sha256": "wrong",
                        "sales-import-id": self.import_id,
                        "data-contract": self.data_contract,
                        "row-contract": self.row_contract,
                    }
                },
                "file checksum",
            ),
            (
                {
                    "Metadata": {
                        "sha256": expected_sha256,
                        "sales-import-id": "wrong-import",
                        "data-contract": self.data_contract,
                        "row-contract": self.row_contract,
                    }
                },
                "sales-import reference",
            ),
            (
                {
                    "Metadata": {
                        "sha256": expected_sha256,
                        "sales-import-id": self.import_id,
                        "data-contract": "wrong-contract",
                        "row-contract": self.row_contract,
                    }
                },
                "intake contract",
            ),
            (
                {
                    "Metadata": {
                        "sha256": expected_sha256,
                        "sales-import-id": self.import_id,
                        "data-contract": self.data_contract,
                        "row-contract": "wrong-contract",
                    }
                },
                "sales row contract",
            ),
            ({"ServerSideEncryption": "AES256"}, "KMS encryption"),
            ({"SSEKMSKeyId": "wrong-key"}, "unexpected KMS key"),
        )

        for overrides, expected_message in cases:
            with self.subTest(expected_message=expected_message):
                body = RecordingBody(content)
                response = self.response(content, body, **overrides)
                if "ChecksumSHA256" not in overrides:
                    self.assertEqual(response["ChecksumSHA256"], expected_checksum)

                with self.assertRaisesRegex(
                    OriginalFileVerificationError,
                    expected_message,
                ):
                    with self.storage(FakeS3Client(response=response)).open_version(
                        **self.download_arguments(content)
                    ):
                        self.fail("An unverified object must not be exposed.")

                self.assertTrue(body.closed)
                self.assertEqual(body.read_sizes, [])

    def test_open_version_verifies_downloaded_bytes_and_size(self):
        expected_content = b"abcdef"
        cases = (
            (b"abcdeg", "checksum does not match"),
            (b"abc", "size does not match"),
            (b"abcdefg", "larger than the recorded size"),
        )

        for downloaded_content, expected_message in cases:
            with self.subTest(expected_message=expected_message):
                body = RecordingBody(downloaded_content)
                client = FakeS3Client(
                    response=self.response(expected_content, body)
                )

                with self.assertRaisesRegex(
                    OriginalFileVerificationError,
                    expected_message,
                ):
                    with self.storage(client).open_version(
                        **self.download_arguments(expected_content)
                    ):
                        self.fail("Invalid downloaded bytes must not be exposed.")

                self.assertTrue(body.closed)

    def test_open_version_distinguishes_not_found_and_transient_get_errors(self):
        content = b"content"
        cases = (
            (
                FakeS3Error("NoSuchVersion"),
                OriginalFileNotFoundError,
                "version was not found",
            ),
            (
                TimeoutError("temporary outage"),
                OriginalFileDownloadError,
                "could not be downloaded",
            ),
        )

        for error, expected_error, expected_message in cases:
            with self.subTest(expected_error=expected_error):
                client = FakeS3Client(error=error)

                with self.assertRaisesRegex(expected_error, expected_message):
                    with self.storage(client).open_version(
                        **self.download_arguments(content)
                    ):
                        self.fail("A failed S3 request must not yield a file.")

    def test_open_version_closes_body_after_interrupted_stream(self):
        content = b"x" * (80 * 1024)
        body = RecordingBody(content, fail_after_reads=1)
        client = FakeS3Client(response=self.response(content, body))

        with self.assertRaisesRegex(
            OriginalFileDownloadError,
            "download was interrupted",
        ):
            with self.storage(client).open_version(
                **self.download_arguments(content)
            ):
                self.fail("An interrupted response must not yield a file.")

        self.assertTrue(body.closed)
        self.assertEqual(body.read_sizes, [64 * 1024])

    def test_open_version_closes_temporary_file_after_consumer_error(self):
        content = b"content"
        body = RecordingBody(content)
        client = FakeS3Client(response=self.response(content, body))

        with self.assertRaisesRegex(RuntimeError, "consumer failed"):
            with self.storage(client).open_version(
                **self.download_arguments(content)
            ) as downloaded_file:
                raise RuntimeError("consumer failed")

        self.assertTrue(body.closed)
        self.assertTrue(downloaded_file.closed)
