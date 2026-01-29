"""Tests for metrics service and endpoint."""

from typing import Any
from unittest.mock import Mock


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
        # MetricsService owns shutdown metrics
        assert "application_shutting_down" in data
        # TaskService owns task metrics (if TaskService is initialized)
        # ConnectionManager owns SSE metrics (if ConnectionManager is initialized)


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

    def test_shutdown_state_metric(
        self, app_context: Any, container: Any
    ) -> None:
        """Test shutdown state can be set."""
        metrics_service = container.metrics_service()

        # Should not raise
        metrics_service.set_shutdown_state(True)

        # Verify metric is in output
        text = metrics_service.get_metrics_text()
        assert "application_shutting_down" in text

    def test_metrics_service_minimal_api(
        self, app_context: Any, container: Any
    ) -> None:
        """Test MetricsService has the expected minimal API."""
        metrics_service = container.metrics_service()

        # Verify required methods exist
        assert hasattr(metrics_service, "get_metrics_text")
        assert hasattr(metrics_service, "set_shutdown_state")
        assert callable(metrics_service.get_metrics_text)
        assert callable(metrics_service.set_shutdown_state)

        # Verify it owns shutdown metrics
        assert hasattr(metrics_service, "application_shutting_down")
        assert hasattr(metrics_service, "graceful_shutdown_duration_seconds")


class TestMetricsUpdateCoordinator:
    """Tests for MetricsUpdateCoordinator."""

    def test_coordinator_register_updater(
        self, app_context: Any, container: Any
    ) -> None:
        """Test registering an updater with the coordinator."""
        coordinator = container.metrics_coordinator()

        # Register a mock updater
        mock_updater = Mock()
        coordinator.register_updater(mock_updater)

        # Verify updater was registered
        assert mock_updater in coordinator._updaters

    def test_coordinator_update_all_calls_updaters(
        self, app_context: Any, container: Any
    ) -> None:
        """Test update_all calls all registered updaters."""
        coordinator = container.metrics_coordinator()

        # Register mock updaters
        mock_updater1 = Mock()
        mock_updater2 = Mock()
        coordinator.register_updater(mock_updater1)
        coordinator.register_updater(mock_updater2)

        # Call update_all
        coordinator.update_all()

        # Verify both updaters were called
        mock_updater1.assert_called_once()
        mock_updater2.assert_called_once()

    def test_coordinator_updater_exception_isolated(
        self, app_context: Any, container: Any
    ) -> None:
        """Test that an updater exception doesn't break other updaters."""
        coordinator = container.metrics_coordinator()

        # Register failing and working updaters
        failing_updater = Mock(side_effect=Exception("Updater failed"))
        working_updater = Mock()
        coordinator.register_updater(failing_updater)
        coordinator.register_updater(working_updater)

        # Call update_all (should not raise)
        coordinator.update_all()

        # Verify both were called (working one succeeded)
        failing_updater.assert_called_once()
        working_updater.assert_called_once()
