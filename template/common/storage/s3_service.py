"""S3-compatible storage service for Ceph RGW or AWS S3."""

import hashlib
from io import BytesIO
from typing import TYPE_CHECKING, Any, BinaryIO

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
    from mypy_boto3_s3.type_defs import CopySourceTypeDef

from common.core.settings import CommonSettings


class S3ServiceError(Exception):
    """Base exception for S3 service errors."""

    pass


class S3ObjectNotFoundError(S3ServiceError):
    """Raised when an S3 object is not found."""

    pass


class S3Service:
    """Service for S3-compatible storage operations using Ceph or AWS S3 backend."""

    def __init__(self, settings: CommonSettings) -> None:
        """Initialize S3 service.

        Args:
            settings: Application settings containing S3 configuration.
        """
        self._s3_client: "S3Client | None" = None
        self.settings = settings

    @property
    def s3_client(self) -> "S3Client":
        """Get or create S3 client with lazy initialization."""
        if self._s3_client is None:
            try:
                self._s3_client = boto3.client(
                    "s3",
                    endpoint_url=self.settings.S3_ENDPOINT_URL,
                    aws_access_key_id=self.settings.S3_ACCESS_KEY_ID,
                    aws_secret_access_key=self.settings.S3_SECRET_ACCESS_KEY,
                    region_name=self.settings.S3_REGION,
                    use_ssl=self.settings.S3_USE_SSL,
                )
            except NoCredentialsError as e:
                raise S3ServiceError(
                    "Failed to initialize S3 client: credentials not configured"
                ) from e
        return self._s3_client

    def compute_hash(self, content: bytes) -> str:
        """Compute SHA-256 hash of content.

        Args:
            content: File content bytes.

        Returns:
            64-character hex SHA-256 hash.
        """
        return hashlib.sha256(content).hexdigest()

    def generate_cas_key(self, content: bytes) -> str:
        """Generate CAS S3 key from content hash.

        Args:
            content: File content bytes.

        Returns:
            CAS S3 key in format: cas/{hash}
        """
        content_hash = self.compute_hash(content)
        return f"cas/{content_hash}"

    def upload_file(
        self,
        file_obj: BinaryIO,
        s3_key: str,
        content_type: str | None = None,
    ) -> bool:
        """Upload file to S3.

        Args:
            file_obj: File object to upload.
            s3_key: S3 key for the file.
            content_type: MIME type of the file.

        Returns:
            True if upload successful.

        Raises:
            S3ServiceError: If upload fails.
        """
        try:
            extra_args = {"ContentType": content_type} if content_type else None
            if extra_args is not None:
                self.s3_client.upload_fileobj(
                    file_obj,
                    self.settings.S3_BUCKET_NAME,
                    s3_key,
                    ExtraArgs=extra_args,
                )
            else:
                self.s3_client.upload_fileobj(
                    file_obj,
                    self.settings.S3_BUCKET_NAME,
                    s3_key,
                )
            return True

        except ClientError as e:
            raise S3ServiceError(f"Failed to upload file to S3: {e}") from e

    def download_file(self, s3_key: str) -> BytesIO:
        """Download file from S3.

        Args:
            s3_key: S3 key of the file.

        Returns:
            BytesIO object containing the file data.

        Raises:
            S3ObjectNotFoundError: If file not found.
            S3ServiceError: If download fails.
        """
        try:
            file_obj = BytesIO()
            self.s3_client.download_fileobj(
                self.settings.S3_BUCKET_NAME, s3_key, file_obj
            )
            file_obj.seek(0)
            return file_obj

        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                raise S3ObjectNotFoundError(
                    f"File not found in S3: {s3_key}"
                ) from e
            raise S3ServiceError(f"Failed to download file from S3: {e}") from e

    def copy_file(self, source_s3_key: str, target_s3_key: str) -> bool:
        """Copy file within S3.

        Args:
            source_s3_key: S3 key of the source file.
            target_s3_key: S3 key for the target file.

        Returns:
            True if copy successful.

        Raises:
            S3ObjectNotFoundError: If source file not found.
            S3ServiceError: If copy fails.
        """
        try:
            copy_source: "CopySourceTypeDef" = {
                "Bucket": self.settings.S3_BUCKET_NAME,
                "Key": source_s3_key,
            }

            self.s3_client.copy_object(
                CopySource=copy_source,
                Bucket=self.settings.S3_BUCKET_NAME,
                Key=target_s3_key,
            )
            return True

        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                raise S3ObjectNotFoundError(
                    f"Source file not found in S3: {source_s3_key}"
                ) from e
            raise S3ServiceError(f"Failed to copy file in S3: {e}") from e

    def delete_file(self, s3_key: str) -> bool:
        """Delete file from S3.

        Args:
            s3_key: S3 key of the file to delete.

        Returns:
            True if deletion successful.

        Raises:
            S3ServiceError: If deletion fails.
        """
        try:
            self.s3_client.delete_object(
                Bucket=self.settings.S3_BUCKET_NAME, Key=s3_key
            )
            return True

        except ClientError as e:
            raise S3ServiceError(f"Failed to delete file from S3: {e}") from e

    def file_exists(self, s3_key: str) -> bool:
        """Check if file exists in S3.

        Args:
            s3_key: S3 key of the file.

        Returns:
            True if file exists, False otherwise.

        Raises:
            S3ServiceError: If check fails for reasons other than not found.
        """
        try:
            self.s3_client.head_object(
                Bucket=self.settings.S3_BUCKET_NAME, Key=s3_key
            )
            return True

        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise S3ServiceError(f"Failed to check file existence in S3: {e}") from e

    def get_file_metadata(self, s3_key: str) -> dict[str, Any]:
        """Get file metadata from S3.

        Args:
            s3_key: S3 key of the file.

        Returns:
            Dictionary containing file metadata:
            - content_length: File size in bytes
            - content_type: MIME type
            - last_modified: Last modified datetime
            - etag: Entity tag (without quotes)

        Raises:
            S3ObjectNotFoundError: If file not found.
            S3ServiceError: If metadata retrieval fails.
        """
        try:
            response = self.s3_client.head_object(
                Bucket=self.settings.S3_BUCKET_NAME, Key=s3_key
            )

            return {
                "content_length": response.get("ContentLength"),
                "content_type": response.get("ContentType"),
                "last_modified": response.get("LastModified"),
                "etag": response.get("ETag", "").strip('"'),
            }

        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                raise S3ObjectNotFoundError(
                    f"File not found in S3: {s3_key}"
                ) from e
            raise S3ServiceError(f"Failed to get file metadata from S3: {e}") from e

    def ensure_bucket_exists(self) -> bool:
        """Ensure the configured S3 bucket exists, create if it doesn't.

        Returns:
            True if bucket exists or was created successfully.

        Raises:
            S3ServiceError: If bucket creation fails.
        """
        try:
            self.s3_client.head_bucket(Bucket=self.settings.S3_BUCKET_NAME)
            return True

        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                try:
                    self.s3_client.create_bucket(Bucket=self.settings.S3_BUCKET_NAME)
                    return True
                except ClientError as create_error:
                    raise S3ServiceError(
                        f"Failed to create S3 bucket {self.settings.S3_BUCKET_NAME}: "
                        f"{create_error}"
                    ) from create_error
            else:
                raise S3ServiceError(f"Failed to check S3 bucket existence: {e}") from e
