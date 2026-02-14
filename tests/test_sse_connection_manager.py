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
