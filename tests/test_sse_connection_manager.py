"""Unit tests for SSEConnectionManager service."""

from unittest.mock import Mock, patch

import pytest

from app.services.sse_connection_manager import SSEConnectionManager


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


class TestBroadcastSend:
    """Tests for broadcast send functionality."""

    @patch("app.services.sse_connection_manager.requests.post")
    def test_broadcast_to_all_connections(self, mock_post, sse_connection_manager):
        sse_connection_manager.on_connect("req1", "token-1", "/api/sse/stream?request_id=req1")
        sse_connection_manager.on_connect("req2", "token-2", "/api/sse/stream?request_id=req2")
        sse_connection_manager.on_connect("req3", "token-3", "/api/sse/stream?request_id=req3")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        result = sse_connection_manager.send_event(
            None,
            {"version": "1.2.3"},
            event_name="version",
            service_type="version",
        )

        assert result is True
        assert mock_post.call_count == 3

    def test_broadcast_with_no_connections_returns_false(self, sse_connection_manager):
        result = sse_connection_manager.send_event(
            None,
            {"version": "1.2.3"},
            event_name="version",
            service_type="version",
        )
        assert result is False


class TestSubjectFilteredBroadcast:
    """Tests for broadcast send with target_subject filtering."""

    @patch("app.services.sse_connection_manager.requests.post")
    def test_broadcast_with_target_subject_filters_by_identity(
        self, mock_post, sse_connection_manager
    ):
        """Only connections with matching subject receive the event."""
        sse_connection_manager.on_connect("req-alice", "tok-a", "/api/sse/stream?request_id=req-alice")
        sse_connection_manager.on_connect("req-bob", "tok-b", "/api/sse/stream?request_id=req-bob")
        sse_connection_manager.bind_identity("req-alice", "alice")
        sse_connection_manager.bind_identity("req-bob", "bob")

        mock_post.return_value = Mock(status_code=200)

        result = sse_connection_manager.send_event(
            None,
            {"task_id": "t1"},
            event_name="task_event",
            service_type="task",
            target_subject="alice",
        )

        assert result is True
        assert mock_post.call_count == 1
        # Verify the token sent to belongs to alice
        call_body = mock_post.call_args_list[0][1]["json"]
        assert call_body["token"] == "tok-a"

    @patch("app.services.sse_connection_manager.requests.post")
    def test_broadcast_with_target_subject_includes_local_user_sentinel(
        self, mock_post, sse_connection_manager
    ):
        """Connections bound with 'local-user' sentinel always receive events."""
        sse_connection_manager.on_connect("req1", "tok-1", "/api/sse/stream?request_id=req1")
        sse_connection_manager.on_connect("req2", "tok-2", "/api/sse/stream?request_id=req2")
        sse_connection_manager.bind_identity("req1", "alice")
        sse_connection_manager.bind_identity("req2", "local-user")

        mock_post.return_value = Mock(status_code=200)

        result = sse_connection_manager.send_event(
            None,
            {"task_id": "t1"},
            event_name="task_event",
            service_type="task",
            target_subject="bob",  # Neither connection is "bob"
        )

        assert result is True
        # Only local-user connection should receive
        assert mock_post.call_count == 1
        call_body = mock_post.call_args_list[0][1]["json"]
        assert call_body["token"] == "tok-2"

    @patch("app.services.sse_connection_manager.requests.post")
    def test_broadcast_without_target_subject_sends_to_all(
        self, mock_post, sse_connection_manager
    ):
        """Without target_subject, broadcast reaches all connections."""
        sse_connection_manager.on_connect("req1", "tok-1", "/api/sse/stream?request_id=req1")
        sse_connection_manager.on_connect("req2", "tok-2", "/api/sse/stream?request_id=req2")
        sse_connection_manager.bind_identity("req1", "alice")
        sse_connection_manager.bind_identity("req2", "bob")

        mock_post.return_value = Mock(status_code=200)

        result = sse_connection_manager.send_event(
            None,
            {"version": "1.0"},
            event_name="version",
            service_type="version",
        )

        assert result is True
        assert mock_post.call_count == 2

    @patch("app.services.sse_connection_manager.requests.post")
    def test_broadcast_with_target_subject_no_match_returns_false(
        self, mock_post, sse_connection_manager
    ):
        """When no connections match target_subject, returns False."""
        sse_connection_manager.on_connect("req1", "tok-1", "/api/sse/stream?request_id=req1")
        sse_connection_manager.bind_identity("req1", "alice")

        result = sse_connection_manager.send_event(
            None,
            {"task_id": "t1"},
            event_name="task_event",
            service_type="task",
            target_subject="bob",
        )

        assert result is False
        assert mock_post.call_count == 0
