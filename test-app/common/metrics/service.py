"""Prometheus metrics service for application monitoring.

This service provides minimal infrastructure metrics (shutdown state) and
the get_metrics_text() method that returns ALL metrics from the global
Prometheus registry.

Services should own their own metrics directly:

    class MyService:
        def __init__(self):
            self.requests_total = Counter('my_requests_total', 'Total requests')

        def handle_request(self):
            self.requests_total.inc()

All metrics registered with Prometheus are automatically included in the
output of get_metrics_text().
"""

import logging
import time
from typing import TYPE_CHECKING

from prometheus_client import Gauge, Histogram, generate_latest

from common.core.shutdown import LifetimeEvent

if TYPE_CHECKING:
    from common.core.shutdown import ShutdownCoordinatorProtocol

logger = logging.getLogger(__name__)


class MetricsService:
    """Minimal metrics service for infrastructure concerns.

    This service handles:
    - Shutdown state metrics
    - Generating Prometheus metrics text (from global registry)

    Domain and service-specific metrics should be owned by those services
    directly, not centralized here.
    """

    def __init__(self, shutdown_coordinator: "ShutdownCoordinatorProtocol"):
        """Initialize metrics service.

        Args:
            shutdown_coordinator: Coordinator for graceful shutdown.
        """
        self.shutdown_coordinator = shutdown_coordinator
        self._shutdown_start_time: float | None = None

        # Register shutdown notification
        self.shutdown_coordinator.register_lifetime_notification(
            self._on_lifetime_event
        )

        # Initialize shutdown metrics
        self.application_shutting_down = Gauge(
            "application_shutting_down",
            "Whether application is shutting down (1=yes, 0=no)",
        )

        self.graceful_shutdown_duration_seconds = Histogram(
            "graceful_shutdown_duration_seconds",
            "Duration of graceful shutdowns",
        )

    def get_metrics_text(self) -> str:
        """Generate metrics in Prometheus text format.

        Returns all metrics from the global Prometheus registry,
        including metrics owned by other services.
        """
        return generate_latest().decode("utf-8")

    def set_shutdown_state(self, is_shutting_down: bool) -> None:
        """Set the shutdown state metric.

        Args:
            is_shutting_down: Whether the application is shutting down.
        """
        try:
            self.application_shutting_down.set(1 if is_shutting_down else 0)
            if is_shutting_down:
                self._shutdown_start_time = time.perf_counter()
        except Exception as e:
            logger.error(f"Error setting shutdown state: {e}")

    def _on_lifetime_event(self, event: LifetimeEvent) -> None:
        """Callback for shutdown lifecycle events."""
        match event:
            case LifetimeEvent.PREPARE_SHUTDOWN:
                self.set_shutdown_state(True)
            case LifetimeEvent.SHUTDOWN:
                self._record_shutdown_duration()

    def _record_shutdown_duration(self) -> None:
        """Record the shutdown duration metric."""
        if self._shutdown_start_time:
            duration = time.perf_counter() - self._shutdown_start_time
            try:
                self.graceful_shutdown_duration_seconds.observe(duration)
            except Exception as e:
                logger.error(f"Error recording shutdown duration: {e}")
