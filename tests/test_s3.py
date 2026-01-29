"""Tests for S3 storage service."""

import io
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, NoCredentialsError


class TestS3Service:
    """Tests for S3Service functionality."""

    def test_init_creates_client(self, test_settings: Any) -> None:
        """Test that S3Service initializes with boto3 client."""
        from common.storage.s3_service import S3Service

        with patch("boto3.client") as mock_boto3:
            service = S3Service(test_settings)
            # Access the s3_client property to trigger boto3.client call
            _ = service.s3_client

            mock_boto3.assert_called_once()
            call_args = mock_boto3.call_args[1]
            assert call_args["endpoint_url"] is not None
            assert call_args["aws_access_key_id"] is not None
            assert call_args["aws_secret_access_key"] is not None

    def test_compute_hash(self, test_settings: Any) -> None:
        """Test SHA-256 hash computation."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()

        with patch("boto3.client", return_value=mock_client):
            service = S3Service(test_settings)

            content = b"test content"
            hash_result = service.compute_hash(content)

            # Known SHA-256 hash of "test content"
            expected = "6ae8a75555209fd6c44157c0aed8016e763ff435a19cf186f76863140143ff72"
            assert hash_result == expected

    def test_generate_cas_key(self, test_settings: Any) -> None:
        """Test CAS key generation."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()

        with patch("boto3.client", return_value=mock_client):
            service = S3Service(test_settings)

            content = b"test content"
            cas_key = service.generate_cas_key(content)

            assert cas_key.startswith("cas/")
            assert len(cas_key) == 68  # "cas/" + 64 char hash

    def test_upload_file_success(self, test_settings: Any) -> None:
        """Test successful file upload."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()

        with patch("boto3.client", return_value=mock_client):
            service = S3Service(test_settings)

            file_obj = io.BytesIO(b"test content")
            result = service.upload_file(
                file_obj, "test/path/file.txt", content_type="text/plain"
            )

            assert result is True
            mock_client.upload_fileobj.assert_called_once()
            args, kwargs = mock_client.upload_fileobj.call_args
            assert args[0] == file_obj
            assert args[1] == test_settings.S3_BUCKET_NAME
            assert args[2] == "test/path/file.txt"
            assert kwargs["ExtraArgs"]["ContentType"] == "text/plain"

    def test_upload_file_no_content_type(self, test_settings: Any) -> None:
        """Test upload without explicit content type."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()

        with patch("boto3.client", return_value=mock_client):
            service = S3Service(test_settings)

            file_obj = io.BytesIO(b"test content")
            service.upload_file(file_obj, "test/key.bin")

            args, kwargs = mock_client.upload_fileobj.call_args
            assert "ExtraArgs" not in kwargs or kwargs.get("ExtraArgs") is None

    def test_upload_file_client_error(self, test_settings: Any) -> None:
        """Test file upload with client error."""
        from common.storage.s3_service import S3Service, S3ServiceError

        mock_client = MagicMock()
        mock_client.upload_fileobj.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Access denied"}},
            "PutObject",
        )

        with patch("boto3.client", return_value=mock_client):
            service = S3Service(test_settings)

            file_obj = io.BytesIO(b"test content")
            with pytest.raises(S3ServiceError) as exc_info:
                service.upload_file(file_obj, "test/key", "text/plain")

            assert "upload file to s3" in str(exc_info.value).lower()

    def test_upload_file_no_credentials(self, test_settings: Any) -> None:
        """Test file upload with missing credentials."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()
        mock_client.upload_fileobj.side_effect = NoCredentialsError()

        with patch("boto3.client", return_value=mock_client):
            service = S3Service(test_settings)

            file_obj = io.BytesIO(b"test content")
            # NoCredentialsError is not caught by the service, so it bubbles up
            with pytest.raises(NoCredentialsError):
                service.upload_file(file_obj, "test/key")

    def test_download_file_success(self, test_settings: Any) -> None:
        """Test successful file download."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()
        test_content = b"downloaded content"

        def mock_download(bucket: str, key: str, file_obj: io.BytesIO) -> None:
            file_obj.write(test_content)

        mock_client.download_fileobj.side_effect = mock_download

        with patch("boto3.client", return_value=mock_client):
            service = S3Service(test_settings)

            result = service.download_file("test/file.txt")

            assert isinstance(result, io.BytesIO)
            assert result.read() == test_content
            mock_client.download_fileobj.assert_called_once()

    def test_download_file_not_found(self, test_settings: Any) -> None:
        """Test downloading a nonexistent file raises S3ObjectNotFoundError."""
        from common.storage.s3_service import S3ObjectNotFoundError, S3Service

        mock_client = MagicMock()
        mock_client.download_fileobj.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not found"}}, "DownloadFileobj"
        )

        with patch("boto3.client", return_value=mock_client):
            service = S3Service(test_settings)

            with pytest.raises(S3ObjectNotFoundError):
                service.download_file("nonexistent.txt")

    def test_copy_file_success(self, test_settings: Any) -> None:
        """Test copying a file within S3."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()
        mock_client.copy_object.return_value = {}

        with patch("boto3.client", return_value=mock_client):
            service = S3Service(test_settings)

            result = service.copy_file("source/file.txt", "target/file.txt")

            assert result is True
            mock_client.copy_object.assert_called_once_with(
                CopySource={
                    "Bucket": test_settings.S3_BUCKET_NAME,
                    "Key": "source/file.txt",
                },
                Bucket=test_settings.S3_BUCKET_NAME,
                Key="target/file.txt",
            )

    def test_copy_file_source_not_found(self, test_settings: Any) -> None:
        """Test copying a nonexistent file raises S3ObjectNotFoundError."""
        from common.storage.s3_service import S3ObjectNotFoundError, S3Service

        mock_client = MagicMock()
        mock_client.copy_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not found"}}, "CopyObject"
        )

        with patch("boto3.client", return_value=mock_client):
            service = S3Service(test_settings)

            with pytest.raises(S3ObjectNotFoundError):
                service.copy_file("nonexistent.txt", "target.txt")

    def test_delete_file_success(self, test_settings: Any) -> None:
        """Test successful file deletion."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()
        mock_client.delete_object.return_value = {"DeleteMarker": True}

        with patch("boto3.client", return_value=mock_client):
            service = S3Service(test_settings)

            result = service.delete_file("test/file.txt")

            assert result is True
            mock_client.delete_object.assert_called_once_with(
                Bucket=test_settings.S3_BUCKET_NAME, Key="test/file.txt"
            )

    def test_delete_file_error(self, test_settings: Any) -> None:
        """Test deletion with error."""
        from common.storage.s3_service import S3Service, S3ServiceError

        mock_client = MagicMock()
        mock_client.delete_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Access denied"}},
            "DeleteObject",
        )

        with patch("boto3.client", return_value=mock_client):
            service = S3Service(test_settings)

            with pytest.raises(S3ServiceError):
                service.delete_file("test/key")

    def test_file_exists_true(self, test_settings: Any) -> None:
        """Test checking if a file exists (true case)."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()
        mock_client.head_object.return_value = {"ContentLength": 1024}

        with patch("boto3.client", return_value=mock_client):
            service = S3Service(test_settings)

            exists = service.file_exists("test/file.txt")

            assert exists is True
            mock_client.head_object.assert_called_once_with(
                Bucket=test_settings.S3_BUCKET_NAME, Key="test/file.txt"
            )

    def test_file_exists_false(self, test_settings: Any) -> None:
        """Test checking if a file exists (false case)."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()
        mock_client.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not found"}}, "HeadObject"
        )

        with patch("boto3.client", return_value=mock_client):
            service = S3Service(test_settings)

            exists = service.file_exists("nonexistent.txt")

            assert exists is False

    def test_file_exists_other_error(self, test_settings: Any) -> None:
        """Test file existence check with unexpected error."""
        from common.storage.s3_service import S3Service, S3ServiceError

        mock_client = MagicMock()
        mock_client.head_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Access denied"}},
            "HeadObject",
        )

        with patch("boto3.client", return_value=mock_client):
            service = S3Service(test_settings)

            with pytest.raises(S3ServiceError):
                service.file_exists("test/key.txt")

    def test_get_file_metadata_success(self, test_settings: Any) -> None:
        """Test getting file metadata from S3."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()
        last_modified = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        mock_client.head_object.return_value = {
            "ContentLength": 1024,
            "ContentType": "text/plain",
            "LastModified": last_modified,
            "ETag": '"abc123"',
        }

        with patch("boto3.client", return_value=mock_client):
            service = S3Service(test_settings)

            metadata = service.get_file_metadata("test/file.txt")

            assert metadata["content_length"] == 1024
            assert metadata["content_type"] == "text/plain"
            assert metadata["last_modified"] == last_modified
            assert metadata["etag"] == "abc123"

    def test_get_file_metadata_not_found(self, test_settings: Any) -> None:
        """Test getting metadata for nonexistent file raises S3ObjectNotFoundError."""
        from common.storage.s3_service import S3ObjectNotFoundError, S3Service

        mock_client = MagicMock()
        mock_client.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not found"}}, "HeadObject"
        )

        with patch("boto3.client", return_value=mock_client):
            service = S3Service(test_settings)

            with pytest.raises(S3ObjectNotFoundError):
                service.get_file_metadata("nonexistent.txt")

    def test_ensure_bucket_exists_creates_if_missing(self, test_settings: Any) -> None:
        """Test ensure_bucket_exists creates bucket if it doesn't exist."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()
        mock_client.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not found"}}, "HeadBucket"
        )

        with patch("boto3.client", return_value=mock_client):
            service = S3Service(test_settings)

            result = service.ensure_bucket_exists()

            assert result is True
            mock_client.create_bucket.assert_called_once_with(
                Bucket=test_settings.S3_BUCKET_NAME
            )

    def test_ensure_bucket_exists_no_op_if_exists(self, test_settings: Any) -> None:
        """Test ensure_bucket_exists does nothing if bucket exists."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()
        mock_client.head_bucket.return_value = {}

        with patch("boto3.client", return_value=mock_client):
            service = S3Service(test_settings)

            result = service.ensure_bucket_exists()

            assert result is True
            mock_client.create_bucket.assert_not_called()


class TestS3HealthCheck:
    """Tests for S3 health check."""

    def test_check_s3_health_healthy(self, test_settings: Any) -> None:
        """Test S3 health check when healthy."""
        from common.storage.health import check_s3_health
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()
        mock_client.head_bucket.return_value = {}

        with patch("boto3.client", return_value=mock_client):
            s3_service = S3Service(test_settings)

            healthy, message = check_s3_health(s3_service)

            assert healthy is True
            assert "accessible" in message

    def test_check_s3_health_bucket_not_found(self, test_settings: Any) -> None:
        """Test S3 health check when bucket not found."""
        from common.storage.health import check_s3_health
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()
        mock_client.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not found"}}, "HeadBucket"
        )

        with patch("boto3.client", return_value=mock_client):
            s3_service = S3Service(test_settings)

            healthy, message = check_s3_health(s3_service)

            assert healthy is False
            assert "not found" in message

    def test_check_s3_health_access_denied(self, test_settings: Any) -> None:
        """Test S3 health check when access denied."""
        from common.storage.health import check_s3_health
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()
        mock_client.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadBucket"
        )

        with patch("boto3.client", return_value=mock_client):
            s3_service = S3Service(test_settings)

            healthy, message = check_s3_health(s3_service)

            assert healthy is False
            assert "access denied" in message
