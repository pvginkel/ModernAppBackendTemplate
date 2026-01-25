"""Graceful shutdown coordinator for managing service shutdowns."""

import logging
import signal
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import Enum

logger = logging.getLogger(__name__)


class LifetimeEvent(str, Enum):
    """Lifecycle events during shutdown process."""

    PREPARE_SHUTDOWN = "prepare-shutdown"
    SHUTDOWN = "shutdown"
    AFTER_SHUTDOWN = "after-shutdown"


class ShutdownCoordinatorProtocol(ABC):
    """Protocol for shutdown coordinator implementations."""

    @abstractmethod
    def initialize(self) -> None:
        """Setup the signal handlers."""
        pass

    @abstractmethod
    def register_lifetime_notification(
        self, callback: Callable[[LifetimeEvent], None]
    ) -> None:
        """Register a callback to be notified when shutdown starts."""
        pass

    @abstractmethod
    def register_shutdown_waiter(
        self, name: str, handler: Callable[[float], bool]
    ) -> None:
        """Register a handler that blocks until ready for shutdown."""
        pass

    @abstractmethod
    def is_shutting_down(self) -> bool:
        """Check if shutdown has been initiated."""
        pass

    @abstractmethod
    def shutdown(self) -> None:
        """Implements the shutdown process."""
        pass


class ShutdownCoordinator(ShutdownCoordinatorProtocol):
    """Coordinator for graceful shutdown of services.

    Handles SIGTERM/SIGINT signals and coordinates shutdown across
    registered services, allowing them to complete in-flight work.
    """

    def __init__(self, graceful_shutdown_timeout: int):
        """Initialize shutdown coordinator.

        Args:
            graceful_shutdown_timeout: Maximum seconds to wait for shutdown
        """
        self._graceful_shutdown_timeout = graceful_shutdown_timeout
        self._shutting_down = False
        self._shutdown_lock = threading.RLock()
        self._shutdown_notifications: list[Callable[[LifetimeEvent], None]] = []
        self._shutdown_waiters: dict[str, Callable[[float], bool]] = {}

        logger.info("ShutdownCoordinator initialized")

    def initialize(self) -> None:
        """Setup the signal handlers."""
        signal.signal(signal.SIGTERM, self._handle_sigterm)
        signal.signal(signal.SIGINT, self._handle_sigterm)

    def register_lifetime_notification(
        self, callback: Callable[[LifetimeEvent], None]
    ) -> None:
        """Register a callback to be notified when shutdown starts."""
        with self._shutdown_lock:
            self._shutdown_notifications.append(callback)
            logger.debug(
                f"Registered shutdown notification: "
                f"{getattr(callback, '__name__', repr(callback))}"
            )

    def register_shutdown_waiter(
        self, name: str, handler: Callable[[float], bool]
    ) -> None:
        """Register a handler that blocks until ready for shutdown."""
        with self._shutdown_lock:
            self._shutdown_waiters[name] = handler
            logger.debug(f"Registered shutdown waiter: {name}")

    def is_shutting_down(self) -> bool:
        """Check if shutdown has been initiated."""
        with self._shutdown_lock:
            return self._shutting_down

    def _handle_sigterm(self, signum: int, frame: object) -> None:
        """SIGTERM signal handler that performs complete graceful shutdown."""
        logger.info(f"Received signal {signum}, initiating graceful shutdown")
        self.shutdown()

    def shutdown(self) -> None:
        """Implements the shutdown process."""
        with self._shutdown_lock:
            if self._shutting_down:
                logger.warning("Shutdown already in progress, ignoring signal")
                return

            self._shutting_down = True
            shutdown_start_time = time.perf_counter()

            # Notify all listeners that we're starting shutdown
            self._raise_lifetime_event(LifetimeEvent.PREPARE_SHUTDOWN)

        # Phase 2: Wait for services to complete (blocking)
        logger.info(
            f"Waiting for {len(self._shutdown_waiters)} services to complete "
            f"(timeout: {self._graceful_shutdown_timeout}s)"
        )

        start_time = time.perf_counter()
        all_ready = True

        for name, waiter in self._shutdown_waiters.items():
            elapsed = time.perf_counter() - start_time
            remaining = self._graceful_shutdown_timeout - elapsed

            if remaining <= 0:
                logger.error(f"Shutdown timeout exceeded before checking {name}")
                all_ready = False
                break

            try:
                logger.info(
                    f"Waiting for {name} to complete (remaining: {remaining:.1f}s)"
                )
                ready = waiter(remaining)

                if not ready:
                    logger.warning(f"{name} was not ready within timeout")
                    all_ready = False

            except Exception as e:
                logger.error(f"Error in shutdown waiter {name}: {e}")
                all_ready = False

        total_duration = time.perf_counter() - shutdown_start_time

        if not all_ready:
            logger.error(
                f"Shutdown timeout exceeded after {total_duration:.1f}s, "
                "forcing shutdown"
            )

        # Notify that we're actually shutting down now
        self._raise_lifetime_event(LifetimeEvent.SHUTDOWN)

        logger.info("Shutting down")

        self._raise_lifetime_event(LifetimeEvent.AFTER_SHUTDOWN)

    def _raise_lifetime_event(self, event: LifetimeEvent) -> None:
        """Notify all registered callbacks of a lifetime event."""
        logger.info(f"Raising lifetime event {event}")

        for callback in self._shutdown_notifications:
            try:
                callback(event)
            except Exception as e:
                logger.error(
                    f"Error in lifetime event notification "
                    f"{getattr(callback, '__name__', repr(callback))}: {e}"
                )
