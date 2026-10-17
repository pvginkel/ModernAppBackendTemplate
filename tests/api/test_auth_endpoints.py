"""Tests for authentication endpoints."""

import time
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from flask import Flask

from app.exceptions import AuthenticationException
from app.services.oidc_client_service import AuthState, TokenResponse
from app.utils.auth import deserialize_auth_state, serialize_auth_state

AUTH_COOKIES = ("access_token", "refresh_token", "id_token")
LOGOUT_URL = "https://auth.example.com/realms/test/protocol/openid-connect/logout"


def _set_cookies(response: Any) -> dict[str, str]:
    """The response's Set-Cookie headers by cookie name."""
    by_name: dict[str, str] = {}
    for header in response.headers.getlist("Set-Cookie"):
        name = header.split("=", 1)[0]
        by_name[name] = header
    return by_name


def _assert_cleared(response: Any) -> None:
    cookies = _set_cookies(response)
    for name in AUTH_COOKIES:
        assert name in cookies, f"missing Set-Cookie for {name}"
        assert cookies[name].startswith(f"{name}=;"), cookies[name]
        assert "Max-Age=0" in cookies[name], cookies[name]


def _refresh_jwt(generate_test_jwt: Any) -> str:
    """A signed refresh token with an exp claim, as the after_request hook needs."""
    payload = {"sub": "test-user", "exp": int(time.time()) + 86400, "typ": "Refresh"}
    return jwt.encode(payload, generate_test_jwt.private_key, algorithm="RS256")


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


class TestCurrentUser:
    """/api/auth/self reads the identity the request hook resolved."""

    def test_valid_access_token(self, roles_oidc_client: Any, generate_test_jwt: Any):
        token = generate_test_jwt(
            subject="self-1", email="self1@example.com", name="Self One", roles=["editor"]
        )
        roles_oidc_client.set_cookie("access_token", token)

        response = roles_oidc_client.get("/api/auth/self")

        assert response.status_code == 200
        assert response.get_json() == {
            "subject": "self-1",
            "email": "self1@example.com",
            "name": "Self One",
            "roles": ["editor"],
        }

    def test_expired_access_with_valid_refresh_refreshes(
        self, roles_oidc_app: Flask, generate_test_jwt: Any
    ):
        client = roles_oidc_app.test_client()
        client.set_cookie("access_token", generate_test_jwt(expired=True, roles=["editor"]))
        client.set_cookie("refresh_token", "old-refresh")
        fresh = TokenResponse(
            access_token=generate_test_jwt(subject="self-2", roles=["editor"]),
            id_token=None,
            refresh_token=_refresh_jwt(generate_test_jwt),
            token_type="Bearer",
            expires_in=300,
        )

        with patch.object(
            roles_oidc_app.container.oidc_client_service(),
            "refresh_access_token",
            return_value=fresh,
        ) as refresh:
            response = client.get("/api/auth/self")

        assert response.status_code == 200
        assert response.get_json()["subject"] == "self-2"
        refresh.assert_called_once_with("old-refresh")
        cookies = _set_cookies(response)
        assert cookies["access_token"].startswith(f"access_token={fresh.access_token};")
        assert cookies["refresh_token"].startswith(f"refresh_token={fresh.refresh_token};")
        assert "Max-Age=0" not in cookies["access_token"]

    def test_expired_access_with_failed_refresh_clears_cookies(
        self, roles_oidc_app: Flask, generate_test_jwt: Any
    ):
        client = roles_oidc_app.test_client()
        client.set_cookie("access_token", generate_test_jwt(expired=True, roles=["editor"]))
        client.set_cookie("refresh_token", "expired-refresh")

        with patch.object(
            roles_oidc_app.container.oidc_client_service(),
            "refresh_access_token",
            side_effect=AuthenticationException("Refresh failed"),
        ):
            response = client.get("/api/auth/self")

        assert response.status_code == 401
        _assert_cleared(response)

    def test_no_cookies_returns_401_without_clearing(self, roles_oidc_client: Any):
        response = roles_oidc_client.get("/api/auth/self")

        assert response.status_code == 401
        assert not set(_set_cookies(response)) & set(AUTH_COOKIES)

    def test_no_recognized_role_returns_403(
        self, roles_oidc_client: Any, generate_test_jwt: Any
    ):
        token = generate_test_jwt(roles=["default-roles-homelab"])
        roles_oidc_client.set_cookie("access_token", token)

        response = roles_oidc_client.get("/api/auth/self")

        assert response.status_code == 403
        assert "no recognized role" in response.get_json()["error"].lower()

    def test_additional_role_only_is_signed_in(
        self, roles_oidc_client: Any, generate_test_jwt: Any
    ):
        token = generate_test_jwt(subject="ci", roles=["pipeline"])
        roles_oidc_client.set_cookie("access_token", token)

        response = roles_oidc_client.get("/api/auth/self")

        assert response.status_code == 200
        assert response.get_json()["roles"] == ["pipeline"]

    def test_forced_auth_error_short_circuits(
        self, roles_oidc_app: Flask, generate_test_jwt: Any
    ):
        client = roles_oidc_app.test_client()
        client.set_cookie("access_token", generate_test_jwt(roles=["editor"]))
        roles_oidc_app.container.testing_service().set_forced_auth_error(503)

        response = client.get("/api/auth/self")

        assert response.status_code == 503
        data = response.get_json()
        assert data["error"] == "Simulated error for testing (status 503)"
        assert data["message"] == "Simulated error for testing"

        # Single-shot
        assert client.get("/api/auth/self").status_code == 200

    def test_test_session_with_oidc_disabled(self, app: Flask, client: Any):
        token = app.container.testing_service().create_session(
            subject="session-user", name="Session User", email="s@example.com", roles=["admin"]
        )
        client.set_cookie("access_token", token)
        try:
            response = client.get("/api/auth/self")
        finally:
            app.container.testing_service().clear_session(token)

        assert response.status_code == 200
        assert response.get_json()["subject"] == "session-user"


