"""Tests for lifecycle coordinator."""

import signal
import threading
import time
from unittest.mock import MagicMock

from app.utils.lifecycle_coordinator import LifecycleCoordinator, LifecycleEvent
from tests.testing_utils import TestLifecycleCoordinator


class TestProductionLifecycleCoordinator:
    """Test lifecycle coordinator functionality."""

    def test_lifecycle_event_sequence(self):
        coordinator = LifecycleCoordinator(graceful_shutdown_timeout=60)
        events_received = []

        def notification_callback(event: LifecycleEvent):
            events_received.append(event)

        coordinator.register_lifecycle_notification(notification_callback)
        coordinator._handle_sigterm(signal.SIGTERM, None)

        assert len(events_received) == 3
        assert events_received[0] == LifecycleEvent.PREPARE_SHUTDOWN
        assert events_received[1] == LifecycleEvent.SHUTDOWN
        assert events_received[2] == LifecycleEvent.AFTER_SHUTDOWN

    def test_multiple_notifications(self):
        coordinator = LifecycleCoordinator(graceful_shutdown_timeout=60)
        callback1 = MagicMock()
        callback2 = MagicMock()
        callback3 = MagicMock()
        coordinator.register_lifecycle_notification(callback1)
        coordinator.register_lifecycle_notification(callback2)
        coordinator.register_lifecycle_notification(callback3)

        coordinator._handle_sigterm(signal.SIGTERM, None)

        assert callback1.call_count == 3
        assert callback2.call_count == 3
        callback1.assert_any_call(LifecycleEvent.PREPARE_SHUTDOWN)
        callback1.assert_any_call(LifecycleEvent.SHUTDOWN)
        callback1.assert_any_call(LifecycleEvent.AFTER_SHUTDOWN)

    def test_shutdown_waiters_sequential_execution(self):
        coordinator = LifecycleCoordinator(graceful_shutdown_timeout=5)
        after_shutdown_attempted = False

        def lifecycle_notification(event: LifecycleEvent):
            nonlocal after_shutdown_attempted
            if event == LifecycleEvent.AFTER_SHUTDOWN:
                after_shutdown_attempted = True

        coordinator.register_lifecycle_notification(lifecycle_notification)

        waiter_calls = []

        def waiter1(timeout: float) -> bool:
            waiter_calls.append(("waiter1", timeout))
            time.sleep(1)
            return True

        def waiter2(timeout: float) -> bool:
            waiter_calls.append(("waiter2", timeout))
            time.sleep(0.5)
            return True

        coordinator.register_shutdown_waiter("Service1", waiter1)
        coordinator.register_shutdown_waiter("Service2", waiter2)

        start_time = time.perf_counter()
        coordinator._handle_sigterm(signal.SIGTERM, None)
        end_time = time.perf_counter()

        assert len(waiter_calls) == 2
        assert waiter_calls[0][0] == "waiter1"
        assert waiter_calls[1][0] == "waiter2"
        assert waiter_calls[1][1] < waiter_calls[0][1]
        total_time = end_time - start_time
        assert 1.4 < total_time < 2.0
        assert after_shutdown_attempted

    def test_is_shutting_down_state(self):
        coordinator = LifecycleCoordinator(graceful_shutdown_timeout=60)
        assert not coordinator.is_shutting_down()

        shutdown_state_during_notification = []

        def check_state_callback(event: LifecycleEvent):
            shutdown_state_during_notification.append((event, coordinator.is_shutting_down()))

        coordinator.register_lifecycle_notification(check_state_callback)

        thread = threading.Thread(
            target=lambda: coordinator._handle_sigterm(signal.SIGTERM, None)
        )
        thread.start()
        thread.join(timeout=5)

        assert len(shutdown_state_during_notification) == 3
        assert shutdown_state_during_notification[0] == (LifecycleEvent.PREPARE_SHUTDOWN, True)
        assert shutdown_state_during_notification[1] == (LifecycleEvent.SHUTDOWN, True)
        assert shutdown_state_during_notification[2] == (LifecycleEvent.AFTER_SHUTDOWN, True)

    def test_notification_exception_handling(self):
        coordinator = LifecycleCoordinator(graceful_shutdown_timeout=60)

        def bad_callback(event: LifecycleEvent):
            raise Exception("Test error")

        def good_callback(event: LifecycleEvent):
            good_callback.calls = getattr(good_callback, "calls", [])
            good_callback.calls.append(event)

        coordinator.register_lifecycle_notification(bad_callback)
        coordinator.register_lifecycle_notification(good_callback)

        coordinator._handle_sigterm(signal.SIGTERM, None)

        assert len(good_callback.calls) == 3
        assert good_callback.calls[0] == LifecycleEvent.PREPARE_SHUTDOWN
        assert good_callback.calls[1] == LifecycleEvent.SHUTDOWN
        assert good_callback.calls[2] == LifecycleEvent.AFTER_SHUTDOWN

    def test_waiter_exception_handling(self):
        coordinator = LifecycleCoordinator(graceful_shutdown_timeout=60)
        after_shutdown_attempted = False

        def lifecycle_notification(event: LifecycleEvent):
            nonlocal after_shutdown_attempted
            if event == LifecycleEvent.AFTER_SHUTDOWN:
                after_shutdown_attempted = True

        coordinator.register_lifecycle_notification(lifecycle_notification)

        def bad_waiter(timeout: float) -> bool:
            raise Exception("Waiter error")

        good_waiter_called = threading.Event()

        def good_waiter(timeout: float) -> bool:
            good_waiter_called.set()
            return True

        coordinator.register_shutdown_waiter("BadService", bad_waiter)
        coordinator.register_shutdown_waiter("GoodService", good_waiter)

        coordinator._handle_sigterm(signal.SIGTERM, None)
        assert good_waiter_called.is_set()
        assert after_shutdown_attempted

    def test_fire_startup_dispatches_event(self):
        coordinator = LifecycleCoordinator(graceful_shutdown_timeout=60)
        events_received: list[LifecycleEvent] = []

        def callback(event: LifecycleEvent) -> None:
            events_received.append(event)

        coordinator.register_lifecycle_notification(callback)
        coordinator.fire_startup()

        assert len(events_received) == 1
        assert events_received[0] == LifecycleEvent.STARTUP

    def test_fire_startup_idempotent(self):
        coordinator = LifecycleCoordinator(graceful_shutdown_timeout=60)
        call_count = 0

        def callback(event: LifecycleEvent) -> None:
            nonlocal call_count
            if event == LifecycleEvent.STARTUP:
                call_count += 1

        coordinator.register_lifecycle_notification(callback)
        coordinator.fire_startup()
        coordinator.fire_startup()
        assert call_count == 1

    def test_fire_startup_exception_handling(self):
        coordinator = LifecycleCoordinator(graceful_shutdown_timeout=60)

        def bad_callback(event: LifecycleEvent) -> None:
            raise Exception("Startup error")

        good_events: list[LifecycleEvent] = []

        def good_callback(event: LifecycleEvent) -> None:
            good_events.append(event)

        coordinator.register_lifecycle_notification(bad_callback)
        coordinator.register_lifecycle_notification(good_callback)
        coordinator.fire_startup()

        assert len(good_events) == 1
        assert good_events[0] == LifecycleEvent.STARTUP

    def test_full_lifecycle_with_startup(self):
        coordinator = LifecycleCoordinator(graceful_shutdown_timeout=60)
        events_received: list[LifecycleEvent] = []

        def callback(event: LifecycleEvent) -> None:
            events_received.append(event)

        coordinator.register_lifecycle_notification(callback)
        coordinator.fire_startup()
        coordinator._handle_sigterm(signal.SIGTERM, None)

        assert len(events_received) == 4
        assert events_received[0] == LifecycleEvent.STARTUP
        assert events_received[1] == LifecycleEvent.PREPARE_SHUTDOWN
        assert events_received[2] == LifecycleEvent.SHUTDOWN
        assert events_received[3] == LifecycleEvent.AFTER_SHUTDOWN


