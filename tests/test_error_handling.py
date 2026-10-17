"""Tests for error handling and exception hierarchy."""

from flask import Flask, request
from flask.testing import FlaskClient
from werkzeug.exceptions import UnsupportedMediaType

from app.exceptions import (
    BusinessLogicException,
    InvalidOperationException,
    RecordNotFoundException,
    ResourceConflictException,
)


class TestExceptionHierarchy:
    """Test exception class hierarchy and messages."""

    def test_record_not_found_exception(self):
        """Test RecordNotFoundException message formatting."""
        exc = RecordNotFoundException("Item", 42)
        assert "Item 42 was not found" in str(exc)
        assert exc.error_code == "RECORD_NOT_FOUND"

    def test_resource_conflict_exception(self):
        """Test ResourceConflictException message formatting."""
        exc = ResourceConflictException("Item", "test-name")
        assert "item" in str(exc).lower()
        assert "test-name" in str(exc)
        assert exc.error_code == "RESOURCE_CONFLICT"

    def test_invalid_operation_exception(self):
        """Test InvalidOperationException message formatting."""
        exc = InvalidOperationException("delete", "item has dependencies")
        assert "Cannot delete" in str(exc)
        assert "item has dependencies" in str(exc)
        assert exc.error_code == "INVALID_OPERATION"

    def test_business_logic_exception_base(self):
        """Test BusinessLogicException base class."""
        exc = BusinessLogicException("test message", "TEST_CODE")
        assert exc.message == "test message"
        assert exc.error_code == "TEST_CODE"
        assert str(exc) == "test message"


class TestErrorResponses:
    """Test that error handlers produce correct JSON responses."""

    def test_404_includes_correlation_id(self, client: FlaskClient):
        """Test that 404 responses include correlationId."""
        response = client.get("/api/nonexistent-endpoint")
        assert response.status_code == 404
        data = response.get_json()
        assert "correlationId" in data

    def test_custom_request_id_propagated(self, client: FlaskClient):
        """Test that X-Request-ID header is used as correlationId."""
        response = client.get(
            "/api/nonexistent-endpoint",
            headers={"X-Request-ID": "test-correlation-123"},
        )
        data = response.get_json()
        assert data["correlationId"] == "test-correlation-123"

    def test_auto_generated_correlation_id(self, client: FlaskClient):
        """Test that correlationId is auto-generated when not provided."""
        response = client.get("/api/nonexistent-endpoint")
        data = response.get_json()
        # Should be a UUID-like string
        assert len(data["correlationId"]) > 0


class TestHttpErrors:
    """Werkzeug HTTP errors keep their status in the JSON envelope.

    Each test adds routes, which only an app that has served no request yet
    accepts, so they run on an app of their own.
    """

    def test_body_over_the_upload_limit_is_413(self, committed_app: Flask):
        committed_app.config["MAX_CONTENT_LENGTH"] = 10
        committed_app.add_url_rule(
            "/test-upload", "test_upload", lambda: str(len(request.get_data())), methods=["POST"]
        )

        response = committed_app.test_client().post("/test-upload", data=b"x" * 100)

        assert response.status_code == 413
        assert response.json["error"] == "Request Entity Too Large"

    def test_body_within_the_limit_passes(self, committed_app: Flask):
        committed_app.config["MAX_CONTENT_LENGTH"] = 1000
        committed_app.add_url_rule(
            "/test-upload", "test_upload", lambda: str(len(request.get_data())), methods=["POST"]
        )

        response = committed_app.test_client().post("/test-upload", data=b"x" * 100)

        assert response.status_code == 200
        assert response.get_data(as_text=True) == "100"

    def test_other_http_errors_keep_their_status(self, committed_app: Flask):
        def unsupported() -> str:
            raise UnsupportedMediaType()

        committed_app.add_url_rule("/test-415", "test_415", unsupported)

        response = committed_app.test_client().get("/test-415")

        assert response.status_code == 415
        assert response.json["error"] == "Unsupported Media Type"
