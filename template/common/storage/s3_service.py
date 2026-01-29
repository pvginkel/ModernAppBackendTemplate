"""S3-compatible storage service for Ceph RGW or AWS S3."""

import hashlib
from io import BytesIO
from typing import TYPE_CHECKING, Any, BinaryIO

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
    from mypy_boto3_s3.type_defs import CopySourceTypeDef

from common.core.settings import CommonSettings


class S3ServiceError(Exception):
    """Base exception for S3 service errors."""

    pass


class S3ObjectNotFoundError(S3ServiceError):
    """Raised when an object is not found."""

    pass


class S3Service:
    """Service for interacting with S3-compatible storage (Ceph RGW or AWS S3).

    This service is designed to be created per-request (Factory pattern)
    to ensure thread safety with boto3 clients.
    """

    def __init__(self, settings: CommonSettings) -> None:
        """Initialize S3 service with settings.

        Args:
            settings: Application settings containing S3 configuration.
        """
        self._settings = settings
        self._client: "S3Client | None" = None

    @property
    def client(self) -> "S3Client":
        """Get or create the S3 client (lazy initialization)."""
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self._settings.S3_ENDPOINT_URL,
                aws_access_key_id=self._settings.S3_ACCESS_KEY_ID,
                aws_secret_access_key=self._settings.S3_SECRET_ACCESS_KEY,
                region_name=self._settings.S3_REGION,
                use_ssl=self._settings.S3_USE_SSL,
                config=Config(
                    signature_version="s3v4",
                    retries={"max_attempts": 3, "mode": "standard"},
                ),
            )
        return self._client

    @property
    def bucket(self) -> str:
        """Get the configured bucket name."""
        return self._settings.S3_BUCKET_NAME

    def generate_cas_key(self, content: bytes) -> str:
        """Generate a content-addressable storage key based on content hash.

        Args:
            content: The binary content to hash.

        Returns:
            S3 key in the format "cas/{sha256_hash}".
        """
        content_hash = hashlib.sha256(content).hexdigest()
        return f"cas/{content_hash}"

    def upload_file(
        self,
        file_obj: BinaryIO,
        s3_key: str,
        content_type: str | None = None,
    ) -> bool:
        """Upload a file to S3.

        Args:
            file_obj: File-like object to upload.
            s3_key: S3 object key (path).
            content_type: Optional MIME type.

        Returns:
            True if upload was successful.

        Raises:
            S3ServiceError: If upload fails.
        """
        extra_args: dict[str, Any] = {}
        if content_type:
            extra_args["ContentType"] = content_type

        try:
            self.client.upload_fileobj(
                file_obj,
                self.bucket,
                s3_key,
                ExtraArgs=extra_args if extra_args else None,
            )
            return True
        except ClientError as e:
            raise S3ServiceError(f"Failed to upload file: {e}") from e

    def download_file(self, s3_key: str) -> BytesIO:
        """Download a file from S3.

        Args:
            s3_key: S3 object key (path).

        Returns:
            BytesIO object containing the file data.

        Raises:
            S3ObjectNotFoundError: If object doesn't exist.
            S3ServiceError: If download fails.
        """
        try:
            file_obj = BytesIO()
            self.client.download_fileobj(self.bucket, s3_key, file_obj)
            file_obj.seek(0)
            return file_obj
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "NoSuchKey":
                raise S3ObjectNotFoundError(f"Object not found: {s3_key}") from e
            raise S3ServiceError(f"Failed to download file: {e}") from e

    def copy_file(self, source_s3_key: str, target_s3_key: str) -> bool:
        """Copy a file within S3.

        Args:
            source_s3_key: S3 key of the source file.
            target_s3_key: S3 key for the target file.

        Returns:
            True if copy was successful.

        Raises:
            S3ObjectNotFoundError: If source object doesn't exist.
            S3ServiceError: If copy fails.
        """
        try:
            copy_source: "CopySourceTypeDef" = {
                "Bucket": self.bucket,
                "Key": source_s3_key,
            }
            self.client.copy_object(
                CopySource=copy_source,
                Bucket=self.bucket,
                Key=target_s3_key,
            )
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "NoSuchKey":
                raise S3ObjectNotFoundError(
                    f"Source object not found: {source_s3_key}"
                ) from e
            raise S3ServiceError(f"Failed to copy file: {e}") from e

    def delete_file(self, s3_key: str) -> bool:
        """Delete a file from S3.

        Args:
            s3_key: S3 object key (path).

        Returns:
            True if deletion was successful.

        Raises:
            S3ServiceError: If deletion fails.
        """
        try:
            self.client.delete_object(Bucket=self.bucket, Key=s3_key)
            return True
        except ClientError as e:
            raise S3ServiceError(f"Failed to delete file: {e}") from e

    def file_exists(self, s3_key: str) -> bool:
        """Check if a file exists in S3.

        Args:
            s3_key: S3 object key (path).

        Returns:
            True if the object exists, False otherwise.

        Raises:
            S3ServiceError: If the check fails for reasons other than not found.
        """
        try:
            self.client.head_object(Bucket=self.bucket, Key=s3_key)
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "404":
                return False
            raise S3ServiceError(f"Failed to check file existence: {e}") from e

    def get_file_metadata(self, s3_key: str) -> dict[str, Any]:
        """Get metadata for a file in S3.

        Args:
            s3_key: S3 object key (path).

        Returns:
            Dict with content_length, content_type, last_modified, etag.

        Raises:
            S3ObjectNotFoundError: If object doesn't exist.
            S3ServiceError: If operation fails.
        """
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=s3_key)
            return {
                "content_length": response.get("ContentLength"),
                "content_type": response.get("ContentType"),
                "last_modified": response.get("LastModified"),
                "etag": response.get("ETag", "").strip('"'),
            }
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "404":
                raise S3ObjectNotFoundError(f"Object not found: {s3_key}") from e
            raise S3ServiceError(f"Failed to get file metadata: {e}") from e

    def ensure_bucket_exists(self) -> bool:
        """Ensure the configured S3 bucket exists, create if it doesn't.

        Returns:
            True if bucket exists or was created successfully.

        Raises:
            S3ServiceError: If bucket creation fails.
        """
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code == "404":
                try:
                    self.client.create_bucket(Bucket=self.bucket)
                    return True
                except ClientError as create_error:
                    raise S3ServiceError(
                        f"Failed to create bucket: {create_error}"
                    ) from create_error
            else:
                raise S3ServiceError(f"Failed to check bucket: {e}") from e
