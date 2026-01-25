"""Tests for graceful shutdown coordinator."""

import threading
import time
from typing import Any
from unittest.mock import MagicMock


class TestShutdownCoordinator:
    """Tests for ShutdownCoordinator functionality."""

    def test_initial_state_not_shutting_down(self) -> None:
        """Test coordinator starts in non-shutdown state."""
        from common.core.shutdown import ShutdownCoordinator

        coordinator = ShutdownCoordinator(graceful_shutdown_timeout=5)

        assert coordinator.is_shutting_down() is False

    def test_shutdown_sets_flag(self) -> None:
        """Test shutdown sets the shutting down flag."""
        from common.core.shutdown import ShutdownCoordinator

        coordinator = ShutdownCoordinator(graceful_shutdown_timeout=5)

        coordinator.shutdown()

        assert coordinator.is_shutting_down() is True

    def test_shutdown_is_idempotent(self) -> None:
        """Test calling shutdown multiple times is safe."""
        from common.core.shutdown import ShutdownCoordinator

        coordinator = ShutdownCoordinator(graceful_shutdown_timeout=5)

        coordinator.shutdown()
        coordinator.shutdown()
        coordinator.shutdown()

        assert coordinator.is_shutting_down() is True

    def test_register_lifetime_notification(self) -> None:
        """Test registering and receiving lifetime notifications."""
        from common.core.shutdown import ShutdownCoordinator, LifetimeEvent

        coordinator = ShutdownCoordinator(graceful_shutdown_timeout=5)

        received_events: list[LifetimeEvent] = []

        def callback(event: LifetimeEvent) -> None:
            received_events.append(event)

        coordinator.register_lifetime_notification(callback)
        coordinator.shutdown()

        # Should receive PREPARE_SHUTDOWN event
        assert LifetimeEvent.PREPARE_SHUTDOWN in received_events

    def test_register_shutdown_waiter(self) -> None:
        """Test registering a shutdown waiter."""
        from common.core.shutdown import ShutdownCoordinator

        coordinator = ShutdownCoordinator(graceful_shutdown_timeout=5)

        waiter_called = threading.Event()

        def waiter(timeout: float) -> bool:
            waiter_called.set()
            return True

        coordinator.register_shutdown_waiter("TestWaiter", waiter)

        # Waiter should be called during shutdown
        # (We don't fully test shutdown sequence here to avoid complexity)
        assert "TestWaiter" in coordinator._shutdown_waiters

    def test_shutdown_completes_with_waiters(self) -> None:
        """Test shutdown completes when waiters are ready."""
        from common.core.shutdown import ShutdownCoordinator

        coordinator = ShutdownCoordinator(graceful_shutdown_timeout=5)

        def fast_waiter(timeout: float) -> bool:
            return True  # Immediately ready

        coordinator.register_shutdown_waiter("FastWaiter", fast_waiter)

        # Should return quickly since waiter returns True immediately
        start = time.time()
        coordinator.shutdown()
        elapsed = time.time() - start

        assert elapsed < 1.0  # Should complete quickly
        assert coordinator.is_shutting_down() is True


class TestLifetimeEvent:
    """Tests for LifetimeEvent enum."""

    def test_lifetime_events_exist(self) -> None:
        """Test expected lifetime events are defined."""
        from common.core.shutdown import LifetimeEvent

        assert hasattr(LifetimeEvent, "PREPARE_SHUTDOWN")
        assert hasattr(LifetimeEvent, "SHUTDOWN")

    def test_lifetime_events_are_distinct(self) -> None:
        """Test lifetime events have distinct values."""
        from common.core.shutdown import LifetimeEvent

        events = [LifetimeEvent.PREPARE_SHUTDOWN, LifetimeEvent.SHUTDOWN]
        values = [e.value for e in events]

        assert len(values) == len(set(values))
