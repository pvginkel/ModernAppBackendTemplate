"""Tests for the request diagnostics service."""

import pytest
from flask import Flask

from app import create_app
from app.app_config import AppSettings
from app.config import Settings
from app.services.diagnostics_service import (
    DiagnosticsService,
    QueryInfo,
    RequestDiagnostics,
)
from tests.conftest_infrastructure import _dispose_app


class TestRequestDiagnostics:
    """Unit tests for the RequestDiagnostics data class."""

    def test_query_count_empty(self):
        """Test query count with no queries."""
        diag = RequestDiagnostics()
        assert diag.query_count == 0

    def test_query_count_with_queries(self):
        """Test query count with multiple queries."""
        diag = RequestDiagnostics()
        diag.queries.append(QueryInfo(sql="SELECT 1", duration_ms=1.0))
        diag.queries.append(QueryInfo(sql="SELECT 2", duration_ms=2.0))
        assert diag.query_count == 2

    def test_total_query_time(self):
        """Test total query time calculation."""
        diag = RequestDiagnostics()
        diag.queries.append(QueryInfo(sql="SELECT 1", duration_ms=10.5))
        diag.queries.append(QueryInfo(sql="SELECT 2", duration_ms=25.3))
        assert diag.total_query_time_ms == pytest.approx(35.8)

    def test_python_time_calculation(self):
        """Test that Python time is request duration minus query time."""
        import time

        diag = RequestDiagnostics()
        time.sleep(0.01)  # 10ms
        diag.queries.append(QueryInfo(sql="SELECT 1", duration_ms=5.0))

        assert diag.request_duration_ms >= 10
        assert diag.python_time_ms > 0


# DiagnosticsService registers its metrics when it is constructed.
@pytest.mark.usefixtures("clear_prometheus_registry")
class TestDiagnosticsServiceInit:
    """Tests for DiagnosticsService initialization."""

    def test_service_disabled_by_default(self, test_settings: Settings):
        """Test that diagnostics is disabled by default."""
        assert test_settings.diagnostics_enabled is False
        service = DiagnosticsService(test_settings)
        assert service.enabled is False

    def test_service_enabled_via_settings(self):
        """Test enabling diagnostics via settings."""
        settings = Settings(
            flask_env="testing",
            diagnostics_enabled=True,
        )
        service = DiagnosticsService(settings)
        assert service.enabled is True

    def test_threshold_configuration(self):
        """Test custom threshold configuration."""
        settings = Settings(
            flask_env="testing",
            diagnostics_enabled=True,
            diagnostics_slow_query_threshold_ms=50,
            diagnostics_slow_request_threshold_ms=200,
        )
        service = DiagnosticsService(settings)
        assert service.slow_query_threshold_ms == 50
        assert service.slow_request_threshold_ms == 200


class TestDiagnosticsServiceIntegration:
    """Integration tests with Flask and SQLAlchemy."""

    @pytest.mark.usefixtures("clear_prometheus_registry")
    def test_request_timing_recorded(
        self, app: Flask, test_settings: Settings, test_app_settings: AppSettings
    ):
        """Test that an app with background services carries the diagnostics service."""
        settings = test_settings.model_copy(
            update={"database_url": app.config["SQLALCHEMY_DATABASE_URI"]}
        )
        app_with_services = create_app(settings, app_settings=test_app_settings)
        try:
            assert hasattr(app_with_services, "diagnostics_service")
            service = app_with_services.diagnostics_service

            assert hasattr(service, "request_query_count")
            assert hasattr(service, "query_duration_seconds")
        finally:
            _dispose_app(app_with_services)

    def test_query_tracking_structure(self):
        """Test QueryInfo data structure."""
        query = QueryInfo(
            sql="SELECT * FROM items WHERE key = 'ABCD'",
            duration_ms=15.5,
            parameters={"key": "ABCD"},
        )

        assert query.sql == "SELECT * FROM items WHERE key = 'ABCD'"
        assert query.duration_ms == 15.5
        assert query.parameters == {"key": "ABCD"}


@pytest.mark.usefixtures("clear_prometheus_registry")
class TestDiagnosticsMetrics:
    """Tests for Prometheus metrics initialization."""

    def test_all_metrics_initialized(self):
        """Test that all required metrics are initialized."""
        settings = Settings(
            flask_env="testing",
            diagnostics_enabled=True,
        )
        service = DiagnosticsService(settings)

        assert service.request_query_count is not None
        assert service.request_query_time_seconds is not None
        assert service.request_python_time_seconds is not None

        assert service.query_duration_seconds is not None
        assert service.slow_query_total is not None

        assert service.slow_request_total is not None
        assert service.active_requests is not None
