"""Tests for SSE Gateway callback API endpoint."""

from unittest.mock import Mock

import pytest

from app.services.sse_connection_manager import SSEConnectionManager


class TestSSECallbackAPI:
    """Test SSE Gateway callback endpoint."""

    @pytest.fixture
    def mock_sse_connection_manager(self):
        """Create mock SSEConnectionManager."""
        return Mock(spec=SSEConnectionManager)

    def test_connect_callback_extracts_request_id_and_calls_sse_connection_manager(
        self, client, app, mock_sse_connection_manager
    ):
        """Test connect callback extracts request_id and calls SSEConnectionManager."""
        payload = {
            "action": "connect",
            "token": "test-token-123",
            "request": {
                "url": "/api/sse/stream?request_id=abc123",
                "headers": {},
            },
        }

        with app.app_context():
            app.container.sse_connection_manager.override(mock_sse_connection_manager)

            response = client.post("/api/sse/callback", json=payload)

            assert response.status_code == 200
            mock_sse_connection_manager.on_connect.assert_called_once_with(
                "abc123",
                "test-token-123",
                "/api/sse/stream?request_id=abc123",
            )

    def test_connect_callback_returns_empty_json(
        self, client, app, mock_sse_connection_manager
    ):
        """Test connect callback returns empty JSON response."""
        payload = {
            "action": "connect",
            "token": "test-token",
            "request": {
                "url": "/api/sse/stream?request_id=test123",
                "headers": {},
            },
        }

        with app.app_context():
            app.container.sse_connection_manager.override(mock_sse_connection_manager)

            response = client.post("/api/sse/callback", json=payload)

            assert response.status_code == 200
            json_data = response.get_json()
            assert json_data == {}

    def test_disconnect_callback_calls_sse_connection_manager(
        self, client, app, mock_sse_connection_manager
    ):
        """Test disconnect callback calls SSEConnectionManager.on_disconnect."""
        payload = {
            "action": "disconnect",
            "token": "test-token-123",
            "reason": "client_disconnect",
            "request": {
                "url": "/api/sse/stream?request_id=abc123",
                "headers": {},
            },
        }

        with app.app_context():
            app.container.sse_connection_manager.override(mock_sse_connection_manager)

            response = client.post("/api/sse/callback", json=payload)

            assert response.status_code == 200
            mock_sse_connection_manager.on_disconnect.assert_called_once_with(
                "test-token-123"
            )

    def test_authentication_enforced_in_production_mode(self):
        """Test authentication function in production mode."""
        from app.api.sse import _authenticate_callback
        from app.config import Settings

        settings = Settings(
            flask_env="production",
            sse_callback_secret="my-secret-key",
            database_url="sqlite:///:memory:",
            secret_key="test",
        )

        assert _authenticate_callback(None, settings) is False
        assert _authenticate_callback("wrong-secret", settings) is False
        assert _authenticate_callback("my-secret-key", settings) is True

        settings_no_secret = Settings(
            flask_env="production",
            sse_callback_secret="",
            database_url="sqlite:///:memory:",
            secret_key="test",
        )
        assert _authenticate_callback("any-secret", settings_no_secret) is False

    def test_authentication_skipped_in_dev_mode(
        self, client, app, mock_sse_connection_manager
    ):
        """Test secret authentication skipped in development mode."""
        payload = {
            "action": "connect",
            "token": "test-token",
            "request": {
                "url": "/api/sse/stream?request_id=test123",
                "headers": {},
            },
        }

        with app.app_context():
            app.container.sse_connection_manager.override(mock_sse_connection_manager)

            response = client.post("/api/sse/callback", json=payload)
            assert response.status_code == 200

    def test_missing_request_id_returns_400(
        self, client, app, mock_sse_connection_manager
    ):
        """Test missing request_id query parameter returns 400."""
        payload = {
            "action": "connect",
            "token": "test-token",
            "request": {
                "url": "/api/sse/stream",
                "headers": {},
            },
        }

        with app.app_context():
            app.container.sse_connection_manager.override(mock_sse_connection_manager)

            response = client.post("/api/sse/callback", json=payload)

            assert response.status_code == 400
            json_data = response.get_json()
            assert "error" in json_data

    def test_invalid_json_returns_400(self, client, app, mock_sse_connection_manager):
        """Test invalid JSON payload returns 400."""
        with app.app_context():
            app.container.sse_connection_manager.override(mock_sse_connection_manager)

            response = client.post(
                "/api/sse/callback",
                data="not-valid-json",
                content_type="application/json",
            )

            assert response.status_code == 400

    def test_missing_json_body_returns_400(
        self, client, app, mock_sse_connection_manager
    ):
        """Test missing JSON body returns 400."""
        with app.app_context():
            app.container.sse_connection_manager.override(mock_sse_connection_manager)

            response = client.post("/api/sse/callback")

            assert response.status_code == 400
            json_data = response.get_json()
            assert "error" in json_data
            assert "Missing JSON body" in json_data["error"]

    def test_unknown_action_returns_400(self, client, app, mock_sse_connection_manager):
        """Test unknown action returns 400."""
        payload = {
            "action": "unknown_action",
            "token": "test-token",
            "request": {
                "url": "/api/sse/stream?request_id=test123",
                "headers": {},
            },
        }

        with app.app_context():
            app.container.sse_connection_manager.override(mock_sse_connection_manager)

            response = client.post("/api/sse/callback", json=payload)

            assert response.status_code == 400
            json_data = response.get_json()
            assert "error" in json_data
            assert "Unknown action" in json_data["error"]

    def test_validation_error_returns_400(
        self, client, app, mock_sse_connection_manager
    ):
        """Test Pydantic validation error returns 400 with details."""
        payload = {
            "action": "connect",
            "request": {
                "url": "/api/sse/stream?request_id=test123",
                "headers": {},
            },
        }

        with app.app_context():
            app.container.sse_connection_manager.override(mock_sse_connection_manager)

            response = client.post("/api/sse/callback", json=payload)

            assert response.status_code == 400
            json_data = response.get_json()
            assert "error" in json_data
            assert "Invalid payload" in json_data["error"]
            assert "details" in json_data

    def test_request_id_with_colon_returns_400(
        self, client, app, mock_sse_connection_manager
    ):
        """Test request_id containing colon (reserved character) returns 400."""
        payload = {
            "action": "connect",
            "token": "test-token",
            "request": {
                "url": "/api/sse/stream?request_id=invalid:id",
                "headers": {},
            },
        }

        with app.app_context():
            app.container.sse_connection_manager.override(mock_sse_connection_manager)

            response = client.post("/api/sse/callback", json=payload)

            assert response.status_code == 400
            json_data = response.get_json()
            assert "error" in json_data
