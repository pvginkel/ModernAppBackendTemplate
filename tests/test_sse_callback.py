"""Tests for SSE Gateway callback API endpoint."""

from typing import Any
from unittest.mock import MagicMock


class TestSSECallbackAPI:
    """Test SSE Gateway callback endpoint."""

    def test_connect_callback_returns_200(self, client: Any) -> None:
        """Test connect callback returns 200 OK."""
        payload = {
            "action": "connect",
            "token": "test-token-123",
            "request": {
                "url": "/api/sse/stream?request_id=abc123",
                "method": "GET"
            }
        }

        response = client.post("/api/sse/callback", json=payload)

        assert response.status_code == 200

    def test_connect_callback_returns_empty_json(self, client: Any) -> None:
        """Test connect callback returns empty JSON response."""
        payload = {
            "action": "connect",
            "token": "test-token",
            "request": {
                "url": "/api/sse/stream?request_id=test123",
                "method": "GET"
            }
        }

        response = client.post("/api/sse/callback", json=payload)

        assert response.status_code == 200
        json_data = response.get_json()
        # SSE Gateway only checks status code, response body is empty
        assert json_data == {}

    def test_disconnect_callback_returns_200(self, client: Any) -> None:
        """Test disconnect callback returns 200 OK."""
        payload = {
            "action": "disconnect",
            "token": "test-token-123",
            "reason": "client_closed",
            "request": {
                "url": "/api/sse/stream?request_id=abc123",
            },
        }

        response = client.post("/api/sse/callback", json=payload)

        assert response.status_code == 200

    def test_missing_request_id_returns_400(self, client: Any) -> None:
        """Test missing request_id query parameter returns 400."""
        payload = {
            "action": "connect",
            "token": "test-token",
            "request": {
                "url": "/api/sse/stream",  # Missing request_id
                "method": "GET"
            }
        }

        response = client.post("/api/sse/callback", json=payload)

        assert response.status_code == 400
        json_data = response.get_json()
        assert "error" in json_data

    def test_missing_json_body_returns_400(self, client: Any) -> None:
        """Test missing JSON body returns 400."""
        response = client.post("/api/sse/callback")

        assert response.status_code == 400
        json_data = response.get_json()
        assert "error" in json_data

    def test_unknown_action_returns_400(self, client: Any) -> None:
        """Test unknown action returns 400."""
        payload = {
            "action": "unknown_action",
            "token": "test-token",
            "request": {
                "url": "/api/sse/stream?request_id=test123",
                "method": "GET"
            }
        }

        response = client.post("/api/sse/callback", json=payload)

        assert response.status_code == 400
        json_data = response.get_json()
        assert "error" in json_data

    def test_validation_error_returns_400(self, client: Any) -> None:
        """Test Pydantic validation error returns 400 with details."""
        payload = {
            "action": "connect",
            # Missing required 'token' field
            "request": {
                "url": "/api/sse/stream?request_id=test123",
                "method": "GET"
            }
        }

        response = client.post("/api/sse/callback", json=payload)

        assert response.status_code == 400
        json_data = response.get_json()
        assert "error" in json_data


class TestSSECallbackAuthentication:
    """Test SSE callback authentication."""

    def test_authentication_skipped_in_dev_mode(self, client: Any) -> None:
        """Test secret authentication skipped in development mode."""
        payload = {
            "action": "connect",
            "token": "test-token",
            "request": {
                "url": "/api/sse/stream?request_id=test123",
                "method": "GET"
            }
        }

        # No secret parameter - should still succeed in dev mode
        response = client.post("/api/sse/callback", json=payload)
        assert response.status_code == 200

    def test_authentication_rejects_in_production_without_secret(
        self, app: Any, client: Any
    ) -> None:
        """Test authentication rejects in production mode without secret."""
        # Temporarily set production mode
        original_env = app.config.get("FLASK_ENV")
        app.config["FLASK_ENV"] = "production"
        app.config["SSE_CALLBACK_SECRET"] = "my-secret-key"

        try:
            payload = {
                "action": "connect",
                "token": "test-token",
                "request": {
                    "url": "/api/sse/stream?request_id=test123",
                    "method": "GET"
                }
            }

            # Without secret query param - should fail in production
            response = client.post("/api/sse/callback", json=payload)
            assert response.status_code == 401
        finally:
            app.config["FLASK_ENV"] = original_env

    def test_authentication_accepts_with_correct_secret(
        self, app: Any, client: Any
    ) -> None:
        """Test authentication accepts with correct secret in production."""
        # Temporarily set production mode
        original_env = app.config.get("FLASK_ENV")
        app.config["FLASK_ENV"] = "production"
        app.config["SSE_CALLBACK_SECRET"] = "my-secret-key"

        try:
            payload = {
                "action": "connect",
                "token": "test-token",
                "request": {
                    "url": "/api/sse/stream?request_id=test123",
                    "method": "GET"
                }
            }

            # With correct secret - should succeed
            response = client.post(
                "/api/sse/callback?secret=my-secret-key", json=payload
            )
            assert response.status_code == 200
        finally:
            app.config["FLASK_ENV"] = original_env