class TestNoopLifecycleCoordinator:
    """Test no-op lifecycle coordinator for testing."""

    def test_initialization(self):
        coordinator = TestLifecycleCoordinator()
        assert not coordinator.is_shutting_down()
        assert len(coordinator._notifications) == 0
        assert len(coordinator._waiters) == 0

    def test_register_notification(self):
        coordinator = TestLifecycleCoordinator()
        callback = MagicMock()
        coordinator.register_lifecycle_notification(callback)
        assert len(coordinator._notifications) == 1

    def test_register_waiter(self):
        coordinator = TestLifecycleCoordinator()
        waiter = MagicMock()
        coordinator.register_shutdown_waiter("TestService", waiter)
        assert "TestService" in coordinator._waiters

    def test_handle_sigterm_simulation(self):
        coordinator = TestLifecycleCoordinator()
        callback = MagicMock()
        waiter = MagicMock(return_value=True)
        coordinator.register_lifecycle_notification(callback)
        coordinator.register_shutdown_waiter("TestService", waiter)

        assert not coordinator.is_shutting_down()
        coordinator.simulate_full_shutdown()
        assert coordinator.is_shutting_down()
        assert callback.call_count == 2
        callback.assert_any_call(LifecycleEvent.PREPARE_SHUTDOWN)
        callback.assert_any_call(LifecycleEvent.SHUTDOWN)
        waiter.assert_called_once_with(30.0)

    def test_simulate_startup_method(self):
        coordinator = TestLifecycleCoordinator()
        events_received = []

        def callback(event: LifecycleEvent):
            events_received.append(event)

        coordinator.register_lifecycle_notification(callback)
        coordinator.simulate_startup()

        assert len(events_received) == 1
        assert events_received[0] == LifecycleEvent.STARTUP

    def test_exception_handling_in_callbacks(self):
        coordinator = TestLifecycleCoordinator()

        def bad_callback(event: LifecycleEvent):
            raise Exception("Test error")

        def good_callback(event: LifecycleEvent):
            good_callback.calls = getattr(good_callback, "calls", [])
            good_callback.calls.append(event)

        coordinator.register_lifecycle_notification(bad_callback)
        coordinator.register_lifecycle_notification(good_callback)
        coordinator.simulate_full_shutdown()
        assert len(good_callback.calls) == 2

    def test_interface_compatibility(self):
        noop = TestLifecycleCoordinator()
        assert callable(noop.initialize)
        assert callable(noop.register_lifecycle_notification)
        assert callable(noop.register_shutdown_waiter)
        assert callable(noop.is_shutting_down)
        assert callable(noop.simulate_full_shutdown)
        assert callable(noop.fire_startup)


