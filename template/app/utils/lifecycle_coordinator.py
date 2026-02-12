"""Lifecycle coordinator for managing application startup and graceful shutdown in Kubernetes."""

import logging
import signal
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import Enum

from prometheus_client import Gauge, Histogram

logger = logging.getLogger(__name__)

APPLICATION_SHUTTING_DOWN = Gauge(
    "application_shutting_down",
    "Whether application is shutting down (1=yes, 0=no)",
)
GRACEFUL_SHUTDOWN_DURATION_SECONDS = Histogram(
    "graceful_shutdown_duration_seconds",
    "Duration of graceful shutdowns",
)


class LifecycleEvent(str, Enum):
    STARTUP = "startup"
    PREPARE_SHUTDOWN = "prepare-shutdown"
    SHUTDOWN = "shutdown"
    AFTER_SHUTDOWN = "after-shutdown"

class LifecycleCoordinatorProtocol(ABC):
    """Protocol for lifecycle coordinator implementations."""

    @abstractmethod
    def initialize(self) -> None: ...

    @abstractmethod
    def register_lifecycle_notification(self, callback: Callable[[LifecycleEvent], None]) -> None: ...

    @abstractmethod
    def register_shutdown_waiter(self, name: str, handler: Callable[[float], bool]) -> None: ...

    @abstractmethod
    def is_shutting_down(self) -> bool: ...

    @abstractmethod
    def shutdown(self) -> None: ...

    @abstractmethod
    def fire_startup(self) -> None: ...

class LifecycleCoordinator(LifecycleCoordinatorProtocol):
    """Coordinator for application lifecycle events and graceful shutdown."""

    def __init__(self, graceful_shutdown_timeout: int):
        self._graceful_shutdown_timeout = graceful_shutdown_timeout
        self._shutting_down = False
        self._started = False
        self._lifecycle_lock = threading.RLock()
        self._lifecycle_notifications: list[Callable[[LifecycleEvent], None]] = []
        self._shutdown_waiters: dict[str, Callable[[float], bool]] = {}

        logger.info("LifecycleCoordinator initialized")

    def initialize(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_sigterm)
        signal.signal(signal.SIGINT, self._handle_sigterm)

    def register_lifecycle_notification(self, callback: Callable[[LifecycleEvent], None]) -> None:
        with self._lifecycle_lock:
            self._lifecycle_notifications.append(callback)

    def register_shutdown_waiter(self, name: str, handler: Callable[[float], bool]) -> None:
        with self._lifecycle_lock:
            self._shutdown_waiters[name] = handler

    def is_shutting_down(self) -> bool:
        with self._lifecycle_lock:
            return self._shutting_down

    def fire_startup(self) -> None:
        with self._lifecycle_lock:
            if self._started:
                return
            self._started = True
        self._raise_lifecycle_event(LifecycleEvent.STARTUP)

    def _handle_sigterm(self, signum: int, frame: object) -> None:
        logger.info(f"Received signal {signum}, initiating graceful shutdown")
        self.shutdown()

    def shutdown(self) -> None:
        with self._lifecycle_lock:
            if self._shutting_down:
                return
            self._shutting_down = True
            shutdown_start_time = time.perf_counter()
            APPLICATION_SHUTTING_DOWN.set(1)
            self._raise_lifecycle_event(LifecycleEvent.PREPARE_SHUTDOWN)

        start_time = time.perf_counter()
        all_ready = True

        for name, waiter in self._shutdown_waiters.items():
            elapsed = time.perf_counter() - start_time
            remaining = self._graceful_shutdown_timeout - elapsed
            if remaining <= 0:
                all_ready = False
                break
            try:
                ready = waiter(remaining)
                if not ready:
                    all_ready = False
            except Exception as e:
                logger.error(f"Error in shutdown waiter {name}: {e}")
                all_ready = False

        total_duration = time.perf_counter() - shutdown_start_time
        GRACEFUL_SHUTDOWN_DURATION_SECONDS.observe(total_duration)

        self._raise_lifecycle_event(LifecycleEvent.SHUTDOWN)
        self._raise_lifecycle_event(LifecycleEvent.AFTER_SHUTDOWN)

    def _raise_lifecycle_event(self, event: LifecycleEvent) -> None:
        for callback in self._lifecycle_notifications:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Error in lifecycle event notification: {e}")
