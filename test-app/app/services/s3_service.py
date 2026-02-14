"""S3 service for file storage operations."""

import hashlib
from io import BytesIO
from typing import Any, BinaryIO

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from mypy_boto3_s3.client import S3Client
from mypy_boto3_s3.type_defs import CopySourceTypeDef

from app.config import Settings
from app.exceptions import InvalidOperationException


class S3Service:
    """Service for S3-compatible storage operations using Ceph backend."""

    def __init__(self, settings: Settings):
        """Initialize S3 service.

        Args:
            settings: Application settings
        """
        self._s3_client: S3Client | None = None
        self.settings = settings

    @property
    def s3_client(self) -> S3Client:
        """Get or create S3 client with lazy initialization."""
        if self._s3_client is None:
            try:
                self._s3_client = boto3.client(
                    's3',
                    endpoint_url=self.settings.s3_endpoint_url,
                    aws_access_key_id=self.settings.s3_access_key_id,
                    aws_secret_access_key=self.settings.s3_secret_access_key,
                    region_name=self.settings.s3_region,
                    use_ssl=self.settings.s3_use_ssl
                )
            except NoCredentialsError as e:
                raise InvalidOperationException("initialize S3 client", "credentials not configured") from e
        return self._s3_client

    def compute_hash(self, content: bytes) -> str:
        """Compute SHA-256 hash of content.

        Args:
            content: File content bytes

        Returns:
            64-character hex SHA-256 hash
        """
        return hashlib.sha256(content).hexdigest()

    def generate_cas_key(self, content: bytes) -> str:
        """Generate CAS S3 key from content hash.

        Args:
            content: File content bytes

        Returns:
            CAS S3 key in format: cas/{hash}
        """
        content_hash = self.compute_hash(content)
        return f"cas/{content_hash}"

    def upload_file(self, file_obj: BinaryIO, s3_key: str, content_type: str | None = None) -> bool:
        """Upload file to S3.

        Args:
            file_obj: File object to upload
            s3_key: S3 key for the file
            content_type: MIME type of the file

        Returns:
            True if upload successful

        Raises:
            InvalidOperationException: If upload fails
        """
        try:
            extra_args = {"ContentType": content_type} if content_type else None
            if extra_args is not None:
                self.s3_client.upload_fileobj(
                    file_obj,
                    self.settings.s3_bucket_name,
                    s3_key,
                    ExtraArgs=extra_args,
                )
            else:
                self.s3_client.upload_fileobj(
                    file_obj,
                    self.settings.s3_bucket_name,
                    s3_key,
                )
            return True

        except ClientError as e:
            raise InvalidOperationException("upload file to S3", str(e)) from e

    def download_file(self, s3_key: str) -> BytesIO:
        """Download file from S3.

        Args:
            s3_key: S3 key of the file

        Returns:
            BytesIO object containing the file data

        Raises:
            InvalidOperationException: If download fails
        """
        try:
            file_obj = BytesIO()
            self.s3_client.download_fileobj(
                self.settings.s3_bucket_name,
                s3_key,
                file_obj
            )
            file_obj.seek(0)
            return file_obj

        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                raise InvalidOperationException("download file from S3", f"file not found: {s3_key}") from e
            raise InvalidOperationException("download file from S3", str(e)) from e

    def copy_file(self, source_s3_key: str, target_s3_key: str) -> bool:
        """Copy file within S3.

        Args:
            source_s3_key: S3 key of the source file
            target_s3_key: S3 key for the target file

        Returns:
            True if copy successful

        Raises:
            InvalidOperationException: If copy fails
        """
        try:
            copy_source: CopySourceTypeDef = {
                'Bucket': self.settings.s3_bucket_name,
                'Key': source_s3_key
            }

            self.s3_client.copy_object(
                CopySource=copy_source,
                Bucket=self.settings.s3_bucket_name,
                Key=target_s3_key
            )
            return True

        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                raise InvalidOperationException("copy file in S3", f"source file not found: {source_s3_key}") from e
            raise InvalidOperationException("copy file in S3", str(e)) from e

    def delete_file(self, s3_key: str) -> bool:
        """Delete file from S3.

        Args:
            s3_key: S3 key of the file to delete

        Returns:
            True if deletion successful

        Raises:
            InvalidOperationException: If deletion fails
        """
        try:
            self.s3_client.delete_object(
                Bucket=self.settings.s3_bucket_name,
                Key=s3_key
            )
            return True

        except ClientError as e:
            raise InvalidOperationException("delete file from S3", str(e)) from e

    def file_exists(self, s3_key: str) -> bool:
        """Check if file exists in S3.

        Args:
            s3_key: S3 key of the file

        Returns:
            True if file exists, False otherwise
        """
        try:
            self.s3_client.head_object(
                Bucket=self.settings.s3_bucket_name,
                Key=s3_key
            )
            return True

        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            raise InvalidOperationException("check file existence in S3", str(e)) from e

    def get_file_metadata(self, s3_key: str) -> dict[str, Any]:
        """Get file metadata from S3.

        Args:
            s3_key: S3 key of the file

        Returns:
            Dictionary containing file metadata

        Raises:
            InvalidOperationException: If metadata retrieval fails
        """
        try:
            response = self.s3_client.head_object(
                Bucket=self.settings.s3_bucket_name,
                Key=s3_key
            )

            return {
                'content_length': response.get('ContentLength'),
                'content_type': response.get('ContentType'),
                'last_modified': response.get('LastModified'),
                'etag': response.get('ETag', '').strip('"')
            }

        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                raise InvalidOperationException("get file metadata from S3", f"file not found: {s3_key}") from e
            raise InvalidOperationException("get file metadata from S3", str(e)) from e

    def ensure_bucket_exists(self) -> bool:
        """Ensure the configured S3 bucket exists, create if it doesn't.

        Returns:
            True if bucket exists or was created successfully

        Raises:
            InvalidOperationException: If bucket creation fails
        """
        try:
            # Check if bucket exists
            self.s3_client.head_bucket(Bucket=self.settings.s3_bucket_name)
            return True

        except ClientError as e:
            # If bucket doesn't exist (404), create it
            if e.response['Error']['Code'] == '404':
                try:
                    # Create bucket without location constraint for compatibility
                    self.s3_client.create_bucket(Bucket=self.settings.s3_bucket_name)
                    return True
                except ClientError as create_error:
                    raise InvalidOperationException(
                        "create S3 bucket",
                        f"failed to create bucket {self.settings.s3_bucket_name}: {create_error}"
                    ) from create_error
            else:
                # Other errors (permissions, etc.)
                raise InvalidOperationException("check S3 bucket existence", str(e)) from e
