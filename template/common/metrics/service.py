"""Prometheus metrics service for application monitoring."""

import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from prometheus_client import Counter, Gauge, Histogram, generate_latest

from common.core.shutdown import LifetimeEvent

if TYPE_CHECKING:
    from common.core.shutdown import ShutdownCoordinatorProtocol

logger = logging.getLogger(__name__)


class MetricsServiceProtocol(ABC):
    """Protocol for metrics service implementations.

    Apps can extend MetricsService and add their own metrics
    while maintaining the base infrastructure metrics.
    """

    @abstractmethod
    def start_background_updater(self, interval_seconds: int) -> None:
        """Start background metric updater."""
        pass

    @abstractmethod
    def get_metrics_text(self) -> str:
        """Get metrics in Prometheus text format."""
        pass

    @abstractmethod
    def record_task_execution(
        self, task_type: str, duration: float, status: str
    ) -> None:
        """Record task execution metrics."""
        pass

    @abstractmethod
    def set_shutdown_state(self, is_shutting_down: bool) -> None:
        """Set the shutdown state metric."""
        pass

    @abstractmethod
    def shutdown(self) -> None:
        """Shutdown the metrics service."""
        pass

    @abstractmethod
    def record_sse_gateway_connection(self, action: str) -> None:
        """Record SSE Gateway connection lifecycle events.

        Args:
            action: Action type (connect or disconnect)
        """
        pass

    @abstractmethod
    def record_sse_gateway_event(self, service: str, status: str) -> None:
        """Record SSE Gateway event send attempts.

        Args:
            service: Service type (task or version)
            status: Status (success or error)
        """
        pass

    @abstractmethod
    def record_sse_gateway_send_duration(self, service: str, duration: float) -> None:
        """Record SSE Gateway HTTP send duration.

        Args:
            service: Service type (task or version)
            duration: Duration in seconds
        """
        pass


