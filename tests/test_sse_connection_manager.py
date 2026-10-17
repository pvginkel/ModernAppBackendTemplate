"""Unit tests for SSEConnectionManager service."""

from dataclasses import FrozenInstanceError
from unittest.mock import Mock, patch

import pytest

from app.services.health_service import CheckResult, CheckStatus
from app.services.sse_connection_manager import (
    BROADCAST_TARGET,
    LOCAL_ADMIN_TARGET,
    SSEConnectionManager,
    Target,
)


@pytest.fixture
def sse_connection_manager():
    """Create SSEConnectionManager instance for testing."""
    return SSEConnectionManager(
        gateway_url="http://localhost:3000",
        http_timeout=5.0,
    )


class TestSSEConnectionManagerConnect:
    """Tests for connection registration."""

    def test_on_connect_new_connection(self, sse_connection_manager):
        request_id = "abc123"
        token = "token-1"
        url = "/api/sse/stream?request_id=abc123"

        sse_connection_manager.on_connect(request_id, token, url)

        assert sse_connection_manager.has_connection(request_id)
        assert sse_connection_manager._connections[request_id] == {
            "token": token,
            "url": url,
        }
        assert sse_connection_manager._token_to_request_id[token] == request_id

    def test_on_connect_notifies_observers(self, sse_connection_manager):
        observer1 = Mock()
        observer2 = Mock()
        sse_connection_manager.register_on_connect(observer1)
        sse_connection_manager.register_on_connect(observer2)

        sse_connection_manager.on_connect("abc123", "token-1", "/api/sse/stream?request_id=abc123")

        observer1.assert_called_once_with("abc123")
        observer2.assert_called_once_with("abc123")

    def test_on_connect_observer_exception_isolated(self, sse_connection_manager):
        failing_observer = Mock(side_effect=Exception("Observer crashed"))
        working_observer = Mock()
        sse_connection_manager.register_on_connect(failing_observer)
        sse_connection_manager.register_on_connect(working_observer)

        sse_connection_manager.on_connect("abc123", "token-1", "/api/sse/stream?request_id=abc123")

        assert sse_connection_manager.has_connection("abc123")
        working_observer.assert_called_once_with("abc123")
        failing_observer.assert_called_once_with("abc123")


def _connect(manager: SSEConnectionManager, request_id: str) -> None:
    manager.on_connect(request_id, f"tok-{request_id}", f"/api/sse/stream?request_id={request_id}")


def _tokens_sent(mock_post: Mock) -> list[str]:
    return [call.kwargs["json"]["token"] for call in mock_post.call_args_list]


class TestTarget:
    """The typed target: factories, constants, value semantics."""

    def test_no_broadcast_factory(self):
        """Broadcast is the BROADCAST_TARGET constant, never a casual factory call."""
        assert not hasattr(Target, "broadcast")

    def test_factories(self):
        assert Target.connection("abc") == Target("connection", "abc")
        assert Target.subject("alice") == Target("subject", "alice")

    def test_constants(self):
        assert BROADCAST_TARGET == Target("broadcast")
        assert BROADCAST_TARGET.value is None
        assert LOCAL_ADMIN_TARGET == Target("local_admin")
        assert LOCAL_ADMIN_TARGET.value is None

    def test_equality_and_hash(self):
        assert Target.subject("alice") == Target.subject("alice")
        assert Target.subject("alice") != Target.subject("bob")
        assert Target.subject("abc") != Target.connection("abc")
        assert BROADCAST_TARGET != LOCAL_ADMIN_TARGET
        assert len({Target.subject("alice"), Target.subject("alice")}) == 1

    def test_repr(self):
        assert repr(Target.subject("alice")) == "Target(kind='subject', value='alice')"

    def test_immutable(self):
        target = Target.subject("alice")
        with pytest.raises(FrozenInstanceError):
            target.value = "bob"  # type: ignore[misc]

    @pytest.mark.parametrize("factory", [Target.connection, Target.subject])
    def test_factories_refuse_an_empty_value(self, factory):
        with pytest.raises(ValueError):
            factory("")


