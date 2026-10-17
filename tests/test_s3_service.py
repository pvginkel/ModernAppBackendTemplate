"""Unit tests for S3Service."""

import hashlib
import io
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, NoCredentialsError
from flask import Flask

from app.config import Settings
from app.exceptions import InvalidOperationException
from app.services.s3_service import S3Service


@pytest.fixture
def mock_s3_client():
    return MagicMock()


@pytest.fixture
def s3_service(app: Flask, mock_s3_client, test_settings):
    with app.app_context():
        service = S3Service(test_settings)
        service._s3_client = mock_s3_client
        return service


class TestS3Service:
    """Test S3Service functionality."""

    def test_init_creates_client(self, app: Flask, test_settings: Settings):
        with app.app_context():
            service = S3Service(test_settings)
            assert service.s3_client is not None

    def test_upload_file_success(self, s3_service, mock_s3_client):
        file_data = io.BytesIO(b"test file content")
        s3_key = "test/key.txt"
        content_type = "text/plain"
        mock_s3_client.upload_fileobj.return_value = None

        result = s3_service.upload_file(file_data, s3_key, content_type)

        assert result is True
        mock_s3_client.upload_fileobj.assert_called_once()
        args, kwargs = mock_s3_client.upload_fileobj.call_args
        assert args[0] == file_data
        assert args[2] == s3_key
        assert kwargs["ExtraArgs"]["ContentType"] == content_type

    def test_upload_file_client_error(self, s3_service, mock_s3_client):
        file_data = io.BytesIO(b"test content")
        mock_s3_client.upload_fileobj.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Access denied"}},
            "PutObject",
        )
        with pytest.raises(InvalidOperationException) as exc_info:
            s3_service.upload_file(file_data, "test/key", "text/plain")
        assert "upload file to S3" in str(exc_info.value)

    def test_upload_file_no_credentials(self, s3_service, mock_s3_client):
        file_data = io.BytesIO(b"test content")
        mock_s3_client.upload_fileobj.side_effect = NoCredentialsError()
        with pytest.raises(NoCredentialsError):
            s3_service.upload_file(file_data, "test/key")

    def test_download_file_success(self, s3_service, mock_s3_client):
        test_content = b"downloaded file content"

        def mock_download(bucket, key, fileobj):
            fileobj.write(test_content)

        mock_s3_client.download_fileobj.side_effect = mock_download
        result = s3_service.download_file("test/key.txt")
        assert isinstance(result, io.BytesIO)
        assert result.getvalue() == test_content

    def test_download_file_not_found(self, s3_service, mock_s3_client):
        mock_s3_client.download_fileobj.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist"}},
            "GetObject",
        )
        with pytest.raises(InvalidOperationException):
            s3_service.download_file("nonexistent/key")

    def test_delete_file_success(self, s3_service, mock_s3_client):
        mock_s3_client.delete_object.return_value = {"DeleteMarker": True}
        result = s3_service.delete_file("test/key.txt")
        assert result is True
        mock_s3_client.delete_object.assert_called_once()

    def test_file_exists_true(self, s3_service, mock_s3_client):
        mock_s3_client.head_object.return_value = {"ContentLength": 1024}
        assert s3_service.file_exists("existing/key.txt") is True

    def test_file_exists_false(self, s3_service, mock_s3_client):
        mock_s3_client.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}},
            "HeadObject",
        )
        assert s3_service.file_exists("nonexistent/key.txt") is False

    def test_file_exists_other_error(self, s3_service, mock_s3_client):
        mock_s3_client.head_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Access denied"}},
            "HeadObject",
        )
        with pytest.raises(InvalidOperationException):
            s3_service.file_exists("test/key.txt")

    def test_upload_file_no_content_type(self, s3_service, mock_s3_client):
        file_data = io.BytesIO(b"test content")
        s3_service.upload_file(file_data, "test/key.bin")
        args, kwargs = mock_s3_client.upload_fileobj.call_args
        assert "ExtraArgs" not in kwargs

    def test_uses_config_values(self, app: Flask, test_settings: Settings):
        with app.app_context():
            with patch("app.services.s3_service.boto3.client") as mock_boto3:
                service = S3Service(test_settings)
                _ = service.s3_client
                mock_boto3.assert_called_once()
                call_args = mock_boto3.call_args[1]
                assert call_args["endpoint_url"] is not None
                assert call_args["aws_access_key_id"] is not None


