import base64
import hashlib
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import BinaryIO, Callable, Iterator

from django.conf import settings


class OriginalFileStorageError(Exception):
    """Raised when the original file cannot be stored and verified safely."""


class OriginalFileNotFoundError(OriginalFileStorageError):
    """Raised when a recorded original object or version is absent."""


class OriginalFileVerificationError(OriginalFileStorageError):
    """Raised when stored metadata does not match the intake record."""


class OriginalFileUploadError(OriginalFileStorageError):
    """Raised when S3 rejects the original-file write itself."""


class OriginalFileDownloadError(OriginalFileStorageError):
    """Raised when a stored original cannot be downloaded due to a transient error."""


class OriginalFileKMSProvenanceError(OriginalFileStorageError):
    """Raised when an intake has no trustworthy record of its encryption key."""


@dataclass(frozen=True)
class StoredOriginalFile:
    version_id: str


@dataclass(frozen=True)
class DiscoveredKMSProvenance:
    kms_key_arn: str
    version_id: str


class S3OriginalFileStorage:
    """Store original imports in the private, versioned S3 bucket."""

    _DOWNLOAD_CHUNK_SIZE = 64 * 1024
    _DOWNLOAD_SPOOL_MAX_SIZE = 1024 * 1024
    _KMS_KEY_ARN_PATTERN = re.compile(
        r"^arn:[^:]+:kms:[^:]+:[0-9]{12}:key/[^/\s]+$"
    )

    def __init__(
        self,
        *,
        client=None,
        bucket_name: str | None = None,
        kms_key_arn: str | None = None,
        region_name: str | None = None,
    ):
        self.bucket_name = (
            bucket_name
            if bucket_name is not None
            else settings.SALES_IMPORT_S3_BUCKET
        ).strip()
        self.kms_key_arn = (
            kms_key_arn
            if kms_key_arn is not None
            else settings.SALES_IMPORT_S3_KMS_KEY_ARN
        ).strip()
        self.region_name = (
            region_name
            if region_name is not None
            else settings.SALES_IMPORT_AWS_REGION
        ).strip()
        self._client = client

        if not self.bucket_name or not self.kms_key_arn or not self.region_name:
            raise OriginalFileStorageError(
                "Sales-import S3 storage is not fully configured."
            )
        if self._KMS_KEY_ARN_PATTERN.fullmatch(self.kms_key_arn) is None:
            raise OriginalFileStorageError(
                "Sales-import storage requires a concrete KMS key ARN, not an alias."
            )

    @property
    def client(self):
        if self._client is None:
            try:
                import boto3
                from botocore.config import Config

                self._client = boto3.client(
                    "s3",
                    region_name=self.region_name,
                    config=Config(
                        connect_timeout=5,
                        read_timeout=120,
                        retries={"max_attempts": 3, "mode": "standard"},
                        max_pool_connections=20,
                    ),
                )
            except Exception as error:
                raise OriginalFileStorageError(
                    "The S3 client could not be initialized."
                ) from error
        return self._client

    def _required_expected_kms_key_arn(self, value: str | None) -> str:
        expected_key = str(value or "").strip()
        if not expected_key:
            raise OriginalFileKMSProvenanceError(
                "The import does not record the KMS key used for this object. "
                "Backfill its exact-version KMS provenance before processing it."
            )
        if self._KMS_KEY_ARN_PATTERN.fullmatch(expected_key) is None:
            raise OriginalFileKMSProvenanceError(
                "The import records an invalid KMS key ARN."
            )
        return expected_key

    def store(
        self,
        *,
        uploaded_file,
        object_key: str,
        size_bytes: int,
        sha256: str,
        content_type: str,
        import_id: str,
        data_contract: str,
        row_contract: str,
    ) -> StoredOriginalFile:
        checksum_sha256 = base64.b64encode(bytes.fromhex(sha256)).decode("ascii")
        uploaded_file.seek(0)
        try:
            try:
                response = self.client.put_object(
                    Bucket=self.bucket_name,
                    Key=object_key,
                    Body=uploaded_file,
                    ContentLength=size_bytes,
                    ContentType=content_type or "application/octet-stream",
                    ChecksumAlgorithm="SHA256",
                    ChecksumSHA256=checksum_sha256,
                    ServerSideEncryption="aws:kms",
                    SSEKMSKeyId=self.kms_key_arn,
                    Metadata={
                        "sha256": sha256,
                        "sales-import-id": import_id,
                        "data-contract": data_contract,
                        "row-contract": row_contract,
                    },
                )
            except OriginalFileStorageError:
                raise
            except Exception as error:
                raise OriginalFileUploadError(
                    "S3 rejected the original-file upload."
                ) from error

            version_id = str(response.get("VersionId", "")).strip()
            if not version_id:
                raise OriginalFileStorageError(
                    "S3 did not return a version for the stored object."
                )
            if response.get("ChecksumSHA256") != checksum_sha256:
                raise OriginalFileVerificationError(
                    "S3 did not confirm the uploaded checksum."
                )

            verified = self.verify(
                bucket_name=self.bucket_name,
                object_key=object_key,
                version_id=version_id,
                size_bytes=size_bytes,
                sha256=sha256,
                import_id=import_id,
                data_contract=data_contract,
                row_contract=row_contract,
                expected_kms_key_arn=self.kms_key_arn,
            )

            return verified
        finally:
            uploaded_file.seek(0)

    def verify(
        self,
        *,
        bucket_name: str,
        object_key: str,
        version_id: str | None,
        size_bytes: int,
        sha256: str,
        import_id: str,
        data_contract: str,
        row_contract: str,
        expected_kms_key_arn: str | None = None,
    ) -> StoredOriginalFile:
        expected_kms_key_arn = self._required_expected_kms_key_arn(
            expected_kms_key_arn
        )
        if not bucket_name.strip():
            raise OriginalFileVerificationError(
                "The recorded S3 bucket is missing."
            )
        request = {
            "Bucket": bucket_name,
            "Key": object_key,
            "ChecksumMode": "ENABLED",
        }
        if version_id:
            request["VersionId"] = version_id
        try:
            stored = self.client.head_object(**request)
        except Exception as error:
            error_code = str(
                getattr(error, "response", {}).get("Error", {}).get("Code", "")
            )
            if error_code in {"404", "NoSuchKey", "NoSuchVersion", "NotFound"}:
                raise OriginalFileNotFoundError(
                    "The recorded S3 object was not found."
                ) from error
            raise OriginalFileStorageError(
                "The stored object could not be checked in S3."
            ) from error

        return self._validate_stored_object(
            stored,
            version_id=version_id,
            size_bytes=size_bytes,
            sha256=sha256,
            import_id=import_id,
            data_contract=data_contract,
            row_contract=row_contract,
            expected_kms_key_arn=expected_kms_key_arn,
        )

    def _validate_stored_object(
        self,
        stored,
        *,
        version_id: str | None,
        size_bytes: int,
        sha256: str,
        import_id: str,
        data_contract: str,
        row_contract: str,
        expected_kms_key_arn: str | None = None,
    ) -> StoredOriginalFile:
        try:
            stored_size = int(stored.get("ContentLength", -1))
        except (TypeError, ValueError) as error:
            raise OriginalFileVerificationError(
                "S3 did not report a valid object size."
            ) from error
        if stored_size != size_bytes:
            raise OriginalFileVerificationError(
                "S3 reported an unexpected object size."
            )
        try:
            expected_checksum = base64.b64encode(bytes.fromhex(sha256)).decode(
                "ascii"
            )
        except ValueError as error:
            raise OriginalFileVerificationError(
                "The recorded object checksum is invalid."
            ) from error
        if stored.get("ChecksumSHA256") != expected_checksum:
            raise OriginalFileVerificationError(
                "S3 reported an unexpected object checksum."
            )
        metadata = stored.get("Metadata") or {}
        if metadata.get("sha256") != sha256:
            raise OriginalFileVerificationError(
                "S3 did not retain the file checksum."
            )
        if metadata.get("sales-import-id") != import_id:
            raise OriginalFileVerificationError(
                "S3 did not retain the sales-import reference."
            )
        if metadata.get("data-contract") != data_contract:
            raise OriginalFileVerificationError(
                "S3 did not retain the intake contract."
            )
        if metadata.get("row-contract") != row_contract:
            raise OriginalFileVerificationError(
                "S3 did not retain the sales row contract."
            )
        if stored.get("ServerSideEncryption") != "aws:kms":
            raise OriginalFileVerificationError("S3 did not apply KMS encryption.")
        expected_key = self._required_expected_kms_key_arn(expected_kms_key_arn)
        if stored.get("SSEKMSKeyId") != expected_key:
            raise OriginalFileVerificationError("S3 used an unexpected KMS key.")

        stored_version_id = str(stored.get("VersionId", "")).strip()
        if not stored_version_id:
            raise OriginalFileVerificationError(
                "S3 did not report the stored object version."
            )
        if version_id and stored_version_id != version_id:
            raise OriginalFileVerificationError(
                "S3 reported an unexpected object version."
            )
        return StoredOriginalFile(version_id=stored_version_id)

    def discover_kms_provenance(
        self,
        *,
        bucket_name: str,
        object_key: str,
        version_id: str | None,
        size_bytes: int,
        sha256: str,
        import_id: str,
        data_contract: str,
        row_contract: str,
    ) -> DiscoveredKMSProvenance:
        """Verify a legacy object and return its concrete KMS key and version.

        This deliberately separate API exists only for the controlled migration of
        legacy intake rows that predate stored KMS provenance. Normal reads must
        always provide their already-recorded key ARN.
        """

        if not bucket_name.strip() or not object_key.strip():
            raise OriginalFileKMSProvenanceError(
                "Legacy KMS provenance requires a recorded bucket and object key."
            )
        request = {
            "Bucket": bucket_name,
            "Key": object_key,
            "ChecksumMode": "ENABLED",
        }
        if version_id:
            request["VersionId"] = version_id
        try:
            stored = self.client.head_object(**request)
        except Exception as error:
            error_code = str(
                getattr(error, "response", {}).get("Error", {}).get("Code", "")
            )
            if error_code in {"404", "NoSuchKey", "NoSuchVersion", "NotFound"}:
                raise OriginalFileNotFoundError(
                    "The recorded S3 object version was not found."
                ) from error
            raise OriginalFileStorageError(
                "The stored object could not be checked in S3."
            ) from error

        discovered_key = str(stored.get("SSEKMSKeyId") or "").strip()
        if self._KMS_KEY_ARN_PATTERN.fullmatch(discovered_key) is None:
            raise OriginalFileVerificationError(
                "S3 did not report a concrete KMS key ARN for the exact version."
            )
        verified = self._validate_stored_object(
            stored,
            version_id=version_id,
            size_bytes=size_bytes,
            sha256=sha256,
            import_id=import_id,
            data_contract=data_contract,
            row_contract=row_contract,
            expected_kms_key_arn=discovered_key,
        )
        return DiscoveredKMSProvenance(
            kms_key_arn=discovered_key,
            version_id=verified.version_id,
        )

    @contextmanager
    def open_version(
        self,
        *,
        bucket_name: str,
        object_key: str,
        version_id: str,
        size_bytes: int,
        sha256: str,
        import_id: str,
        data_contract: str,
        row_contract: str,
        expected_kms_key_arn: str | None = None,
        progress_callback: Callable[[int], None] | None = None,
    ) -> Iterator[BinaryIO]:
        """Yield a verified, seekable copy of one exact stored object version.

        ``progress_callback`` receives the cumulative downloaded byte count after
        every accepted chunk has been written. It is optional so existing
        callers keep the same behavior. Callers that perform expensive work in
        the callback should rate-limit that work themselves.
        """

        expected_kms_key_arn = self._required_expected_kms_key_arn(
            expected_kms_key_arn
        )
        if not bucket_name.strip():
            raise OriginalFileVerificationError(
                "The recorded S3 bucket is missing."
            )
        if not object_key.strip():
            raise OriginalFileVerificationError(
                "The recorded S3 object key is missing."
            )
        if not version_id.strip():
            raise OriginalFileVerificationError(
                "The recorded S3 object version is missing."
            )

        try:
            response = self.client.get_object(
                Bucket=bucket_name,
                Key=object_key,
                VersionId=version_id,
                ChecksumMode="ENABLED",
            )
        except Exception as error:
            error_code = str(
                getattr(error, "response", {}).get("Error", {}).get("Code", "")
            )
            if error_code in {"404", "NoSuchKey", "NoSuchVersion", "NotFound"}:
                raise OriginalFileNotFoundError(
                    "The recorded S3 object version was not found."
                ) from error
            raise OriginalFileDownloadError(
                "The stored object could not be downloaded from S3."
            ) from error

        if not hasattr(response, "get"):
            raise OriginalFileVerificationError(
                "S3 returned an invalid object response."
            )
        body = response.get("Body")
        if body is None:
            raise OriginalFileVerificationError(
                "S3 did not return the stored object body."
            )

        downloaded_file = None
        try:
            downloaded_file = tempfile.SpooledTemporaryFile(
                max_size=self._DOWNLOAD_SPOOL_MAX_SIZE,
                mode="w+b",
            )
            self._validate_stored_object(
                response,
                version_id=version_id,
                size_bytes=size_bytes,
                sha256=sha256,
                import_id=import_id,
                data_contract=data_contract,
                row_contract=row_contract,
                expected_kms_key_arn=expected_kms_key_arn,
            )

            downloaded_size = 0
            downloaded_sha256 = hashlib.sha256()
            while True:
                remaining_with_sentinel = max(size_bytes - downloaded_size + 1, 1)
                read_size = min(
                    self._DOWNLOAD_CHUNK_SIZE,
                    remaining_with_sentinel,
                )
                try:
                    chunk = body.read(read_size)
                except Exception as error:
                    raise OriginalFileDownloadError(
                        "The stored object download was interrupted."
                    ) from error
                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    raise OriginalFileDownloadError(
                        "S3 returned an invalid object data stream."
                    )
                downloaded_size += len(chunk)
                if downloaded_size > size_bytes:
                    raise OriginalFileVerificationError(
                        "The downloaded object is larger than the recorded size."
                    )
                downloaded_sha256.update(chunk)
                downloaded_file.write(chunk)
                if progress_callback is not None:
                    progress_callback(downloaded_size)

            if downloaded_size != size_bytes:
                raise OriginalFileVerificationError(
                    "The downloaded object size does not match the recorded size."
                )
            if downloaded_sha256.hexdigest() != sha256:
                raise OriginalFileVerificationError(
                    "The downloaded object checksum does not match the recorded "
                    "checksum."
                )
            downloaded_file.seek(0)
        except Exception:
            if downloaded_file is not None:
                downloaded_file.close()
            raise
        finally:
            try:
                body.close()
            except Exception:
                pass

        assert downloaded_file is not None
        try:
            yield downloaded_file
        finally:
            downloaded_file.close()


@lru_cache(maxsize=1)
def get_sales_import_storage() -> S3OriginalFileStorage:
    return S3OriginalFileStorage()
