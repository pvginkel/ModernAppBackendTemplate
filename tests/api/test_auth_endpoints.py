"""Tests for authentication endpoints."""

from typing import Any


class TestAuthEndpoints:
    """Test suite for authentication endpoints (/api/auth/*)."""

    def test_get_current_user_with_oidc_disabled(self, client: Any):
        """Test /api/auth/self returns default local user when OIDC disabled."""
        response = client.get("/api/auth/self")

        assert response.status_code == 200
        data = response.get_json()
        assert data["subject"] == "local-user"
        assert data["email"] == "admin@local"
        assert data["name"] == "Local Admin"
        assert "admin" in data["roles"]

    def test_get_current_user_unauthenticated(self, oidc_client: Any):
        """Test /api/auth/self returns 401 when not authenticated with OIDC enabled."""
        response = oidc_client.get("/api/auth/self")

        assert response.status_code == 401

    def test_login_without_redirect_parameter(self, oidc_client: Any):
        """Test /api/auth/login returns 400 without redirect parameter."""
        response = oidc_client.get("/api/auth/login")

        assert response.status_code == 400
        data = response.get_json()
        assert "redirect" in data["error"].lower()

    def test_login_with_external_redirect_blocked(self, oidc_client: Any):
        """Test /api/auth/login blocks external redirect URLs."""
        response = oidc_client.get("/api/auth/login?redirect=https://evil.com")

        assert response.status_code == 400

    def test_login_with_valid_redirect_redirects(self, oidc_client: Any):
        """Test /api/auth/login redirects to OIDC provider with valid redirect."""
        response = oidc_client.get("/api/auth/login?redirect=/dashboard")

        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "auth.example.com" in location
        assert "openid-connect/auth" in location

    def test_login_when_oidc_disabled_returns_400(self, client: Any):
        """Test /api/auth/login returns 400 when OIDC is disabled."""
        response = client.get("/api/auth/login?redirect=/dashboard")

        assert response.status_code == 400
        data = response.get_json()
        assert "not enabled" in data["error"].lower()

    def test_logout_clears_cookies(self, client: Any):
        """Test /api/auth/logout clears all auth cookies."""
        response = client.get("/api/auth/logout")

        assert response.status_code == 302
        set_cookie_headers = response.headers.getlist("Set-Cookie")
        cookie_str = " ".join(set_cookie_headers)

        assert "access_token=" in cookie_str
        assert "refresh_token=" in cookie_str
        assert "Max-Age=0" in cookie_str

    def test_logout_clears_id_token_cookie(self, client: Any):
        """Test /api/auth/logout clears the id_token cookie."""
        client.set_cookie("id_token", "some-id-token")

        response = client.get("/api/auth/logout")

        assert response.status_code == 302
        set_cookie_headers = response.headers.getlist("Set-Cookie")
        id_token_cleared = any(
            "id_token=" in header and "Max-Age=0" in header
            for header in set_cookie_headers
        )
        assert id_token_cleared, "id_token cookie should be cleared on logout"

    def test_logout_default_redirect(self, client: Any):
        """Test /api/auth/logout redirects to / by default."""
        response = client.get("/api/auth/logout")

        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert location.endswith("/")

    def test_callback_without_code_returns_400(self, oidc_client: Any):
        """Test /api/auth/callback returns 400 when code parameter is missing."""
        response = oidc_client.get("/api/auth/callback?state=some-state")

        assert response.status_code == 400
        data = response.get_json()
        assert "code" in data["error"].lower()

    def test_callback_without_state_returns_400(self, oidc_client: Any):
        """Test /api/auth/callback returns 400 when state parameter is missing."""
        response = oidc_client.get("/api/auth/callback?code=some-code")

        assert response.status_code == 400
        data = response.get_json()
        assert "state" in data["error"].lower()