@patch("app.services.sse_connection_manager.requests.post")
class TestSendEvent:
    """send_event delivers to exactly the connections its target names."""

    @pytest.fixture(autouse=True)
    def _connections(self, sse_connection_manager):
        """alice, bob, and the local admin (OIDC off) are connected."""
        for request_id in ("req-alice", "req-bob", "req-admin"):
            _connect(sse_connection_manager, request_id)
        sse_connection_manager.bind_identity("req-alice", "alice")
        sse_connection_manager.bind_identity("req-bob", "bob")
        sse_connection_manager.bind_identity("req-admin", "local-user", local_admin=True)

    def _send(self, manager: SSEConnectionManager, target: Target) -> bool:
        return manager.send_event(target, {"x": 1}, event_name="task_event", service_type="task")

    def test_connection_target_reaches_that_connection(self, mock_post, sse_connection_manager):
        mock_post.return_value = Mock(status_code=200)

        assert self._send(sse_connection_manager, Target.connection("req-bob")) is True

        assert _tokens_sent(mock_post) == ["tok-req-bob"]
        body = mock_post.call_args.kwargs["json"]
        assert body["event"] == {"name": "task_event", "data": '{"x": 1}'}

    def test_connection_target_without_connection_returns_false(
        self, mock_post, sse_connection_manager
    ):
        assert self._send(sse_connection_manager, Target.connection("req-gone")) is False
        mock_post.assert_not_called()

    def test_subject_target_reaches_the_subject_and_the_local_admin(
        self, mock_post, sse_connection_manager
    ):
        mock_post.return_value = Mock(status_code=200)

        assert self._send(sse_connection_manager, Target.subject("alice")) is True

        assert sorted(_tokens_sent(mock_post)) == ["tok-req-admin", "tok-req-alice"]

    def test_subject_target_with_no_bound_connection_reaches_only_the_local_admin(
        self, mock_post, sse_connection_manager
    ):
        mock_post.return_value = Mock(status_code=200)

        assert self._send(sse_connection_manager, Target.subject("carol")) is True

        assert _tokens_sent(mock_post) == ["tok-req-admin"]

    def test_local_admin_target_reaches_only_the_local_admin(
        self, mock_post, sse_connection_manager
    ):
        mock_post.return_value = Mock(status_code=200)

        assert self._send(sse_connection_manager, LOCAL_ADMIN_TARGET) is True

        assert _tokens_sent(mock_post) == ["tok-req-admin"]

    def test_broadcast_target_reaches_every_connection(self, mock_post, sse_connection_manager):
        _connect(sse_connection_manager, "req-unbound")
        mock_post.return_value = Mock(status_code=200)

        assert self._send(sse_connection_manager, BROADCAST_TARGET) is True

        assert sorted(_tokens_sent(mock_post)) == [
            "tok-req-admin",
            "tok-req-alice",
            "tok-req-bob",
            "tok-req-unbound",
        ]

    def test_unbound_connection_gets_no_targeted_events(self, mock_post, sse_connection_manager):
        _connect(sse_connection_manager, "req-unbound")
        mock_post.return_value = Mock(status_code=200)

        self._send(sse_connection_manager, Target.subject("alice"))
        self._send(sse_connection_manager, LOCAL_ADMIN_TARGET)

        assert "tok-req-unbound" not in _tokens_sent(mock_post)

    @pytest.mark.parametrize("target", [None, "req-alice"])
    def test_a_non_target_is_refused(self, mock_post, sse_connection_manager, target):
        """The old call shapes (None for broadcast, a bare request ID) fail loudly."""
        with pytest.raises(TypeError, match="needs a Target"):
            self._send(sse_connection_manager, target)
        mock_post.assert_not_called()

    def test_failed_sends_return_false(self, mock_post, sse_connection_manager):
        mock_post.return_value = Mock(status_code=500, text="boom")

        assert self._send(sse_connection_manager, Target.subject("alice")) is False


