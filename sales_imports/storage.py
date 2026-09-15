import base64
from dataclasses import dataclass
from functools import lru_cache

from django.conf import settings


class OriginalFileStorageError(Exception):
    """Raised when the original file cannot be stored and verified safely."""


class OriginalFileNotFoundError(OriginalFileStorageError):
    """Raised when a recorded original object or version is absent."""


class OriginalFileVerificationError(OriginalFileStorageError):
    """Raised when stored metadata does not match the intake record."""


class OriginalFileUploadError(OriginalFileStorageError):
    """Raised when S3 rejects the original-file write itself."""


@dataclass(frozen=True)
class StoredOriginalFile:
    version_id: str


class S3OriginalFileStorage:
    """Store original imports in the private, versioned S3 bucket."""

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
    ) -> StoredOriginalFile:
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

        if int(stored.get("ContentLength", -1)) != size_bytes:
            raise OriginalFileVerificationError(
                "S3 reported an unexpected object size."
            )
        expected_checksum = base64.b64encode(bytes.fromhex(sha256)).decode("ascii")
        if stored.get("ChecksumSHA256") != expected_checksum:
            raise OriginalFileVerificationError(
                "S3 reported an unexpected object checksum."
            )
        metadata = stored.get("Metadata", {})
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
        if stored.get("SSEKMSKeyId") != self.kms_key_arn:
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


@lru_cache(maxsize=1)
def get_sales_import_storage() -> S3OriginalFileStorage:
    return S3OriginalFileStorage()
