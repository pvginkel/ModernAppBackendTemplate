"""Unit tests for ConnectionManager service."""

from unittest.mock import Mock, patch

import pytest


@pytest.fixture
def mock_metrics():
    """Create a mock metrics service."""
    metrics = Mock()
    metrics.record_sse_gateway_connection = Mock()
    metrics.record_sse_gateway_event = Mock()
    metrics.record_sse_gateway_send_duration = Mock()
    return metrics


@pytest.fixture
def connection_manager(mock_metrics):
    """Create ConnectionManager instance with mock metrics."""
    from common.sse.connection_manager import ConnectionManager

    return ConnectionManager(
        gateway_url="http://localhost:3000",
        metrics_service=mock_metrics,
        http_timeout=5.0,
    )


class TestConnectionManagerConnect:
    """Tests for connection registration."""

    def test_on_connect_new_connection(self, connection_manager, mock_metrics):
        """Test registering a new connection."""
        # Given no existing connection
        request_id = "abc123"
        token = "token-1"
        url = "/api/sse/stream?request_id=abc123"

        # When registering connection
        connection_manager.on_connect(request_id, token, url)

        # Then mapping is stored
        assert connection_manager.has_connection(request_id)
        assert connection_manager._connections[request_id] == {
            "token": token,
            "url": url,
        }
        assert connection_manager._token_to_request_id[token] == request_id

        # And metrics recorded
        mock_metrics.record_sse_gateway_connection.assert_called_once_with("connect")

    def test_on_connect_notifies_observers(self, connection_manager):
        """Test that on_connect notifies all registered observers."""
        # Given registered observers
        observer1 = Mock()
        observer2 = Mock()
        connection_manager.register_on_connect(observer1)
        connection_manager.register_on_connect(observer2)

        request_id = "abc123"
        token = "token-1"
        url = "/api/sse/stream?request_id=abc123"

        # When connection registered
        connection_manager.on_connect(request_id, token, url)

        # Then all observers notified
        observer1.assert_called_once_with(request_id)
        observer2.assert_called_once_with(request_id)

    def test_on_connect_observer_exception_isolated(self, connection_manager):
        """Test that observer exception doesn't break connection or other observers."""
        # Given first observer raises exception
        failing_observer = Mock(side_effect=Exception("Observer crashed"))
        working_observer = Mock()
        connection_manager.register_on_connect(failing_observer)
        connection_manager.register_on_connect(working_observer)

        request_id = "abc123"
        token = "token-1"
        url = "/api/sse/stream?request_id=abc123"

        # When connection registered (should not raise exception)
        connection_manager.on_connect(request_id, token, url)

        # Then connection still registered
        assert connection_manager.has_connection(request_id)

        # And second observer still called
        working_observer.assert_called_once_with(request_id)

        # And first observer was called (but raised)
        failing_observer.assert_called_once_with(request_id)


class TestConnectionManagerDisconnect:
    """Tests for disconnect handling."""

    def test_on_disconnect_removes_connection(self, connection_manager, mock_metrics):
        """Test that disconnect removes the connection mappings."""
        # Given an existing connection
        request_id = "abc123"
        token = "token-1"
        url = "/api/sse/stream?request_id=abc123"
        connection_manager.on_connect(request_id, token, url)
        mock_metrics.reset_mock()

        # When disconnect received
        connection_manager.on_disconnect(token)

        # Then connection removed
        assert not connection_manager.has_connection(request_id)
        assert token not in connection_manager._token_to_request_id

        # And metrics recorded
        mock_metrics.record_sse_gateway_connection.assert_called_once_with("disconnect")

    def test_on_disconnect_unknown_token_ignored(self, connection_manager, mock_metrics):
        """Test that disconnect for unknown token is silently ignored."""
        # When disconnect for unknown token
        connection_manager.on_disconnect("unknown-token")

        # Then no error and no metrics recorded
        mock_metrics.record_sse_gateway_connection.assert_not_called()


class TestBroadcastSend:
    """Tests for broadcast send functionality."""

    @patch("common.sse.connection_manager.requests.post")
    def test_broadcast_to_all_connections(
        self, mock_post, connection_manager, mock_metrics
    ):
        """Test broadcasting event to all connections."""
        # Given multiple active connections
        connection_manager.on_connect("req1", "token-1", "/api/sse/stream?request_id=req1")
        connection_manager.on_connect("req2", "token-2", "/api/sse/stream?request_id=req2")
        connection_manager.on_connect("req3", "token-3", "/api/sse/stream?request_id=req3")
        mock_metrics.reset_mock()

        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # When broadcasting event (request_id=None)
        event_data = {"version": "1.2.3"}
        result = connection_manager.send_event(
            None,  # Broadcast to all
            event_data,
            event_name="version",
            service_type="version",
        )

        # Then event sent to all 3 connections
        assert result is True
        assert mock_post.call_count == 3

    def test_broadcast_with_no_connections_returns_false(self, connection_manager):
        """Test broadcast returns False when no active connections."""
        # Given no active connections
        # When broadcasting event
        result = connection_manager.send_event(
            None,
            {"version": "1.2.3"},
            event_name="version",
            service_type="version",
        )

        # Then returns False, no error raised
        assert result is False


class TestTargetedSend:
    """Tests for targeted send functionality."""

    @patch("common.sse.connection_manager.requests.post")
    def test_send_to_specific_connection(
        self, mock_post, connection_manager, mock_metrics
    ):
        """Test sending event to a specific connection."""
        # Given an active connection
        request_id = "abc123"
        token = "token-1"
        connection_manager.on_connect(request_id, token, "/api/sse/stream?request_id=abc123")
        mock_metrics.reset_mock()

        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # When sending to specific request_id
        event_data = {"status": "completed"}
        result = connection_manager.send_event(
            request_id,
            event_data,
            event_name="task_event",
            service_type="task",
        )

        # Then event sent successfully
        assert result is True
        mock_post.assert_called_once()

        # And metrics recorded
        mock_metrics.record_sse_gateway_event.assert_called_once_with("task", "success")
        mock_metrics.record_sse_gateway_send_duration.assert_called_once()

    def test_send_to_unknown_connection_returns_false(
        self, connection_manager, mock_metrics
    ):
        """Test send to unknown request_id returns False."""
        # Given no connection for request_id
        # When sending
        result = connection_manager.send_event(
            "unknown-request-id",
            {"data": "test"},
            event_name="test",
            service_type="test",
        )

        # Then returns False
        assert result is False


class TestBroadcastTaskEvent:
    """Tests for BroadcasterProtocol implementation."""

    @patch("common.sse.connection_manager.requests.post")
    def test_broadcast_task_event(self, mock_post, connection_manager, mock_metrics):
        """Test BroadcasterProtocol broadcast_task_event method."""
        # Given an active connection
        connection_manager.on_connect("req1", "token-1", "/api/sse/stream?request_id=req1")
        mock_metrics.reset_mock()

        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # When broadcasting task event
        result = connection_manager.broadcast_task_event(
            task_id="task-123",
            event_type="completed",
            data={"task_id": "task-123", "status": "completed"},
        )

        # Then event broadcast
        assert result is True
        mock_post.assert_called_once()
