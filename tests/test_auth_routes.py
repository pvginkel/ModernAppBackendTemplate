"""Tests for OIDC authentication HTTP routes."""

from http import HTTPStatus
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask
from flask.testing import FlaskClient


class TestAuthRoutesOIDCDisabled:
    """Tests for auth routes when OIDC is disabled."""

    def test_auth_check_returns_authenticated_and_disabled(self, client: FlaskClient) -> None:
        """When OIDC is disabled, auth check should return authenticated=True, disabled=True."""
        response = client.get("/health/auth/check")
        assert response.status_code == HTTPStatus.OK
        body = response.get_json()
        assert body["authenticated"] is True
        assert body["disabled"] is True

    def test_login_redirects_to_home(self, client: FlaskClient) -> None:
        """When OIDC is disabled, login should redirect to home."""
        response = client.get("/health/auth/login")
        assert response.status_code == HTTPStatus.FOUND
        assert response.headers.get("Location") == "/"

    def test_self_returns_local_user(self, client: FlaskClient) -> None:
        """When OIDC is disabled, /auth/self should return local user."""
        response = client.get("/health/auth/self")
        assert response.status_code == HTTPStatus.OK
        body = response.get_json()
        assert body["subject"] == "local-user"
        assert body["authenticated"] is True

    def test_logout_redirects_to_home(self, client: FlaskClient) -> None:
        """When OIDC is disabled, logout should redirect to specified URL."""
        response = client.get("/health/auth/logout?redirect=/")
        assert response.status_code == HTTPStatus.FOUND


class TestAuthRoutesOIDCEnabled:
    """Tests for auth routes when OIDC is enabled."""

    @pytest.fixture
    def mock_oidc_discovery(self) -> Any:
        """Mock OIDC discovery to avoid network calls."""
        mock_config = {
            "authorization_endpoint": "https://keycloak.example.com/auth",
            "token_endpoint": "https://keycloak.example.com/token",
            "end_session_endpoint": "https://keycloak.example.com/logout",
            "jwks_uri": "https://keycloak.example.com/certs",
            "id_token_signing_alg_values_supported": ["RS256"],
        }

        mock_response = MagicMock()
        mock_response.json.return_value = mock_config
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.get") as mock_get, \
             patch("httpx.Client") as mock_client_class:
            mock_get.return_value = mock_response

            mock_client_instance = MagicMock()
            mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
            mock_client_instance.__exit__ = MagicMock(return_value=False)
            mock_client_instance.get.return_value = mock_response
            mock_client_class.return_value = mock_client_instance

            yield

    @pytest.fixture
    def oidc_enabled_app(
        self, test_settings: Any, mock_oidc_discovery: Any
    ) -> Flask:
        """Create app with OIDC enabled."""
        from app.container import AppContainer
        from common.core.app import create_app

        # Enable OIDC in settings
        test_settings.OIDC_ENABLED = True
        test_settings.OIDC_ISSUER_URL = "https://keycloak.example.com/realms/test"
        test_settings.OIDC_CLIENT_ID = "test-client"
        test_settings.OIDC_CLIENT_SECRET = "test-secret"
        test_settings.BASEURL = "http://localhost:5000"

        flask_app = create_app(
            AppContainer,
            settings=test_settings,
            skip_background_services=True,
        )
        flask_app.config["TESTING"] = True
        return flask_app

    @pytest.fixture
    def oidc_client(self, oidc_enabled_app: Flask) -> FlaskClient:
        """Test client for OIDC-enabled app."""
        return oidc_enabled_app.test_client()

    def test_auth_check_returns_not_authenticated(self, oidc_client: FlaskClient) -> None:
        """When OIDC is enabled, unauthenticated user should get authenticated=False."""
        response = oidc_client.get("/health/auth/check")
        assert response.status_code == HTTPStatus.OK
        body = response.get_json()
        assert body["authenticated"] is False
        assert body["disabled"] is False

    def test_login_redirects_to_oidc_provider(self, oidc_client: FlaskClient) -> None:
        """Login should redirect to OIDC provider."""
        response = oidc_client.get("/health/auth/login")
        assert response.status_code == HTTPStatus.FOUND
        location = response.headers.get("Location", "")
        assert "keycloak.example.com" in location

    def test_self_returns_401_without_token(self, oidc_client: FlaskClient) -> None:
        """When not authenticated, /auth/self should return 401."""
        response = oidc_client.get("/health/auth/self")
        assert response.status_code == HTTPStatus.UNAUTHORIZED
        body = response.get_json()
        assert body["authenticated"] is False

    def test_logout_clears_cookies(self, oidc_client: FlaskClient) -> None:
        """Logout should clear auth cookies."""
        response = oidc_client.get("/health/auth/logout")
        assert response.status_code == HTTPStatus.FOUND
        # Check that cookies are being cleared (Max-Age=0)
        cookies = response.headers.getlist("Set-Cookie")
        cookie_str = " ".join(cookies)
        assert "Max-Age=0" in cookie_str

    def test_callback_rejects_missing_code(self, oidc_client: FlaskClient) -> None:
        """Callback should reject requests missing authorization code."""
        response = oidc_client.get("/health/auth/callback?state=test")
        assert response.status_code == HTTPStatus.BAD_REQUEST

    def test_callback_rejects_missing_state(self, oidc_client: FlaskClient) -> None:
        """Callback should reject requests missing state parameter."""
        response = oidc_client.get("/health/auth/callback?code=test")
        assert response.status_code == HTTPStatus.BAD_REQUEST

    def test_callback_rejects_missing_auth_state_cookie(self, oidc_client: FlaskClient) -> None:
        """Callback should reject requests without auth_state cookie."""
        response = oidc_client.get("/health/auth/callback?code=test&state=test")
        assert response.status_code == HTTPStatus.BAD_REQUEST
        assert b"Missing auth state cookie" in response.data
