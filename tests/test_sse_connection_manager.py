"""Unit tests for SSEConnectionManager: the registry, targets, subscriptions, the queue cap.

Tests connect with ``on_connect`` directly, as a browser's stream does, and
read what a connection was sent off its queue. tests/api/test_sse_stream.py
covers the stream route that drives the manager.
"""

import threading
from dataclasses import FrozenInstanceError
from queue import Empty
from unittest.mock import Mock

import pytest

from app.services.sse_connection_manager import (
    BROADCAST_TARGET,
    LOCAL_ADMIN_TARGET,
    SSE_ACTIVE_CONNECTIONS,
    SSE_CONNECTIONS_TOTAL,
    SSE_EVENTS_SENT_TOTAL,
    SSEConnection,
    SSEConnectionManager,
    Target,
    format_event_frame,
)
from app.utils.lifecycle_coordinator import LifecycleEvent
from tests.testing_utils import StubLifecycleCoordinator, TestLifecycleCoordinator


@pytest.fixture
def lifecycle() -> TestLifecycleCoordinator:
    return TestLifecycleCoordinator()


@pytest.fixture
def manager(lifecycle: TestLifecycleCoordinator) -> SSEConnectionManager:
    return SSEConnectionManager(lifecycle)


def _frames(connection: SSEConnection) -> list[str]:
    """Take every frame queued on the connection, without waiting."""
    frames: list[str] = []
    while True:
        try:
            frame = connection.next_frame(0)
        except Empty:
            return frames
        if frame is None:
            return frames
        frames.append(frame)


def _send(manager: SSEConnectionManager, target: Target) -> bool:
    return manager.send_event(target, {"x": 1}, event_name="task_event", service_type="task")


class TestConnect:
    def test_registers_the_connection_bound_to_its_identity(self, manager):
        connection = manager.on_connect("abc123", "alice")

        assert manager.has_connection("abc123")
        assert connection.request_id == "abc123"
        info = manager.get_connection_info("abc123")
        assert info is not None
        assert (info.request_id, info.subject, info.local_admin) == ("abc123", "alice", False)

    def test_local_admin(self, manager):
        manager.on_connect("abc123", "local-user", local_admin=True)

        info = manager.get_connection_info("abc123")
        assert info is not None
        assert info.local_admin

    def test_unknown_connection_has_no_info(self, manager):
        assert manager.get_connection_info("nope") is None
        assert not manager.has_connection("nope")

    def test_notifies_observers(self, manager):
        observer1 = Mock()
        observer2 = Mock()
        manager.register_on_connect(observer1)
        manager.register_on_connect(observer2)

        manager.on_connect("abc123", "alice")

        observer1.assert_called_once_with("abc123")
        observer2.assert_called_once_with("abc123")

    def test_observers_fire_once_the_identity_is_bound(self, manager):
        """A connect observer reaches the new connection through its subject."""
        manager.register_on_connect(lambda request_id: _send(manager, Target.subject("alice")))

        connection = manager.on_connect("abc123", "alice")

        assert _frames(connection) == [format_event_frame("task_event", {"x": 1})]

    def test_observer_exception_isolated(self, manager):
        failing_observer = Mock(side_effect=Exception("Observer crashed"))
        working_observer = Mock()
        manager.register_on_connect(failing_observer)
        manager.register_on_connect(working_observer)

        manager.on_connect("abc123", "alice")

        assert manager.has_connection("abc123")
        failing_observer.assert_called_once_with("abc123")
        working_observer.assert_called_once_with("abc123")


class TestReplacement:
    """A second connect with the same request_id replaces the first."""

    def test_the_old_connection_closes_and_the_new_one_is_current(self, manager):
        old = manager.on_connect("abc123", "alice")
        new = manager.on_connect("abc123", "bob")

        assert old.closed
        assert old.next_frame(0) is None
        assert not new.closed
        info = manager.get_connection_info("abc123")
        assert info is not None
        assert info.subject == "bob"

    def test_the_new_connection_starts_with_its_own_identity(self, manager):
        manager.on_connect("abc123", "local-user", local_admin=True)
        manager.subscribe("abc123", "news")

        new = manager.on_connect("abc123", "alice")

        assert not new.local_admin
        assert new.channels == set()
        assert not _send(manager, LOCAL_ADMIN_TARGET)
        assert not _send(manager, Target.channel("news"))

    def test_the_old_connection_going_keeps_the_new_one(self, manager):
        disconnected = Mock()
        manager.register_on_disconnect(disconnected)
        old = manager.on_connect("abc123", "alice")
        new = manager.on_connect("abc123", "alice")

        manager.on_disconnect(old)

        assert manager.has_connection("abc123")
        disconnected.assert_not_called()
        assert _send(manager, Target.connection("abc123"))
        assert len(_frames(new)) == 1


