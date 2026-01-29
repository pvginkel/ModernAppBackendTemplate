"""Tests for S3 storage service."""

from datetime import datetime, timezone
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError


class TestS3Service:
    """Tests for S3Service functionality."""

    def test_upload_file(self, test_settings: Any) -> None:
        """Test uploading a file to S3."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()

        with patch("boto3.client", return_value=mock_client):
            s3_service = S3Service(test_settings)

            file_obj = BytesIO(b"test content")
            result = s3_service.upload_file(
                file_obj, "test/path/file.txt", content_type="text/plain"
            )

            assert result is True
            mock_client.upload_fileobj.assert_called_once()

    def test_download_file(self, test_settings: Any) -> None:
        """Test downloading a file from S3."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()

        def mock_download(bucket: str, key: str, file_obj: BytesIO) -> None:
            file_obj.write(b"downloaded content")

        mock_client.download_fileobj.side_effect = mock_download

        with patch("boto3.client", return_value=mock_client):
            s3_service = S3Service(test_settings)

            result = s3_service.download_file("test/file.txt")

            assert isinstance(result, BytesIO)
            assert result.read() == b"downloaded content"
            mock_client.download_fileobj.assert_called_once()

    def test_download_file_not_found(self, test_settings: Any) -> None:
        """Test downloading a nonexistent file raises S3ObjectNotFoundError."""
        from common.storage.s3_service import S3ObjectNotFoundError, S3Service

        mock_client = MagicMock()
        mock_client.download_fileobj.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not found"}}, "DownloadFileobj"
        )

        with patch("boto3.client", return_value=mock_client):
            s3_service = S3Service(test_settings)

            with pytest.raises(S3ObjectNotFoundError):
                s3_service.download_file("nonexistent.txt")

    def test_copy_file_success(self, test_settings: Any) -> None:
        """Test copying a file within S3."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()
        mock_client.copy_object.return_value = {}

        with patch("boto3.client", return_value=mock_client):
            s3_service = S3Service(test_settings)

            result = s3_service.copy_file("source/file.txt", "target/file.txt")

            assert result is True
            mock_client.copy_object.assert_called_once_with(
                CopySource={"Bucket": test_settings.S3_BUCKET_NAME, "Key": "source/file.txt"},
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
            s3_service = S3Service(test_settings)

            with pytest.raises(S3ObjectNotFoundError):
                s3_service.copy_file("nonexistent.txt", "target.txt")

    def test_delete_file(self, test_settings: Any) -> None:
        """Test deleting a file from S3."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()

        with patch("boto3.client", return_value=mock_client):
            s3_service = S3Service(test_settings)

            result = s3_service.delete_file("test/file.txt")

            assert result is True
            mock_client.delete_object.assert_called_once_with(
                Bucket=test_settings.S3_BUCKET_NAME, Key="test/file.txt"
            )

    def test_file_exists_true(self, test_settings: Any) -> None:
        """Test checking if a file exists (true case)."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()
        mock_client.head_object.return_value = {}

        with patch("boto3.client", return_value=mock_client):
            s3_service = S3Service(test_settings)

            exists = s3_service.file_exists("test/file.txt")

            assert exists is True

    def test_file_exists_false(self, test_settings: Any) -> None:
        """Test checking if a file exists (false case)."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()
        mock_client.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not found"}}, "HeadObject"
        )

        with patch("boto3.client", return_value=mock_client):
            s3_service = S3Service(test_settings)

            exists = s3_service.file_exists("nonexistent.txt")

            assert exists is False

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
            s3_service = S3Service(test_settings)

            metadata = s3_service.get_file_metadata("test/file.txt")

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
            s3_service = S3Service(test_settings)

            with pytest.raises(S3ObjectNotFoundError):
                s3_service.get_file_metadata("nonexistent.txt")

    def test_ensure_bucket_exists_creates_if_missing(self, test_settings: Any) -> None:
        """Test ensure_bucket_exists creates bucket if it doesn't exist."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()
        mock_client.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not found"}}, "HeadBucket"
        )

        with patch("boto3.client", return_value=mock_client):
            s3_service = S3Service(test_settings)

            result = s3_service.ensure_bucket_exists()

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
            s3_service = S3Service(test_settings)

            result = s3_service.ensure_bucket_exists()

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
