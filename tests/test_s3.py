"""Tests for S3 storage service."""

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
            key = s3_service.upload_file(
                file_obj, "test/path/file.txt", content_type="text/plain"
            )

            assert key == "test/path/file.txt"
            mock_client.upload_fileobj.assert_called_once()

    def test_upload_bytes(self, test_settings: Any) -> None:
        """Test uploading bytes to S3."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()

        with patch("boto3.client", return_value=mock_client):
            s3_service = S3Service(test_settings)

            key = s3_service.upload_bytes(
                b"test content", "test/file.txt", content_type="text/plain"
            )

            assert key == "test/file.txt"
            mock_client.upload_fileobj.assert_called_once()

    def test_download_file(self, test_settings: Any) -> None:
        """Test downloading a file from S3."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()
        mock_body = MagicMock()
        mock_body.read.return_value = b"downloaded content"
        mock_client.get_object.return_value = {"Body": mock_body}

        with patch("boto3.client", return_value=mock_client):
            s3_service = S3Service(test_settings)

            content = s3_service.download_file("test/file.txt")

            assert content == b"downloaded content"
            mock_client.get_object.assert_called_once_with(
                Bucket=test_settings.S3_BUCKET_NAME, Key="test/file.txt"
            )

    def test_download_file_not_found(self, test_settings: Any) -> None:
        """Test downloading a nonexistent file raises S3ObjectNotFoundError."""
        from common.storage.s3_service import S3Service, S3ObjectNotFoundError

        mock_client = MagicMock()
        mock_client.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not found"}},
            "GetObject"
        )

        with patch("boto3.client", return_value=mock_client):
            s3_service = S3Service(test_settings)

            with pytest.raises(S3ObjectNotFoundError):
                s3_service.download_file("nonexistent.txt")

    def test_delete_file(self, test_settings: Any) -> None:
        """Test deleting a file from S3."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()

        with patch("boto3.client", return_value=mock_client):
            s3_service = S3Service(test_settings)

            s3_service.delete_file("test/file.txt")

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
            {"Error": {"Code": "404", "Message": "Not found"}},
            "HeadObject"
        )

        with patch("boto3.client", return_value=mock_client):
            s3_service = S3Service(test_settings)

            exists = s3_service.file_exists("nonexistent.txt")

            assert exists is False

    def test_list_files(self, test_settings: Any) -> None:
        """Test listing files in S3."""
        from common.storage.s3_service import S3Service
        from datetime import datetime

        mock_client = MagicMock()
        mock_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "file1.txt", "Size": 100, "LastModified": datetime.now()},
                {"Key": "file2.txt", "Size": 200, "LastModified": datetime.now()},
            ]
        }

        with patch("boto3.client", return_value=mock_client):
            s3_service = S3Service(test_settings)

            files = s3_service.list_files(prefix="test/")

            assert len(files) == 2
            assert files[0]["key"] == "file1.txt"
            assert files[1]["key"] == "file2.txt"

    def test_generate_presigned_url(self, test_settings: Any) -> None:
        """Test generating a presigned URL."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()
        mock_client.generate_presigned_url.return_value = "https://s3.example.com/signed-url"

        with patch("boto3.client", return_value=mock_client):
            s3_service = S3Service(test_settings)

            url = s3_service.generate_presigned_url("test/file.txt", expiration=3600)

            assert url == "https://s3.example.com/signed-url"
            mock_client.generate_presigned_url.assert_called_once()

    def test_ensure_bucket_exists_creates_if_missing(self, test_settings: Any) -> None:
        """Test ensure_bucket_exists creates bucket if it doesn't exist."""
        from common.storage.s3_service import S3Service

        mock_client = MagicMock()
        mock_client.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not found"}},
            "HeadBucket"
        )

        with patch("boto3.client", return_value=mock_client):
            s3_service = S3Service(test_settings)

            s3_service.ensure_bucket_exists()

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

            s3_service.ensure_bucket_exists()

            mock_client.create_bucket.assert_not_called()


class TestS3HealthCheck:
    """Tests for S3 health check."""

    def test_check_s3_health_healthy(self, test_settings: Any) -> None:
        """Test S3 health check when healthy."""
        from common.storage.s3_service import S3Service
        from common.storage.health import check_s3_health

        mock_client = MagicMock()
        mock_client.head_bucket.return_value = {}

        with patch("boto3.client", return_value=mock_client):
            s3_service = S3Service(test_settings)

            healthy, message = check_s3_health(s3_service)

            assert healthy is True
            assert "accessible" in message

    def test_check_s3_health_bucket_not_found(self, test_settings: Any) -> None:
        """Test S3 health check when bucket not found."""
        from common.storage.s3_service import S3Service
        from common.storage.health import check_s3_health

        mock_client = MagicMock()
        mock_client.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not found"}},
            "HeadBucket"
        )

        with patch("boto3.client", return_value=mock_client):
            s3_service = S3Service(test_settings)

            healthy, message = check_s3_health(s3_service)

            assert healthy is False
            assert "not found" in message

    def test_check_s3_health_access_denied(self, test_settings: Any) -> None:
        """Test S3 health check when access denied."""
        from common.storage.s3_service import S3Service
        from common.storage.health import check_s3_health

        mock_client = MagicMock()
        mock_client.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Forbidden"}},
            "HeadBucket"
        )

        with patch("boto3.client", return_value=mock_client):
            s3_service = S3Service(test_settings)

            healthy, message = check_s3_health(s3_service)

            assert healthy is False
            assert "access denied" in message
