"""Metrics update coordinator for periodic metric refreshes.

This module provides a coordinator that manages periodic updates of gauge metrics
across services. Services register their update_metrics() methods, and the
coordinator calls them at regular intervals in a background thread.
"""

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from common.core.shutdown import LifetimeEvent

if TYPE_CHECKING:
    from common.core.shutdown import ShutdownCoordinatorProtocol

logger = logging.getLogger(__name__)


class MetricsUpdateCoordinator:
    """Coordinates periodic metric updates across services.

    Services that have gauge metrics needing periodic refresh can register
    their update_metrics() method. The coordinator runs a background thread
    that calls all registered updaters at the configured interval.

    Example usage:
        # In app factory
        coordinator = container.metrics_coordinator()
        coordinator.register_updater(dashboard_service.update_metrics)
        coordinator.start(interval_seconds=60)
    """

    def __init__(self, shutdown_coordinator: "ShutdownCoordinatorProtocol"):
        """Initialize the coordinator.

        Args:
            shutdown_coordinator: Coordinator for graceful shutdown integration.
        """
        self._updaters: list[Callable[[], None]] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # Register for shutdown notification
        shutdown_coordinator.register_lifetime_notification(self._on_lifetime_event)

    def register_updater(self, updater: Callable[[], None]) -> None:
        """Register a service's update_metrics method.

        Args:
            updater: A callable that updates the service's metrics.
                     Should be idempotent and handle its own exceptions.
        """
        with self._lock:
            self._updaters.append(updater)
            logger.debug(
                "Registered metrics updater",
                extra={"updater": getattr(updater, "__name__", repr(updater))},
            )

    def unregister_updater(self, updater: Callable[[], None]) -> None:
        """Unregister a previously registered updater.

        Args:
            updater: The callable to remove.
        """
        with self._lock:
            try:
                self._updaters.remove(updater)
                logger.debug(
                    "Unregistered metrics updater",
                    extra={"updater": getattr(updater, "__name__", repr(updater))},
                )
            except ValueError:
                pass  # Already removed or never registered

    def start(self, interval_seconds: int = 60) -> None:
        """Start the background update loop.

        Args:
            interval_seconds: Time between update cycles (default: 60).
        """
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Metrics update coordinator already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._update_loop,
            args=(interval_seconds,),
            daemon=True,
            name="MetricsUpdateCoordinator",
        )
        self._thread.start()
        logger.info(
            "Started metrics update coordinator",
            extra={"interval_seconds": interval_seconds},
        )

    def stop(self) -> None:
        """Stop the background update loop."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
            logger.info("Stopped metrics update coordinator")

    def _update_loop(self, interval_seconds: int) -> None:
        """Background loop that calls all registered updaters.

        Args:
            interval_seconds: Time to wait between update cycles.
        """
        while not self._stop_event.is_set():
            # Wait first, then update (allows immediate shutdown on startup)
            if self._stop_event.wait(interval_seconds):
                break

            self._run_updaters()

    def _run_updaters(self) -> None:
        """Execute all registered updaters."""
        with self._lock:
            updaters = list(self._updaters)

        for updater in updaters:
            try:
                updater()
            except Exception as e:
                logger.error(
                    "Metrics updater failed",
                    exc_info=True,
                    extra={
                        "updater": getattr(updater, "__name__", repr(updater)),
                        "error": str(e),
                    },
                )

    def _on_lifetime_event(self, event: LifetimeEvent) -> None:
        """Handle shutdown lifecycle events.

        Args:
            event: The lifecycle event.
        """
        if event == LifetimeEvent.SHUTDOWN:
            self.stop()