class TestAuthFlowWithoutRole:
    """Login, callback and logout stay reachable for a caller with no role."""

    def test_login(self, roles_oidc_client: Any, generate_test_jwt: Any):
        token = generate_test_jwt(roles=["default-roles-homelab"])
        roles_oidc_client.set_cookie("access_token", token)

        response = roles_oidc_client.get("/api/auth/login?redirect=/")

        assert response.status_code == 302
        assert response.headers["Location"].startswith(
            "https://auth.example.com/realms/test/protocol/openid-connect/auth"
        )

    def test_callback(self, roles_oidc_app: Flask, generate_test_jwt: Any):
        client = roles_oidc_app.test_client()
        client.set_cookie("access_token", generate_test_jwt(roles=["default-roles-homelab"]))
        state = serialize_auth_state(
            AuthState(code_verifier="verifier", redirect_url="/dashboard", nonce="nonce"),
            roles_oidc_app.container.config().secret_key,
        )
        new_access = generate_test_jwt(subject="new-login", roles=["default-roles-homelab"])
        tokens = TokenResponse(
            access_token=new_access,
            id_token="new-id-token",
            refresh_token=None,
            token_type="Bearer",
            expires_in=300,
        )

        with patch.object(
            roles_oidc_app.container.oidc_client_service(),
            "exchange_code_for_tokens",
            return_value=tokens,
        ):
            response = client.get(
                "/api/auth/callback", query_string={"code": "the-code", "state": state}
            )

        assert response.status_code == 302
        assert response.headers["Location"] == "/dashboard"
        cookies = _set_cookies(response)
        assert cookies["access_token"].startswith(f"access_token={new_access};")
        assert cookies["id_token"].startswith("id_token=new-id-token;")

    def test_logout(self, roles_oidc_client: Any, generate_test_jwt: Any):
        token = generate_test_jwt(roles=["default-roles-homelab"])
        roles_oidc_client.set_cookie("access_token", token)

        response = roles_oidc_client.get("/api/auth/logout")

        assert response.status_code == 302
        assert response.headers["Location"].startswith(LOGOUT_URL)
        _assert_cleared(response)

    def test_logout_after_refresh_still_clears_cookies(
        self, roles_oidc_app: Flask, generate_test_jwt: Any
    ):
        """A refresh made on the way in must not put fresh cookies back."""
        client = roles_oidc_app.test_client()
        client.set_cookie("access_token", generate_test_jwt(expired=True, roles=["editor"]))
        client.set_cookie("refresh_token", "old-refresh")
        fresh = TokenResponse(
            access_token=generate_test_jwt(roles=["editor"]),
            id_token=None,
            refresh_token=_refresh_jwt(generate_test_jwt),
            token_type="Bearer",
            expires_in=300,
        )

        with patch.object(
            roles_oidc_app.container.oidc_client_service(),
            "refresh_access_token",
            return_value=fresh,
        ) as refresh:
            response = client.get("/api/auth/logout")

        refresh.assert_called_once()
        assert response.status_code == 302
        _assert_cleared(response)
        set_cookie = " ".join(response.headers.getlist("Set-Cookie"))
        assert fresh.access_token not in set_cookie
        assert str(fresh.refresh_token) not in set_cookie


def _redirect_params(response: Any) -> dict[str, list[str]]:
    assert response.status_code == 302
    return parse_qs(urlparse(response.headers["Location"]).query)


