"""S3-compatible storage service for Ceph RGW or AWS S3."""

from io import BytesIO
from typing import TYPE_CHECKING, Any, BinaryIO

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

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

    def upload_file(
        self,
        file_obj: BinaryIO,
        key: str,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Upload a file to S3.

        Args:
            file_obj: File-like object to upload.
            key: S3 object key (path).
            content_type: Optional MIME type.
            metadata: Optional metadata dict.

        Returns:
            The S3 key of the uploaded object.

        Raises:
            S3ServiceError: If upload fails.
        """
        extra_args: dict[str, Any] = {}
        if content_type:
            extra_args["ContentType"] = content_type
        if metadata:
            extra_args["Metadata"] = metadata

        try:
            self.client.upload_fileobj(
                file_obj,
                self.bucket,
                key,
                ExtraArgs=extra_args if extra_args else None,
            )
            return key
        except ClientError as e:
            raise S3ServiceError(f"Failed to upload file: {e}") from e

    def upload_bytes(
        self,
        data: bytes,
        key: str,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Upload bytes to S3.

        Args:
            data: Bytes to upload.
            key: S3 object key (path).
            content_type: Optional MIME type.
            metadata: Optional metadata dict.

        Returns:
            The S3 key of the uploaded object.
        """
        return self.upload_file(BytesIO(data), key, content_type, metadata)

    def download_file(self, key: str) -> bytes:
        """Download a file from S3.

        Args:
            key: S3 object key (path).

        Returns:
            File contents as bytes.

        Raises:
            S3ObjectNotFoundError: If object doesn't exist.
            S3ServiceError: If download fails.
        """
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "NoSuchKey":
                raise S3ObjectNotFoundError(f"Object not found: {key}") from e
            raise S3ServiceError(f"Failed to download file: {e}") from e

    def download_to_file(self, key: str, file_obj: BinaryIO) -> None:
        """Download a file from S3 to a file-like object.

        Args:
            key: S3 object key (path).
            file_obj: File-like object to write to.

        Raises:
            S3ObjectNotFoundError: If object doesn't exist.
            S3ServiceError: If download fails.
        """
        try:
            self.client.download_fileobj(self.bucket, key, file_obj)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "NoSuchKey":
                raise S3ObjectNotFoundError(f"Object not found: {key}") from e
            raise S3ServiceError(f"Failed to download file: {e}") from e

    def delete_file(self, key: str) -> None:
        """Delete a file from S3.

        Args:
            key: S3 object key (path).

        Raises:
            S3ServiceError: If deletion fails.
        """
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            raise S3ServiceError(f"Failed to delete file: {e}") from e

    def file_exists(self, key: str) -> bool:
        """Check if a file exists in S3.

        Args:
            key: S3 object key (path).

        Returns:
            True if the object exists, False otherwise.
        """
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "404":
                return False
            raise S3ServiceError(f"Failed to check file existence: {e}") from e

    def get_file_metadata(self, key: str) -> dict[str, Any]:
        """Get metadata for a file in S3.

        Args:
            key: S3 object key (path).

        Returns:
            Dict with ContentLength, ContentType, LastModified, Metadata, etc.

        Raises:
            S3ObjectNotFoundError: If object doesn't exist.
            S3ServiceError: If operation fails.
        """
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
            return {
                "content_length": response.get("ContentLength"),
                "content_type": response.get("ContentType"),
                "last_modified": response.get("LastModified"),
                "metadata": response.get("Metadata", {}),
                "etag": response.get("ETag"),
            }
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "404":
                raise S3ObjectNotFoundError(f"Object not found: {key}") from e
            raise S3ServiceError(f"Failed to get file metadata: {e}") from e

    def list_files(
        self, prefix: str = "", max_keys: int = 1000
    ) -> list[dict[str, Any]]:
        """List files in S3 bucket with optional prefix.

        Args:
            prefix: Optional prefix to filter objects.
            max_keys: Maximum number of keys to return.

        Returns:
            List of dicts with Key, Size, LastModified for each object.
        """
        try:
            response = self.client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=prefix,
                MaxKeys=max_keys,
            )
            return [
                {
                    "key": obj["Key"],
                    "size": obj["Size"],
                    "last_modified": obj["LastModified"],
                }
                for obj in response.get("Contents", [])
            ]
        except ClientError as e:
            raise S3ServiceError(f"Failed to list files: {e}") from e

    def generate_presigned_url(
        self,
        key: str,
        expiration: int = 3600,
        http_method: str = "GET",
    ) -> str:
        """Generate a presigned URL for an S3 object.

        Args:
            key: S3 object key (path).
            expiration: URL expiration time in seconds (default 1 hour).
            http_method: HTTP method (GET for download, PUT for upload).

        Returns:
            Presigned URL string.

        Raises:
            S3ServiceError: If URL generation fails.
        """
        client_method = "get_object" if http_method == "GET" else "put_object"
        try:
            url: str = self.client.generate_presigned_url(
                ClientMethod=client_method,
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expiration,
            )
            return url
        except ClientError as e:
            raise S3ServiceError(f"Failed to generate presigned URL: {e}") from e

    def ensure_bucket_exists(self) -> None:
        """Create the bucket if it doesn't exist.

        Raises:
            S3ServiceError: If bucket creation fails.
        """
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code == "404":
                try:
                    self.client.create_bucket(Bucket=self.bucket)
                except ClientError as create_error:
                    raise S3ServiceError(
                        f"Failed to create bucket: {create_error}"
                    ) from create_error
            else:
                raise S3ServiceError(f"Failed to check bucket: {e}") from e