class TestLifecycleIntegrationScenarios:
    """Test realistic shutdown scenarios."""

    def test_coordinated_service_shutdown(self):
        coordinator = LifecycleCoordinator(graceful_shutdown_timeout=60)
        event_sequence = []

        def service1_notification(event: LifecycleEvent):
            event_sequence.append(f"Service1:{event.value}")

        def service1_waiter(timeout: float) -> bool:
            event_sequence.append("Service1:waiter_complete")
            return True

        def service2_notification(event: LifecycleEvent):
            event_sequence.append(f"Service2:{event.value}")

        def service2_waiter(timeout: float) -> bool:
            time.sleep(0.1)
            event_sequence.append("Service2:waiter_complete")
            return True

        coordinator.register_lifecycle_notification(service1_notification)
        coordinator.register_shutdown_waiter("Service1", service1_waiter)
        coordinator.register_lifecycle_notification(service2_notification)
        coordinator.register_shutdown_waiter("Service2", service2_waiter)

        coordinator._handle_sigterm(signal.SIGTERM, None)

        assert "Service1:prepare-shutdown" in event_sequence
        assert "Service2:prepare-shutdown" in event_sequence
        assert "Service1:waiter_complete" in event_sequence
        assert "Service2:waiter_complete" in event_sequence

        prepare_indices = [
            i for i, event in enumerate(event_sequence) if "prepare-shutdown" in event
        ]
        waiter_indices = [
            i for i, event in enumerate(event_sequence) if "waiter_complete" in event
        ]
        assert max(prepare_indices) < min(waiter_indices)
