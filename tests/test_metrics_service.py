"""Tests for MetricsService polling infrastructure."""

import threading
import time
from unittest.mock import MagicMock

from app.services.metrics_service import MetricsService
from tests.testing_utils import StubLifecycleCoordinator


class TestMetricsServicePolling:
    """Test the thin MetricsService polling infrastructure."""

    def _make_service(self, lifecycle_coordinator=None):
        container = MagicMock()
        lc = lifecycle_coordinator or StubLifecycleCoordinator()
        service = MetricsService(container=container, lifecycle_coordinator=lc)
        return service

    def test_register_for_polling(self):
        service = self._make_service()
        try:
            called = threading.Event()

            def callback():
                called.set()

            service.register_for_polling("test", callback)
            assert "test" in service._polling_callbacks
        finally:
            service.shutdown()

    def test_background_updater_lifecycle(self):
        service = self._make_service()
        try:
            service.start_background_updater(1)
            assert service._updater_thread is not None
            assert service._updater_thread.is_alive()
            assert not service._stop_event.is_set()
            service.shutdown()
            time.sleep(0.1)
            assert service._stop_event.is_set()
        except Exception:
            service.shutdown()
            raise

    def test_background_updater_double_start(self):
        service = self._make_service()
        try:
            service.start_background_updater(1)
            first_thread = service._updater_thread
            service.start_background_updater(1)
            assert service._updater_thread is first_thread
        finally:
            service.shutdown()

    def test_background_updater_invokes_callbacks(self):
        service = self._make_service()
        try:
            called = threading.Event()

            def callback():
                called.set()

            service.register_for_polling("test_cb", callback)
            service.start_background_updater(interval_seconds=1)
            assert called.wait(timeout=3.0), "Callback was not invoked within timeout"
        finally:
            service.shutdown()

    def test_background_updater_handles_callback_errors(self):
        service = self._make_service()
        try:
            error_count = 0
            good_called = threading.Event()

            def bad_callback():
                nonlocal error_count
                error_count += 1
                raise Exception("Callback error")

            def good_callback():
                good_called.set()

            service.register_for_polling("bad", bad_callback)
            service.register_for_polling("good", good_callback)
            service.start_background_updater(interval_seconds=1)
            assert good_called.wait(timeout=3.0), "Good callback was not invoked"
            assert error_count > 0
        finally:
            service.shutdown()

    def test_shutdown_via_lifecycle_event(self):
        lifecycle_coordinator = StubLifecycleCoordinator()
        container = MagicMock()
        service = MetricsService(container=container, lifecycle_coordinator=lifecycle_coordinator)
        service.start_background_updater(1)

        assert service._updater_thread is not None
        assert service._updater_thread.is_alive()

        from app.utils.lifecycle_coordinator import LifecycleEvent

        for notification in lifecycle_coordinator._notifications:
            notification(LifecycleEvent.SHUTDOWN)

        time.sleep(0.2)
        assert service._stop_event.is_set()


class TestDecentralizedMetricsExist:
    """Verify that module-level metrics are defined in owning services."""

    def test_sse_connection_manager_metrics(self):
        from app.services.sse_connection_manager import (
            SSE_GATEWAY_ACTIVE_CONNECTIONS,
            SSE_GATEWAY_CONNECTIONS_TOTAL,
            SSE_GATEWAY_EVENTS_SENT_TOTAL,
        )

        assert SSE_GATEWAY_CONNECTIONS_TOTAL is not None
        assert SSE_GATEWAY_EVENTS_SENT_TOTAL is not None
        assert SSE_GATEWAY_ACTIVE_CONNECTIONS is not None

    def test_lifecycle_coordinator_metrics(self):
        from app.utils.lifecycle_coordinator import (
            APPLICATION_SHUTTING_DOWN,
            GRACEFUL_SHUTDOWN_DURATION_SECONDS,
        )

        assert APPLICATION_SHUTTING_DOWN is not None
        assert GRACEFUL_SHUTDOWN_DURATION_SECONDS is not None

    def test_task_service_shutdown_metric(self):
        from app.services.task_service import ACTIVE_TASKS_AT_SHUTDOWN

        assert ACTIVE_TASKS_AT_SHUTDOWN is not None


class TestMetricsEndpoint:
    """Test the /metrics endpoint uses generate_latest() directly."""

    def test_metrics_endpoint_returns_prometheus_format(self, client):
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.content_type
        text = response.data.decode("utf-8")
        assert len(text) > 0
