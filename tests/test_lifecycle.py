"""Tests for lifecycle coordinator."""

from app.utils.lifecycle_coordinator import LifecycleEvent


def test_startup_event_fires(app):
    """Application startup fires STARTUP lifecycle event and sets _started flag."""
    lifecycle = app.container.lifecycle_coordinator()
    assert lifecycle._started is True


def test_shutdown_sequence(app):
    """Shutdown fires PREPARE_SHUTDOWN then SHUTDOWN events in order."""
    lifecycle = app.container.lifecycle_coordinator()
    events_received = []

    def on_event(event: LifecycleEvent) -> None:
        events_received.append(event)

    lifecycle.register_lifecycle_notification(on_event)

    lifecycle.shutdown()

    assert LifecycleEvent.PREPARE_SHUTDOWN in events_received
    assert LifecycleEvent.SHUTDOWN in events_received
    # PREPARE_SHUTDOWN should come before SHUTDOWN
    prep_idx = events_received.index(LifecycleEvent.PREPARE_SHUTDOWN)
    shut_idx = events_received.index(LifecycleEvent.SHUTDOWN)
    assert prep_idx < shut_idx


def test_shutting_down_toggle(app):
    """Shutting down state can be checked via is_shutting_down()."""
    lifecycle = app.container.lifecycle_coordinator()
    assert lifecycle.is_shutting_down() is False
    lifecycle._shutting_down = True
    assert lifecycle.is_shutting_down() is True
    lifecycle._shutting_down = False