def _callback(app: Flask, generate_test_jwt: Any, auth_state: AuthState) -> Any:
    """Run a callback for auth_state; return the mocked token exchange."""
    state = serialize_auth_state(auth_state, app.container.config().secret_key)
    tokens = TokenResponse(
        access_token=generate_test_jwt(),
        id_token=None,
        refresh_token=None,
        token_type="Bearer",
        expires_in=300,
    )

    with patch.object(
        app.container.oidc_client_service(),
        "exchange_code_for_tokens",
        return_value=tokens,
    ) as exchange:
        response = app.test_client().get(
            "/api/auth/callback", query_string={"code": "the-code", "state": state}
        )

    assert response.status_code == 302
    assert response.headers["Location"] == auth_state.redirect_url
    return exchange


class TestRedirectOrigin:
    """The OIDC redirect URIs come from the request's origin, not BASEURL.

    The oidc_app's BASEURL is http://localhost:3000; the test client's
    origin is http://localhost.
    """

    def test_login_redirect_uri_from_request_host(self, oidc_client: Any):
        params = _redirect_params(oidc_client.get("/api/auth/login?redirect=/dashboard"))

        assert params["redirect_uri"] == ["http://localhost/api/auth/callback"]

    def test_login_keeps_a_non_default_port(self, oidc_client: Any):
        response = oidc_client.get(
            "/api/auth/login?redirect=/", base_url="http://app.example.com:8443"
        )

        params = _redirect_params(response)
        assert params["redirect_uri"] == ["http://app.example.com:8443/api/auth/callback"]

    def test_login_behind_a_proxy_uses_forwarded_headers(self, oidc_client: Any):
        """ProxyFix trusts one hop's X-Forwarded-Proto and X-Forwarded-Host."""
        response = oidc_client.get(
            "/api/auth/login?redirect=/",
            headers={
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "app.example.com",
            },
        )

        params = _redirect_params(response)
        assert params["redirect_uri"] == ["https://app.example.com/api/auth/callback"]

    def test_login_stores_origin_in_state(self, oidc_app: Flask, oidc_client: Any):
        response = oidc_client.get(
            "/api/auth/login?redirect=/items?filter=1",
            headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "app.example.com"},
        )

        state = _redirect_params(response)["state"][0]
        auth_state = deserialize_auth_state(state, oidc_app.container.config().secret_key)
        assert auth_state.origin == "https://app.example.com"
        assert auth_state.redirect_url == "/items?filter=1"

    @pytest.mark.parametrize(
        "redirect_url",
        ["http://localhost/dashboard", "//evil.com", "/\\evil.com", "dashboard"],
    )
    def test_login_rejects_anything_but_a_path(self, oidc_client: Any, redirect_url: str):
        response = oidc_client.get("/api/auth/login", query_string={"redirect": redirect_url})

        assert response.status_code == 400

    def test_callback_uses_origin_from_state(self, oidc_app: Flask, generate_test_jwt: Any):
        auth_state = AuthState(
            code_verifier="verifier",
            redirect_url="/dashboard",
            nonce="nonce",
            origin="https://other.example.com",
        )

        exchange = _callback(oidc_app, generate_test_jwt, auth_state)

        exchange.assert_called_once_with("the-code", "verifier", "https://other.example.com")

    def test_callback_without_origin_in_state_uses_baseurl(
        self, oidc_app: Flask, generate_test_jwt: Any
    ):
        """A state made before origins were stored was built on BASEURL."""
        auth_state = AuthState(code_verifier="verifier", redirect_url="/", nonce="nonce")

        exchange = _callback(oidc_app, generate_test_jwt, auth_state)

        exchange.assert_called_once_with("the-code", "verifier", "http://localhost:3000")

    def test_logout_post_logout_redirect_uri_from_request_origin(self, oidc_client: Any):
        response = oidc_client.get(
            "/api/auth/logout?redirect=/goodbye",
            headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "app.example.com"},
        )

        params = _redirect_params(response)
        assert params["post_logout_redirect_uri"] == ["https://app.example.com/goodbye"]

    def test_logout_default_redirect_is_root(self, oidc_client: Any):
        params = _redirect_params(oidc_client.get("/api/auth/logout"))

        assert params["post_logout_redirect_uri"] == ["http://localhost/"]

    @pytest.mark.parametrize("redirect_url", ["https://evil.com/steal", "//evil.com"])
    def test_logout_rejects_anything_but_a_path(self, oidc_client: Any, redirect_url: str):
        response = oidc_client.get("/api/auth/logout", query_string={"redirect": redirect_url})

        assert response.status_code == 400

    def test_logout_with_oidc_disabled_redirects_to_the_path(self, client: Any):
        response = client.get("/api/auth/logout?redirect=/goodbye")

        assert response.status_code == 302
        assert response.headers["Location"] == "/goodbye"
