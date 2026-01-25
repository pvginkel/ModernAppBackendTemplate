"""Tests for OIDC authentication."""

import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


class TestOIDCUser:
    """Tests for OIDCUser dataclass."""

    def test_from_claims_basic(self) -> None:
        """Test creating OIDCUser from basic claims."""
        from common.auth.oidc import OIDCUser

        claims = {
            "sub": "user-123",
            "email": "user@example.com",
            "name": "Test User",
        }

        user = OIDCUser.from_claims(claims)

        assert user.sub == "user-123"
        assert user.email == "user@example.com"
        assert user.name == "Test User"
        assert user.raw_claims == claims

    def test_from_claims_with_roles(self) -> None:
        """Test creating OIDCUser with roles from claims."""
        from common.auth.oidc import OIDCUser

        claims = {
            "sub": "user-123",
            "roles": ["admin", "user"],
        }

        user = OIDCUser.from_claims(claims)

        assert user.roles == ["admin", "user"]

    def test_from_claims_with_keycloak_realm_access(self) -> None:
        """Test creating OIDCUser with Keycloak realm_access roles."""
        from common.auth.oidc import OIDCUser

        claims = {
            "sub": "user-123",
            "realm_access": {
                "roles": ["admin", "manager"]
            },
        }

        user = OIDCUser.from_claims(claims)

        assert user.roles == ["admin", "manager"]

    def test_from_claims_with_groups(self) -> None:
        """Test creating OIDCUser with groups."""
        from common.auth.oidc import OIDCUser

        claims = {
            "sub": "user-123",
            "groups": ["engineering", "devops"],
        }

        user = OIDCUser.from_claims(claims)

        assert user.groups == ["engineering", "devops"]


class TestOIDCAuthenticator:
    """Tests for OIDCAuthenticator."""

    def test_disabled_when_oidc_not_enabled(self, test_settings: Any) -> None:
        """Test authenticator reports disabled when OIDC_ENABLED is False."""
        from common.auth.oidc import OIDCAuthenticator

        test_settings.OIDC_ENABLED = False

        authenticator = OIDCAuthenticator(test_settings)

        assert authenticator.enabled is False

    def test_raises_when_enabled_without_issuer(self, test_settings: Any) -> None:
        """Test authenticator raises when enabled without issuer URL."""
        from common.auth.oidc import OIDCAuthenticator, OIDCConfigurationError

        test_settings.OIDC_ENABLED = True
        test_settings.OIDC_ISSUER_URL = None

        with pytest.raises(OIDCConfigurationError):
            OIDCAuthenticator(test_settings)

    def test_raises_when_enabled_without_client_id(self, test_settings: Any) -> None:
        """Test authenticator raises when enabled without client ID."""
        from common.auth.oidc import OIDCAuthenticator, OIDCConfigurationError

        test_settings.OIDC_ENABLED = True
        test_settings.OIDC_ISSUER_URL = "https://auth.example.com"
        test_settings.OIDC_CLIENT_ID = None

        with pytest.raises(OIDCConfigurationError):
            OIDCAuthenticator(test_settings)


class TestOIDCDecorators:
    """Tests for OIDC authentication decorators."""

    def test_require_auth_allows_when_disabled(
        self, app: Any, client: Any, test_settings: Any
    ) -> None:
        """Test require_auth allows access when OIDC is disabled."""
        from flask import jsonify, g
        from common.auth.oidc import OIDCAuthenticator

        test_settings.OIDC_ENABLED = False
        authenticator = OIDCAuthenticator(test_settings)

        @app.route("/test-auth-disabled")
        @authenticator.require_auth
        def protected_route() -> Any:
            return jsonify({"user": g.oidc_user})

        response = client.get("/test-auth-disabled")

        assert response.status_code == 200
        data = response.get_json()
        assert data["user"] is None

    def test_require_auth_rejects_without_token(
        self, app: Any, client: Any, test_settings: Any
    ) -> None:
        """Test require_auth rejects requests without token when enabled."""
        from flask import jsonify
        from common.auth.oidc import OIDCAuthenticator

        test_settings.OIDC_ENABLED = True

        with patch.object(
            OIDCAuthenticator, "__init__", lambda self, settings: None
        ):
            authenticator = OIDCAuthenticator.__new__(OIDCAuthenticator)
            authenticator._settings = test_settings
            authenticator._provider = MagicMock()

            @app.route("/test-auth-required")
            @authenticator.require_auth
            def protected_route() -> Any:
                return jsonify({"status": "ok"})

            # Mock enabled property
            with patch.object(
                type(authenticator), "enabled", property(lambda self: True)
            ):
                with patch.object(authenticator, "authenticate", return_value=None):
                    response = client.get("/test-auth-required")

                    assert response.status_code == 401
                    assert "WWW-Authenticate" in response.headers

    def test_optional_auth_allows_without_token(
        self, app: Any, client: Any, test_settings: Any
    ) -> None:
        """Test optional_auth allows access without token."""
        from flask import jsonify, g
        from common.auth.oidc import OIDCAuthenticator

        test_settings.OIDC_ENABLED = False
        authenticator = OIDCAuthenticator(test_settings)

        @app.route("/test-optional-auth")
        @authenticator.optional_auth
        def optional_route() -> Any:
            return jsonify({"user": g.oidc_user})

        response = client.get("/test-optional-auth")

        assert response.status_code == 200
        data = response.get_json()
        assert data["user"] is None


class TestOIDCProvider:
    """Tests for OIDCProvider."""

    def test_discovery_url_format(self) -> None:
        """Test discovery URL is correctly formatted."""
        from common.auth.oidc import OIDCProvider

        provider = OIDCProvider(
            issuer_url="https://auth.example.com/realms/test",
            client_id="test-client",
        )

        expected = "https://auth.example.com/realms/test/.well-known/openid-configuration"
        assert provider.discovery_url == expected

    def test_discovery_url_strips_trailing_slash(self) -> None:
        """Test discovery URL strips trailing slash from issuer."""
        from common.auth.oidc import OIDCProvider

        provider = OIDCProvider(
            issuer_url="https://auth.example.com/realms/test/",
            client_id="test-client",
        )

        expected = "https://auth.example.com/realms/test/.well-known/openid-configuration"
        assert provider.discovery_url == expected

    def test_validate_token_expired(self, mock_oidc_config: dict[str, Any]) -> None:
        """Test validate_token raises for expired tokens."""
        from common.auth.oidc import OIDCProvider, OIDCTokenError

        provider = OIDCProvider(
            issuer_url="https://auth.example.com/realms/test",
            client_id="test-client",
        )

        # Mock the config and JWKS fetching
        with patch.object(provider, "_get_config", return_value=mock_oidc_config):
            mock_jwks_client = MagicMock()
            mock_signing_key = MagicMock()
            mock_signing_key.key = "fake-key"
            mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

            with patch.object(provider, "_get_jwks_client", return_value=mock_jwks_client):
                import jwt
                with patch.object(jwt, "decode") as mock_decode:
                    mock_decode.side_effect = jwt.ExpiredSignatureError("Token expired")

                    with pytest.raises(OIDCTokenError) as exc_info:
                        provider.validate_token("expired-token")

                    assert "expired" in str(exc_info.value).lower()
