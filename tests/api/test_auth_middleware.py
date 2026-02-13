"""Tests for authentication middleware and authorization logic."""

from typing import Any
from unittest.mock import MagicMock, patch

import jwt
from flask import Flask


class TestAuthenticationMiddleware:
    """Test suite for authentication middleware behavior (before_request hook)."""

    def test_bearer_token_authentication(
        self, oidc_app: Flask, generate_test_jwt: Any
    ):
        """Test authentication works with Bearer token in Authorization header."""
        client = oidc_app.test_client()

        token = generate_test_jwt(subject="admin-user", roles=["admin"])

        response = client.get(
            "/api/items", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200

    def test_cookie_token_authentication(
        self, oidc_app: Flask, generate_test_jwt: Any
    ):
        """Test authentication works with token in cookie."""
        client = oidc_app.test_client()

        token = generate_test_jwt(subject="cookie-user", roles=["admin"])
        client.set_cookie("access_token", token)

        response = client.get("/api/items")

        assert response.status_code == 200

    def test_cookie_takes_precedence_over_bearer(
        self, oidc_app: Flask, generate_test_jwt: Any
    ):
        """Test that cookie token is checked before Authorization header."""
        client = oidc_app.test_client()

        cookie_token = generate_test_jwt(subject="cookie-user")
        bearer_token = generate_test_jwt(subject="bearer-user")

        client.set_cookie("access_token", cookie_token)

        response = client.get(
            "/api/items", headers={"Authorization": f"Bearer {bearer_token}"}
        )

        assert response.status_code == 200

    def test_any_authenticated_user_has_access(
        self, oidc_app: Flask, generate_test_jwt: Any
    ):
        """Test that any authenticated user can access endpoints (default: no role check)."""
        client = oidc_app.test_client()

        token = generate_test_jwt(roles=["viewer"])
        client.set_cookie("access_token", token)

        response = client.get("/api/items")
        assert response.status_code == 200

    def test_no_token_returns_401(self, oidc_app: Flask):
        """Test request without token returns 401 Unauthorized."""
        client = oidc_app.test_client()

        response = client.get("/api/items")

        assert response.status_code == 401
        data = response.get_json()
        assert "token" in data["error"].lower()

    def test_expired_token_returns_401(
        self, oidc_app: Flask, generate_test_jwt: Any
    ):
        """Test request with expired token returns 401."""
        client = oidc_app.test_client()

        token = generate_test_jwt(expired=True, roles=["admin"])
        client.set_cookie("access_token", token)

        response = client.get("/api/items")

        assert response.status_code == 401
        data = response.get_json()
        assert "expired" in data["error"].lower()

    def test_invalid_signature_returns_401(
        self, oidc_app: Flask, generate_test_jwt: Any
    ):
        """Test request with invalid signature returns 401."""
        client = oidc_app.test_client()

        token = generate_test_jwt(invalid_signature=True, roles=["admin"])
        client.set_cookie("access_token", token)

        response = client.get("/api/items")

        assert response.status_code == 401
        data = response.get_json()
        assert "signature" in data["error"].lower()

    def test_internal_endpoints_bypass_authentication(self, oidc_app: Flask):
        """Test that internal endpoints (outside api_bp) bypass authentication."""
        client = oidc_app.test_client()

        response = client.get("/health/healthz")
        assert response.status_code not in (401, 403)
        assert response.status_code == 200

        response = client.get("/metrics")
        assert response.status_code not in (401, 403)

    def test_public_login_endpoint_accessible_without_token(self, oidc_app: Flask):
        """Test that /api/auth/login is accessible without token (it's @public)."""
        client = oidc_app.test_client()

        response = client.get("/api/auth/login")
        assert response.status_code == 400

    def test_oidc_disabled_bypasses_authentication(self, client: Any):
        """Test that OIDC_ENABLED=False bypasses all authentication."""
        response = client.get("/api/items")
        assert response.status_code == 200


class TestTokenRefreshMiddleware:
    """Test suite for token refresh functionality in authentication middleware."""

    def test_expired_access_token_with_valid_refresh_token_succeeds(
        self, oidc_app: Flask, generate_test_jwt: Any
    ):
        """Test that expired access token with valid refresh token refreshes and succeeds."""
        import time

        client = oidc_app.test_client()

        expired_token = generate_test_jwt(expired=True, roles=["admin"])

        refresh_exp = int(time.time()) + 86400
        refresh_payload = {"sub": "test-user", "exp": refresh_exp, "typ": "Refresh"}
        refresh_token = jwt.encode(
            refresh_payload, generate_test_jwt.private_key, algorithm="RS256"
        )

        new_access_token = generate_test_jwt(subject="test-user", roles=["admin"])

        with patch("httpx.post") as mock_post:
            mock_refresh_response = MagicMock()
            mock_refresh_response.json.return_value = {
                "access_token": new_access_token,
                "refresh_token": refresh_token,
                "token_type": "Bearer",
                "expires_in": 300,
            }
            mock_refresh_response.raise_for_status = MagicMock()
            mock_post.return_value = mock_refresh_response

            client.set_cookie("access_token", expired_token)
            client.set_cookie("refresh_token", refresh_token)

            response = client.get("/api/items")

            assert response.status_code == 200
            mock_post.assert_called_once()

    def test_expired_access_token_without_refresh_token_returns_401(
        self, oidc_app: Flask, generate_test_jwt: Any
    ):
        """Test that expired access token without refresh token returns 401."""
        client = oidc_app.test_client()

        expired_token = generate_test_jwt(expired=True, roles=["admin"])
        client.set_cookie("access_token", expired_token)

        response = client.get("/api/items")

        assert response.status_code == 401

    def test_expired_access_token_with_failed_refresh_returns_401(
        self, oidc_app: Flask, generate_test_jwt: Any
    ):
        """Test that expired access token with failed refresh returns 401 and clears cookies."""
        import time

        import httpx

        client = oidc_app.test_client()

        expired_access = generate_test_jwt(expired=True, roles=["admin"])

        refresh_exp = int(time.time()) + 86400
        refresh_payload = {"sub": "test-user", "exp": refresh_exp, "typ": "Refresh"}
        refresh_token = jwt.encode(
            refresh_payload, generate_test_jwt.private_key, algorithm="RS256"
        )

        with patch("httpx.post") as mock_post:
            mock_post.side_effect = httpx.HTTPStatusError(
                "401 Unauthorized",
                request=MagicMock(),
                response=MagicMock(status_code=401),
            )

            client.set_cookie("access_token", expired_access)
            client.set_cookie("refresh_token", refresh_token)

            response = client.get("/api/items")

            assert response.status_code == 401

            set_cookie_headers = response.headers.getlist("Set-Cookie")
            cookie_str = " ".join(set_cookie_headers)

            assert "access_token=" in cookie_str
            assert "Max-Age=0" in cookie_str

    def test_valid_access_token_does_not_trigger_refresh(
        self, oidc_app: Flask, generate_test_jwt: Any
    ):
        """Test that valid access token does not trigger refresh."""
        import time

        client = oidc_app.test_client()

        valid_token = generate_test_jwt(roles=["admin"])

        refresh_exp = int(time.time()) + 86400
        refresh_payload = {"sub": "test-user", "exp": refresh_exp, "typ": "Refresh"}
        refresh_token = jwt.encode(
            refresh_payload, generate_test_jwt.private_key, algorithm="RS256"
        )

        with patch("httpx.post") as mock_post:
            client.set_cookie("access_token", valid_token)
            client.set_cookie("refresh_token", refresh_token)

            response = client.get("/api/items")

            assert response.status_code == 200
            mock_post.assert_not_called()