class TestDisconnect:
    def test_removes_and_notifies(self, manager):
        disconnected = Mock()
        manager.register_on_disconnect(disconnected)
        connection = manager.on_connect("abc123", "alice")

        manager.on_disconnect(connection)

        assert not manager.has_connection("abc123")
        assert connection.closed
        disconnected.assert_called_once_with("abc123")

    def test_is_idempotent(self, manager):
        disconnected = Mock()
        manager.register_on_disconnect(disconnected)
        connection = manager.on_connect("abc123", "alice")

        manager.on_disconnect(connection)
        manager.on_disconnect(connection)

        disconnected.assert_called_once_with("abc123")

    def test_observer_exception_isolated(self, manager):
        failing_observer = Mock(side_effect=Exception("Observer crashed"))
        working_observer = Mock()
        manager.register_on_disconnect(failing_observer)
        manager.register_on_disconnect(working_observer)

        manager.on_disconnect(manager.on_connect("abc123", "alice"))

        assert not manager.has_connection("abc123")
        working_observer.assert_called_once_with("abc123")


class TestTarget:
    """The typed target: factories, constants, value semantics."""

    def test_no_broadcast_factory(self):
        """Broadcast is the BROADCAST_TARGET constant, never a casual factory call."""
        assert not hasattr(Target, "broadcast")

    def test_factories(self):
        assert Target.connection("abc") == Target("connection", "abc")
        assert Target.subject("alice") == Target("subject", "alice")
        assert Target.channel("news") == Target("channel", "news")

    def test_constants(self):
        assert BROADCAST_TARGET == Target("broadcast")
        assert BROADCAST_TARGET.value is None
        assert LOCAL_ADMIN_TARGET == Target("local_admin")
        assert LOCAL_ADMIN_TARGET.value is None

    def test_equality_and_hash(self):
        assert Target.subject("alice") == Target.subject("alice")
        assert Target.subject("alice") != Target.subject("bob")
        assert Target.subject("abc") != Target.connection("abc")
        assert Target.channel("abc") != Target.subject("abc")
        assert BROADCAST_TARGET != LOCAL_ADMIN_TARGET
        assert len({Target.subject("alice"), Target.subject("alice")}) == 1

    def test_repr(self):
        assert repr(Target.subject("alice")) == "Target(kind='subject', value='alice')"

    def test_immutable(self):
        target = Target.subject("alice")
        with pytest.raises(FrozenInstanceError):
            target.value = "bob"  # type: ignore[misc]

    @pytest.mark.parametrize("factory", [Target.connection, Target.subject, Target.channel])
    def test_factories_refuse_an_empty_value(self, factory):
        with pytest.raises(ValueError):
            factory("")


class TestSendEvent:
    """send_event delivers to exactly the connections its target names."""

    @pytest.fixture
    def connections(self, manager) -> dict[str, SSEConnection]:
        """alice, bob, the local admin (OIDC off), and a connection bound to nobody."""
        return {
            "alice": manager.on_connect("req-alice", "alice"),
            "bob": manager.on_connect("req-bob", "bob"),
            "admin": manager.on_connect("req-admin", "local-user", local_admin=True),
            "unbound": manager.on_connect("req-unbound", None),
        }

    def _reached(self, connections: dict[str, SSEConnection]) -> list[str]:
        return sorted(name for name, connection in connections.items() if _frames(connection))

    def test_frame_is_the_envelope_the_browser_reads(self, manager, connections):
        manager.send_event(
            Target.connection("req-alice"),
            {"task_id": "t1", "data": {"text": "héllo"}},
            event_name="task_event",
            service_type="task",
        )

        assert _frames(connections["alice"]) == [
            'data: {"type":"task_event","payload":{"task_id": "t1", '
            '"data": {"text": "h\\u00e9llo"}}}\n\n'
        ]

    def test_connection_target_reaches_that_connection(self, manager, connections):
        assert _send(manager, Target.connection("req-bob"))
        assert self._reached(connections) == ["bob"]

    def test_connection_target_without_connection_returns_false(self, manager, connections):
        assert not _send(manager, Target.connection("req-nobody"))
        assert self._reached(connections) == []

    def test_subject_target_reaches_the_subject_and_the_local_admin(self, manager, connections):
        assert _send(manager, Target.subject("alice"))
        assert self._reached(connections) == ["admin", "alice"]

    def test_subject_target_with_no_bound_connection_reaches_only_the_local_admin(
        self, manager, connections
    ):
        assert _send(manager, Target.subject("carol"))
        assert self._reached(connections) == ["admin"]

    def test_local_admin_target_reaches_only_the_local_admin(self, manager, connections):
        assert _send(manager, LOCAL_ADMIN_TARGET)
        assert self._reached(connections) == ["admin"]

    def test_broadcast_target_reaches_every_connection(self, manager, connections):
        assert _send(manager, BROADCAST_TARGET)
        assert self._reached(connections) == ["admin", "alice", "bob", "unbound"]

    def test_no_connections_returns_false(self, manager):
        assert not _send(manager, BROADCAST_TARGET)

    @pytest.mark.parametrize("target", [None, "req-alice"])
    def test_a_non_target_is_refused(self, manager, connections, target):
        with pytest.raises(TypeError, match="needs a Target"):
            manager.send_event(target, {}, event_name="task_event", service_type="task")

    def test_counts_each_recipient(self, manager, connections):
        success = SSE_EVENTS_SENT_TOTAL.labels(service="task", status="success")
        before = success._value.get()

        _send(manager, Target.subject("alice"))

        assert success._value.get() == before + 2