@patch("app.services.sse_connection_manager.requests.post")
class TestLocalAdminBinding:
    """Only the connect callback's local_admin flag makes a connection see every subject."""

    def test_local_user_subject_without_the_flag_is_an_ordinary_subject(
        self, mock_post, sse_connection_manager
    ):
        """With OIDC on, a JWT whose subject is "local-user" is not the local admin."""
        _connect(sse_connection_manager, "req-jwt")
        sse_connection_manager.bind_identity("req-jwt", "local-user")
        mock_post.return_value = Mock(status_code=200)

        assert sse_connection_manager.send_event(
            Target.subject("alice"), {}, event_name="e", service_type="task"
        ) is False
        assert sse_connection_manager.send_event(
            LOCAL_ADMIN_TARGET, {}, event_name="e", service_type="task"
        ) is False
        assert sse_connection_manager.send_event(
            Target.subject("local-user"), {}, event_name="e", service_type="task"
        ) is True
        assert _tokens_sent(mock_post) == ["tok-req-jwt"]

    def test_rebinding_without_the_flag_drops_it(self, mock_post, sse_connection_manager):
        _connect(sse_connection_manager, "req-1")
        sse_connection_manager.bind_identity("req-1", "local-user", local_admin=True)
        sse_connection_manager.bind_identity("req-1", "alice")

        assert sse_connection_manager.send_event(
            LOCAL_ADMIN_TARGET, {}, event_name="e", service_type="task"
        ) is False

    def test_reconnect_starts_unbound(self, mock_post, sse_connection_manager):
        """A replacement connection does not inherit the old one's identity."""
        mock_post.return_value = Mock(status_code=200)
        _connect(sse_connection_manager, "req-1")
        sse_connection_manager.bind_identity("req-1", "local-user", local_admin=True)

        sse_connection_manager.on_connect("req-1", "tok-new", "/api/sse/stream?request_id=req-1")
        mock_post.reset_mock()

        assert sse_connection_manager.get_connection_info("req-1").subject is None
        assert sse_connection_manager.send_event(
            LOCAL_ADMIN_TARGET, {}, event_name="e", service_type="task"
        ) is False
        mock_post.assert_not_called()

    def test_disconnect_drops_the_binding(self, mock_post, sse_connection_manager):
        _connect(sse_connection_manager, "req-1")
        sse_connection_manager.bind_identity("req-1", "local-user", local_admin=True)

        sse_connection_manager.on_disconnect("tok-req-1")

        assert "req-1" not in sse_connection_manager._identity_map
        assert "req-1" not in sse_connection_manager._local_admin_ids

    def test_gateway_404_drops_the_binding(self, mock_post, sse_connection_manager):
        _connect(sse_connection_manager, "req-1")
        sse_connection_manager.bind_identity("req-1", "local-user", local_admin=True)
        mock_post.return_value = Mock(status_code=404)

        assert sse_connection_manager.send_event(
            LOCAL_ADMIN_TARGET, {}, event_name="e", service_type="task"
        ) is False

        assert not sse_connection_manager.has_connection("req-1")
        assert "req-1" not in sse_connection_manager._identity_map
        assert "req-1" not in sse_connection_manager._local_admin_ids


class TestReadinessCheck:
    """The manager registers the gateway's readiness check itself."""

    def test_registers_a_non_critical_check(self):
        health_service = Mock()

        manager = SSEConnectionManager(
            gateway_url="http://localhost:3000", health_service=health_service
        )

        health_service.register_check.assert_called_once_with(
            "sse_gateway", manager.check_readiness, critical=False
        )

    def test_gateway_ready(self, sse_connection_manager):
        with patch("requests.get", return_value=Mock(status_code=200)) as get:
            assert sse_connection_manager.check_readiness() == CheckResult(CheckStatus.OK)

        get.assert_called_once_with("http://localhost:3000/readyz", timeout=5.0)

    def test_gateway_not_ready(self, sse_connection_manager):
        with patch("requests.get", return_value=Mock(status_code=503)):
            result = sse_connection_manager.check_readiness()

        assert result == CheckResult(CheckStatus.ERROR, "gateway readyz answered 503")

    def test_gateway_unreachable_raises(self, sse_connection_manager):
        with patch("requests.get", side_effect=ConnectionError("Connection refused")):
            with pytest.raises(ConnectionError):
                sse_connection_manager.check_readiness()
