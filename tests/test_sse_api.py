"""Tests for SSE Gateway callback API endpoint.

Every refusal answers with the error envelope; the gateway reads only the
status code (tests/integration/test_sse_gateway_rejection.py).
"""

from unittest.mock import Mock

import pytest
from dependency_injector import providers

from app.services.sse_connection_manager import SSEConnectionManager

_DISCONNECT = {
    "action": "disconnect",
    "token": "t",
    "reason": "client_closed",
    "request": {"url": "/api/sse/stream?request_id=abc123", "headers": {}},
}


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
            secret_key="test",
        )

        assert _authenticate_callback(None, settings) is False
        assert _authenticate_callback("wrong-secret", settings) is False
        assert _authenticate_callback("my-secret-key", settings) is True

        settings_no_secret = Settings(
            flask_env="production",
            sse_callback_secret="",
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
            assert json_data["code"] == "VALIDATION_FAILED"
            assert "has no request_id" in json_data["error"]
            mock_sse_connection_manager.on_connect.assert_not_called()

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
            assert response.get_json()["error"] == "Invalid JSON"

    def test_missing_json_body_returns_400(
        self, client, app, mock_sse_connection_manager
    ):
        """Test missing JSON body returns 400."""
        with app.app_context():
            app.container.sse_connection_manager.override(mock_sse_connection_manager)

            response = client.post("/api/sse/callback")

            assert response.status_code == 400
            json_data = response.get_json()
            assert json_data["code"] == "VALIDATION_FAILED"
            assert json_data["error"] == "SSE Gateway callback has no JSON body"

    def test_body_not_an_object_returns_400(self, client, app, mock_sse_connection_manager):
        """A JSON body that isn't an object is refused, not a 500."""
        with app.app_context():
            app.container.sse_connection_manager.override(mock_sse_connection_manager)

            response = client.post("/api/sse/callback", json=["connect"])

            assert response.status_code == 400
            json_data = response.get_json()
            assert json_data["code"] == "VALIDATION_FAILED"
            assert json_data["error"] == "SSE Gateway callback body is not a JSON object"

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
            assert json_data["code"] == "VALIDATION_FAILED"
            assert json_data["error"] == "Unknown SSE Gateway callback action: unknown_action"

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
            assert json_data["error"] == "Validation failed"
            assert {"message": "Field required", "field": "token"} in json_data["details"]["errors"]

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
            assert json_data["code"] == "VALIDATION_FAILED"
            assert json_data["error"] == "SSE connect request_id contains a colon: invalid:id"
            mock_sse_connection_manager.on_connect.assert_not_called()

    @pytest.mark.parametrize("secret", [None, "wrong-secret"])
    def test_production_refuses_a_wrong_secret_with_401(
        self, client, app, mock_sse_connection_manager, secret
    ):
        """In production the callback needs the shared secret: 401 and the envelope."""
        production = app.container.config().model_copy(
            update={"flask_env": "production", "sse_callback_secret": "my-secret-key"}
        )
        url = "/api/sse/callback" if secret is None else f"/api/sse/callback?secret={secret}"

        with app.app_context():
            app.container.config.override(providers.Object(production))
            app.container.sse_connection_manager.override(mock_sse_connection_manager)

            response = client.post(url, json=_DISCONNECT)

            assert response.status_code == 401
            json_data = response.get_json()
            assert json_data["code"] == "AUTHENTICATION_REQUIRED"
            assert json_data["error"] == "SSE Gateway callback secret is missing or wrong"
            mock_sse_connection_manager.on_disconnect.assert_not_called()

    def test_production_accepts_the_right_secret(
        self, client, app, mock_sse_connection_manager
    ):
        production = app.container.config().model_copy(
            update={"flask_env": "production", "sse_callback_secret": "my-secret-key"}
        )

        with app.app_context():
            app.container.config.override(providers.Object(production))
            app.container.sse_connection_manager.override(mock_sse_connection_manager)

            response = client.post("/api/sse/callback?secret=my-secret-key", json=_DISCONNECT)

            assert response.status_code == 200
            mock_sse_connection_manager.on_disconnect.assert_called_once_with("t")