class TestLocalAdminBinding:
    """Only the flag makes a connection the local admin's, and it never outlives its binding."""

    def test_local_user_subject_without_the_flag_is_an_ordinary_subject(self, manager):
        """With OIDC on, a JWT whose sub is local-user is just that subject."""
        impostor = manager.on_connect("req-lu", "local-user")
        alice = manager.on_connect("req-alice", "alice")

        assert not _send(manager, LOCAL_ADMIN_TARGET)
        assert _send(manager, Target.subject("bob")) is False
        assert _send(manager, Target.subject("alice"))
        assert _frames(impostor) == []
        assert len(_frames(alice)) == 1

    def test_rebinding_without_the_flag_drops_it(self, manager):
        manager.on_connect("req-1", "local-user", local_admin=True)

        manager.bind_identity("req-1", "alice")

        info = manager.get_connection_info("req-1")
        assert info is not None
        assert (info.subject, info.local_admin) == ("alice", False)
        assert not _send(manager, LOCAL_ADMIN_TARGET)

    def test_binding_an_unknown_connection_changes_nothing(self, manager):
        manager.bind_identity("req-nobody", "alice")

        assert not manager.has_connection("req-nobody")


class TestSubscriptions:
    def test_subscribing_an_unknown_connection_is_false(self, manager):
        assert manager.subscribe("req-nobody", "news") is False

    def test_channel_target_reaches_the_subscribed_connections_only(self, manager):
        alice = manager.on_connect("req-alice", "alice")
        bob = manager.on_connect("req-bob", "bob")
        admin = manager.on_connect("req-admin", "local-user", local_admin=True)

        assert manager.subscribe("req-alice", "news")
        assert manager.subscribe("req-alice", "weather")

        assert _send(manager, Target.channel("news"))
        assert len(_frames(alice)) == 1
        # The local admin gets no channel's events unsubscribed
        assert _frames(bob) == _frames(admin) == []

    def test_channel_without_subscribers_returns_false(self, manager):
        manager.on_connect("req-alice", "alice")

        assert not _send(manager, Target.channel("news"))

    def test_unsubscribe(self, manager):
        alice = manager.on_connect("req-alice", "alice")
        manager.subscribe("req-alice", "news")

        manager.unsubscribe("req-alice", "news")
        manager.unsubscribe("req-alice", "never-subscribed")
        manager.unsubscribe("req-nobody", "news")

        assert not _send(manager, Target.channel("news"))
        assert _frames(alice) == []

    def test_a_subscription_goes_with_its_connection(self, manager):
        connection = manager.on_connect("req-alice", "alice")
        manager.subscribe("req-alice", "news")

        manager.on_disconnect(connection)
        manager.on_connect("req-alice", "alice")

        assert not _send(manager, Target.channel("news"))

    def test_an_empty_channel_is_refused(self, manager):
        manager.on_connect("req-alice", "alice")

        with pytest.raises(ValueError):
            manager.subscribe("req-alice", "")


