"""Tests for metrics service and endpoint."""

from typing import Any
from unittest.mock import patch


class TestMetricsEndpoint:
    """Tests for /metrics endpoint."""

    def test_metrics_endpoint_returns_prometheus_format(self, client: Any) -> None:
        """Test metrics endpoint returns Prometheus text format."""
        response = client.get("/metrics")

        assert response.status_code == 200
        assert response.content_type.startswith("text/plain")

        # Should contain some metric data
        data = response.get_data(as_text=True)
        assert len(data) > 0

    def test_metrics_endpoint_contains_app_metrics(self, client: Any) -> None:
        """Test metrics contains app-specific metrics."""
        response = client.get("/metrics")

        data = response.get_data(as_text=True)
        # MetricsService uses a private registry with app-specific metrics
        assert "application_shutting_down" in data
        assert "task_execution" in data


class TestMetricsService:
    """Tests for MetricsService functionality."""

    def test_get_metrics_text_returns_string(
        self, app_context: Any, container: Any
    ) -> None:
        """Test get_metrics_text returns a string."""
        metrics_service = container.metrics_service()

        text = metrics_service.get_metrics_text()

        assert isinstance(text, str)
        assert len(text) > 0

    def test_record_task_execution_success(
        self, app_context: Any, container: Any
    ) -> None:
        """Test recording successful task execution."""
        metrics_service = container.metrics_service()

        # Should not raise
        metrics_service.record_task_execution(
            task_type="TestTask",
            duration=1.5,
            status="success"
        )

    def test_record_task_execution_error(
        self, app_context: Any, container: Any
    ) -> None:
        """Test recording failed task execution."""
        metrics_service = container.metrics_service()

        # Should not raise
        metrics_service.record_task_execution(
            task_type="TestTask",
            duration=0.5,
            status="error"
        )

    def test_metrics_service_protocol(self, app_context: Any, container: Any) -> None:
        """Test MetricsService implements MetricsServiceProtocol."""
        from common.metrics.service import MetricsServiceProtocol

        metrics_service = container.metrics_service()

        # Verify required methods exist
        assert hasattr(metrics_service, "get_metrics_text")
        assert hasattr(metrics_service, "record_task_execution")
        assert hasattr(metrics_service, "start_background_updater")
        assert callable(metrics_service.get_metrics_text)
        assert callable(metrics_service.record_task_execution)
