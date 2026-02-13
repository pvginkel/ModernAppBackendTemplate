"""Shared testing utilities for lifecycle coordinator stubs."""

import logging
from collections.abc import Callable

from app.utils.lifecycle_coordinator import LifecycleCoordinatorProtocol, LifecycleEvent


class StubLifecycleCoordinator(LifecycleCoordinatorProtocol):
    """Basic lifecycle coordinator stub for testing.

    This stub only stores registrations and maintains state - it never
    executes callbacks or waiters. Use this for unit tests that just
    need dependency injection without lifecycle behavior testing.
    """

    def __init__(self):
        self._shutting_down = False
        self._notifications: list[Callable[[LifecycleEvent], None]] = []
        self._waiters: dict[str, Callable[[float], bool]] = {}

    def initialize(self) -> None:
        """Initialize (noop)."""
        pass

    def register_lifecycle_notification(self, callback: Callable[[LifecycleEvent], None]) -> None:
        """Store notification callback."""
        self._notifications.append(callback)

    def register_shutdown_waiter(self, name: str, handler: Callable[[float], bool]) -> None:
        """Store shutdown waiter."""
        self._waiters[name] = handler

    def is_shutting_down(self) -> bool:
        """Return current shutdown state."""
        return self._shutting_down

    def shutdown(self) -> None:
        """Implements the shutdown process."""
        pass

    def fire_startup(self) -> None:
        """Fire startup (noop for stub)."""
        pass


class TestLifecycleCoordinator(StubLifecycleCoordinator):
    """Enhanced lifecycle coordinator stub with controllable execution.

    This extends the basic stub with methods to simulate lifecycle behavior
    for integration testing. Use this when you need to test actual shutdown
    sequences and callback execution.
    """

    __test__ = False

    def simulate_startup(self) -> None:
        """Simulate startup - executes STARTUP callbacks."""
        for callback in self._notifications:
            try:
                callback(LifecycleEvent.STARTUP)
            except Exception as e:
                logging.getLogger(__name__).error(f"Error in test startup callback: {e}")

    def simulate_shutdown(self) -> None:
        """Simulate shutdown - sets state AND executes PREPARE_SHUTDOWN callbacks."""
        self._shutting_down = True
        for callback in self._notifications:
            try:
                callback(LifecycleEvent.PREPARE_SHUTDOWN)
            except Exception as e:
                logging.getLogger(__name__).error(f"Error in test shutdown callback: {e}")

    def simulate_full_shutdown(self, timeout: float = 30.0) -> None:
        """Simulate full shutdown including waiter execution and SHUTDOWN callbacks."""
        self.simulate_shutdown()

        for name, waiter in self._waiters.items():
            try:
                waiter(timeout)
            except Exception as e:
                logging.getLogger(__name__).error(f"Error in test waiter {name}: {e}")

        for callback in self._notifications:
            try:
                callback(LifecycleEvent.SHUTDOWN)
            except Exception as e:
                logging.getLogger(__name__).error(f"Error in test shutdown complete callback: {e}")