class TestQueueCap:
    """A connection whose queue is full has stalled: it is closed, and its browser reconnects."""

    @pytest.fixture
    def manager(self, lifecycle: TestLifecycleCoordinator) -> SSEConnectionManager:
        return SSEConnectionManager(lifecycle, queue_max_frames=2)

    def test_a_full_queue_closes_the_connection(self, manager):
        disconnected = Mock()
        manager.register_on_disconnect(disconnected)
        stalled = manager.on_connect("req-stalled", "alice")
        dropped = SSE_EVENTS_SENT_TOTAL.labels(service="task", status="dropped")
        before = dropped._value.get()

        assert _send(manager, Target.connection("req-stalled"))
        assert _send(manager, Target.connection("req-stalled"))
        assert not _send(manager, Target.connection("req-stalled"))

        assert stalled.closed
        assert stalled.next_frame(0) is None
        assert not manager.has_connection("req-stalled")
        disconnected.assert_called_once_with("req-stalled")
        assert dropped._value.get() == before + 1

    def test_other_connections_still_get_the_event(self, manager):
        stalled = manager.on_connect("req-stalled", "alice")
        reading = manager.on_connect("req-reading", "bob")
        _send(manager, Target.connection("req-stalled"))
        _send(manager, Target.connection("req-stalled"))

        assert _send(manager, BROADCAST_TARGET)

        assert stalled.closed
        assert len(_frames(reading)) == 1
        assert manager.has_connection("req-reading")

    def test_a_reader_that_keeps_up_never_hits_the_cap(self, manager):
        connection = manager.on_connect("req-1", "alice")

        for _ in range(5):
            assert _send(manager, Target.connection("req-1"))
            assert len(_frames(connection)) == 1

        assert not connection.closed


class TestShutdown:
    def test_prepare_shutdown_closes_every_connection(self, manager, lifecycle):
        disconnected = Mock()
        manager.register_on_disconnect(disconnected)
        alice = manager.on_connect("req-alice", "alice")
        bob = manager.on_connect("req-bob", "bob")

        lifecycle.simulate_shutdown()

        assert alice.closed and bob.closed
        assert alice.next_frame(0) is None
        # Each stream's end unregisters its connection
        manager.on_disconnect(alice)
        manager.on_disconnect(bob)
        assert not manager.has_connection("req-alice")
        assert disconnected.call_count == 2

    def test_no_connection_is_accepted_after_it(self, manager, lifecycle):
        connected = Mock()
        manager.register_on_connect(connected)
        lifecycle.simulate_shutdown()

        connection = manager.on_connect("req-late", "alice")

        assert connection.closed
        assert not manager.has_connection("req-late")
        connected.assert_not_called()

    def test_other_lifecycle_events_change_nothing(self, manager, lifecycle):
        connection = manager.on_connect("req-1", "alice")

        lifecycle.simulate_startup()

        assert not connection.closed

    def test_registers_with_the_coordinator(self):
        lifecycle = StubLifecycleCoordinator()

        SSEConnectionManager(lifecycle)

        assert len(lifecycle._notifications) == 1
        lifecycle._notifications[0](LifecycleEvent.SHUTDOWN)


class TestConnectionQueue:
    def test_idle_raises_empty(self, manager):
        connection = manager.on_connect("req-1", "alice")

        with pytest.raises(Empty):
            connection.next_frame(0.01)

    def test_close_wakes_a_blocked_reader(self, manager):
        connection = manager.on_connect("req-1", "alice")
        result: list[str | None] = []
        reader = threading.Thread(target=lambda: result.append(connection.next_frame(10)))
        reader.start()

        connection.close()
        reader.join(timeout=2)

        assert not reader.is_alive()
        assert result == [None]

    def test_frames_queued_before_the_close_are_dropped(self, manager):
        connection = manager.on_connect("req-1", "alice")
        _send(manager, Target.connection("req-1"))

        connection.close()

        assert connection.next_frame(0) is None


class TestMetrics:
    def test_connections_are_counted(self, manager):
        connect = SSE_CONNECTIONS_TOTAL.labels(action="connect")
        disconnect = SSE_CONNECTIONS_TOTAL.labels(action="disconnect")
        connects, disconnects = connect._value.get(), disconnect._value.get()

        first = manager.on_connect("req-1", "alice")
        manager.on_connect("req-2", "bob")
        assert SSE_ACTIVE_CONNECTIONS._value.get() == 2

        # A replaced connection leaves the registry: a disconnect, no change in the gauge
        manager.on_connect("req-1", "alice")
        assert SSE_ACTIVE_CONNECTIONS._value.get() == 2
        manager.on_disconnect(first)

        manager.on_disconnect(manager.on_connect("req-2", "bob"))
        assert SSE_ACTIVE_CONNECTIONS._value.get() == 1
        assert connect._value.get() == connects + 4
        assert disconnect._value.get() == disconnects + 3