class MetricsService(MetricsServiceProtocol):
    """Base metrics service with infrastructure metrics.

    Apps can subclass this to add their own metrics:

        class AppMetricsService(MetricsService):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.my_counter = Counter("my_metric", "Description")

            def update_app_metrics(self) -> None:
                # Called by background updater
                pass
    """

    def __init__(self, shutdown_coordinator: "ShutdownCoordinatorProtocol"):
        """Initialize metrics service.

        Args:
            shutdown_coordinator: Coordinator for graceful shutdown
        """
        self.shutdown_coordinator = shutdown_coordinator
        self._shutdown_start_time: float | None = None

        # Background update control
        self._stop_event = threading.Event()
        self._updater_thread: threading.Thread | None = None

        # Register shutdown notification
        self.shutdown_coordinator.register_lifetime_notification(
            self._on_lifetime_event
        )

        # Initialize metrics
        self._initialize_metrics()

    def _initialize_metrics(self) -> None:
        """Initialize Prometheus metric objects."""
        # Shutdown metrics
        self.application_shutting_down = Gauge(
            "application_shutting_down",
            "Whether application is shutting down (1=yes, 0=no)",
        )

        self.graceful_shutdown_duration_seconds = Histogram(
            "graceful_shutdown_duration_seconds",
            "Duration of graceful shutdowns",
        )

        # Task metrics
        self.task_execution_total = Counter(
            "task_execution_total",
            "Total task executions",
            ["task_type", "status"],
        )

        self.task_execution_duration_seconds = Histogram(
            "task_execution_duration_seconds",
            "Task execution duration in seconds",
            ["task_type"],
        )

        # SSE Gateway metrics
        self.sse_gateway_connections_total = Counter(
            "sse_gateway_connections_total",
            "Total SSE Gateway connection lifecycle events",
            ["action"],
        )
        self.sse_gateway_active_connections = Gauge(
            "sse_gateway_active_connections",
            "Current number of active SSE Gateway connections",
        )
        self.sse_gateway_events_sent_total = Counter(
            "sse_gateway_events_sent_total",
            "Total events sent to SSE Gateway",
            ["service", "status"],
        )
        self.sse_gateway_send_duration_seconds = Histogram(
            "sse_gateway_send_duration_seconds",
            "Duration of SSE Gateway HTTP send calls",
            ["service"],
        )

    def start_background_updater(self, interval_seconds: int = 60) -> None:
        """Start background thread for periodic metric updates."""
        if self._updater_thread is not None and self._updater_thread.is_alive():
            return

        self._stop_event.clear()
        self._updater_thread = threading.Thread(
            target=self._background_update_loop,
            args=(interval_seconds,),
            daemon=True,
        )
        self._updater_thread.start()

    def _stop_background_updater(self) -> None:
        """Stop the background metric updater thread."""
        self._stop_event.set()
        if self._updater_thread:
            self._updater_thread.join(timeout=5)

    def _background_update_loop(self, interval_seconds: int) -> None:
        """Background loop for periodic metric updates."""
        while not self._stop_event.is_set():
            try:
                self.update_metrics()
            except Exception as e:
                logger.error(f"Error in background metrics update: {e}")

            self._stop_event.wait(interval_seconds)

    def update_metrics(self) -> None:
        """Update metrics. Override in subclass to add app-specific metrics."""
        pass

    def record_task_execution(
        self, task_type: str, duration: float, status: str
    ) -> None:
        """Record task execution metrics."""
        try:
            self.task_execution_total.labels(
                task_type=task_type, status=status
            ).inc()
            self.task_execution_duration_seconds.labels(
                task_type=task_type
            ).observe(duration)
        except Exception as e:
            logger.error(f"Error recording task execution metric: {e}")

    def set_shutdown_state(self, is_shutting_down: bool) -> None:
        """Set the shutdown state metric."""
        try:
            self.application_shutting_down.set(1 if is_shutting_down else 0)
            if is_shutting_down:
                self._shutdown_start_time = time.perf_counter()
        except Exception as e:
            logger.error(f"Error setting shutdown state: {e}")

    def get_metrics_text(self) -> str:
        """Generate metrics in Prometheus text format."""
        return generate_latest().decode("utf-8")

    def _on_lifetime_event(self, event: LifetimeEvent) -> None:
        """Callback for shutdown lifecycle events."""
        match event:
            case LifetimeEvent.PREPARE_SHUTDOWN:
                self.set_shutdown_state(True)
            case LifetimeEvent.SHUTDOWN:
                self.shutdown()

    def shutdown(self) -> None:
        """Shutdown the metrics service."""
        self._stop_background_updater()

        if self._shutdown_start_time:
            duration = time.perf_counter() - self._shutdown_start_time
            try:
                self.graceful_shutdown_duration_seconds.observe(duration)
            except Exception as e:
                logger.error(f"Error recording shutdown duration: {e}")

    def record_sse_gateway_connection(self, action: str) -> None:
        """Record SSE Gateway connection lifecycle events.

        Args:
            action: Action type (connect or disconnect)
        """
        try:
            self.sse_gateway_connections_total.labels(action=action).inc()
            if action == "connect":
                self.sse_gateway_active_connections.inc()
            elif action == "disconnect":
                self.sse_gateway_active_connections.dec()
        except Exception as e:
            logger.error(f"Error recording SSE Gateway connection metric: {e}")

    def record_sse_gateway_event(self, service: str, status: str) -> None:
        """Record SSE Gateway event send attempts.

        Args:
            service: Service type (task or version)
            status: Status (success or error)
        """
        try:
            self.sse_gateway_events_sent_total.labels(
                service=service, status=status
            ).inc()
        except Exception as e:
            logger.error(f"Error recording SSE Gateway event metric: {e}")

    def record_sse_gateway_send_duration(self, service: str, duration: float) -> None:
        """Record SSE Gateway HTTP send duration.

        Args:
            service: Service type (task or version)
            duration: Duration in seconds
        """
        try:
            self.sse_gateway_send_duration_seconds.labels(service=service).observe(
                duration
            )
        except Exception as e:
            logger.error(f"Error recording SSE Gateway send duration: {e}")