# ---------------------------------------------------------------------------
# Every method against a mocked boto3 client, on a service of its own.
# ---------------------------------------------------------------------------


def _client_error(code: str, message: str = "Error") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "Test")


@pytest.fixture
def client() -> MagicMock:
    return MagicMock()


@pytest.fixture
def service(client: MagicMock, test_settings: Settings) -> S3Service:
    svc = S3Service(test_settings.model_copy(update={"s3_bucket_name": "test-bucket"}))
    svc._s3_client = client
    return svc


def _paginator(client: MagicMock, pages: list[dict[str, object]]) -> MagicMock:
    paginator = MagicMock()
    paginator.paginate.return_value = pages
    client.get_paginator.return_value = paginator
    return paginator


class TestClientInitialisation:
    def test_client_is_created_once(self, test_settings: Settings):
        with patch("app.services.s3_service.boto3") as boto3:
            svc = S3Service(test_settings)

            assert svc.s3_client is svc.s3_client
            assert boto3.client.call_count == 1

    def test_missing_credentials_are_an_invalid_operation(self, test_settings: Settings):
        with patch("app.services.s3_service.boto3") as boto3:
            boto3.client.side_effect = NoCredentialsError()
            svc = S3Service(test_settings)

            with pytest.raises(InvalidOperationException, match="credentials not configured"):
                _ = svc.s3_client


class TestDownloadErrors:
    def test_download_rewinds_the_buffer(self, service: S3Service, client: MagicMock):
        client.download_fileobj.side_effect = lambda bucket, key, fileobj: fileobj.write(b"data")

        result = service.download_file("my/key.txt")

        assert result.tell() == 0
        assert result.read() == b"data"

    def test_other_errors_are_generic(self, service: S3Service, client: MagicMock):
        client.download_fileobj.side_effect = _client_error("InternalError", "Boom")

        with pytest.raises(InvalidOperationException, match="download file"):
            service.download_file("key")


class TestCopyFile:
    def test_copy_within_the_bucket(self, service: S3Service, client: MagicMock):
        assert service.copy_file("source/key", "target/key") is True
        client.copy_object.assert_called_once_with(
            CopySource={"Bucket": "test-bucket", "Key": "source/key"},
            Bucket="test-bucket",
            Key="target/key",
        )

    def test_missing_source(self, service: S3Service, client: MagicMock):
        client.copy_object.side_effect = _client_error("NoSuchKey")

        with pytest.raises(InvalidOperationException, match="source file not found"):
            service.copy_file("missing", "target")

    def test_other_errors(self, service: S3Service, client: MagicMock):
        client.copy_object.side_effect = _client_error("InternalError")

        with pytest.raises(InvalidOperationException, match="copy file"):
            service.copy_file("src", "dst")


class TestDeleteFileErrors:
    def test_delete_error_is_raised(self, service: S3Service, client: MagicMock):
        """Unlike delete_prefix, a single delete does not swallow its error."""
        client.delete_object.side_effect = _client_error("AccessDenied")

        with pytest.raises(InvalidOperationException, match="delete file"):
            service.delete_file("key")


