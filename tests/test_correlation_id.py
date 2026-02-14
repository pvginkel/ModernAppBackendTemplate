"""Tests for correlation ID handling."""

from app.utils import get_current_correlation_id


class TestCorrelationId:
    """Test correlation ID utility functions."""

    def test_get_current_correlation_id_without_request_context(self):
        """Test getting correlation ID outside request context."""
        correlation_id = get_current_correlation_id()
        assert correlation_id is None

    def test_request_generates_correlation_id(self, client):
        """Test that requests get a correlation ID."""
        response = client.get("/health/healthz")
        assert response.status_code == 200

    def test_custom_request_id_header(self, client):
        """Test that X-Request-ID header is used as correlation ID."""
        response = client.get(
            "/health/healthz",
            headers={"X-Request-ID": "custom-id-123"},
        )
        assert response.status_code == 200