class TestGetFileMetadata:
    def test_returns_the_metadata(self, service: S3Service, client: MagicMock):
        client.head_object.return_value = {
            "ContentLength": 1024,
            "ContentType": "image/png",
            "LastModified": "2024-01-01T00:00:00Z",
            "ETag": '"abc123"',
        }

        metadata = service.get_file_metadata("img/file.png")

        assert metadata["content_length"] == 1024
        assert metadata["content_type"] == "image/png"
        assert metadata["etag"] == "abc123"

    def test_missing_file(self, service: S3Service, client: MagicMock):
        client.head_object.side_effect = _client_error("404")

        with pytest.raises(InvalidOperationException, match="file not found"):
            service.get_file_metadata("missing")

    def test_other_errors(self, service: S3Service, client: MagicMock):
        client.head_object.side_effect = _client_error("500", "Internal")

        with pytest.raises(InvalidOperationException, match="get file metadata"):
            service.get_file_metadata("key")


class TestListObjects:
    def test_collects_keys_across_pages(self, service: S3Service, client: MagicMock):
        _paginator(client, [
            {"Contents": [{"Key": "prefix/a.txt"}, {"Key": "prefix/b.txt"}]},
            {"Contents": [{"Key": "prefix/c.txt"}]},
        ])

        assert service.list_objects("prefix/") == ["prefix/a.txt", "prefix/b.txt", "prefix/c.txt"]

    def test_empty_prefix(self, service: S3Service, client: MagicMock):
        _paginator(client, [{}])

        assert service.list_objects("empty/") == []

    def test_errors(self, service: S3Service, client: MagicMock):
        _paginator(client, []).paginate.side_effect = _client_error("AccessDenied")

        with pytest.raises(InvalidOperationException, match="list objects"):
            service.list_objects("prefix/")


class TestDeletePrefix:
    def test_deletes_everything_and_counts(self, service: S3Service, client: MagicMock):
        _paginator(client, [{"Contents": [{"Key": "p/a"}, {"Key": "p/b"}]}])

        assert service.delete_prefix("p/") == 2
        assert client.delete_object.call_count == 2

    def test_a_failed_delete_does_not_stop_the_rest(self, service: S3Service, client: MagicMock):
        _paginator(client, [{"Contents": [{"Key": "p/a"}, {"Key": "p/b"}, {"Key": "p/c"}]}])
        client.delete_object.side_effect = [None, _client_error("InternalError"), None]

        assert service.delete_prefix("p/") == 2
        assert client.delete_object.call_count == 3


class TestEnsureBucketExists:
    def test_existing_bucket_is_left_alone(self, service: S3Service, client: MagicMock):
        assert service.ensure_bucket_exists() is True
        client.create_bucket.assert_not_called()

    def test_missing_bucket_is_created(self, service: S3Service, client: MagicMock):
        client.head_bucket.side_effect = _client_error("404")

        assert service.ensure_bucket_exists() is True
        client.create_bucket.assert_called_once_with(Bucket="test-bucket")

    def test_create_failure(self, service: S3Service, client: MagicMock):
        client.head_bucket.side_effect = _client_error("404")
        client.create_bucket.side_effect = _client_error("InternalError")

        with pytest.raises(InvalidOperationException, match="create S3 bucket"):
            service.ensure_bucket_exists()

    def test_other_head_bucket_errors(self, service: S3Service, client: MagicMock):
        client.head_bucket.side_effect = _client_error("403", "Forbidden")

        with pytest.raises(InvalidOperationException, match="check S3 bucket"):
            service.ensure_bucket_exists()


class TestStartup:
    def test_startup_ensures_the_bucket(self, service: S3Service, client: MagicMock):
        client.head_bucket.side_effect = _client_error("404")

        service.startup()

        client.create_bucket.assert_called_once_with(Bucket="test-bucket")

    def test_startup_logs_a_failure_and_carries_on(self, service: S3Service, client: MagicMock):
        client.head_bucket.side_effect = _client_error("403", "Forbidden")

        service.startup()


class TestContentAddressing:
    def test_hash_is_sha256_hex(self, service: S3Service):
        assert service.compute_hash(b"hello world") == hashlib.sha256(b"hello world").hexdigest()

    def test_cas_key_is_the_hash_under_cas(self, service: S3Service):
        assert service.generate_cas_key(b"content") == f"cas/{hashlib.sha256(b'content').hexdigest()}"
