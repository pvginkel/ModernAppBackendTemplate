"""Tests for AuthService JWT validation."""

from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.exceptions import AuthenticationException
from app.services.auth_service import AuthContext, AuthService


def _create_auth_service(
    auth_settings: Settings,
    mock_oidc_discovery: dict,
    generate_test_jwt: object,
) -> AuthService:
    """Helper to create an AuthService with mocked OIDC discovery and JWKS."""
    with patch("httpx.get") as mock_get:
        mock_response = MagicMock()
        mock_response.json.return_value = mock_oidc_discovery
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        with patch("app.services.auth_service.PyJWKClient") as mock_jwk_client_class:
            mock_jwk_client = MagicMock()
            mock_signing_key = MagicMock()
            mock_signing_key.key = generate_test_jwt.public_key  # type: ignore[attr-defined]
            mock_jwk_client.get_signing_key_from_jwt.return_value = mock_signing_key
            mock_jwk_client_class.return_value = mock_jwk_client

            return AuthService(auth_settings)


class TestAuthService:
    """Test suite for AuthService."""

    @pytest.fixture
    def auth_settings(self, test_settings: Settings) -> Settings:
        """Create settings with OIDC enabled."""
        return test_settings.model_copy(
            update={
                "oidc_enabled": True,
                "oidc_issuer_url": "https://auth.example.com/realms/test",
                "oidc_client_id": "test-backend",
                "oidc_client_secret": "test-secret",
            }
        )

    def test_validate_token_success(
        self, auth_settings, generate_test_jwt, mock_oidc_discovery, mock_jwks
    ):
        """Test successful token validation extracts correct claims."""
        auth_service = _create_auth_service(
            auth_settings, mock_oidc_discovery, generate_test_jwt
        )

        token = generate_test_jwt(subject="user-123", roles=["admin"])
        auth_context = auth_service.validate_token(token)

        assert auth_context.subject == "user-123"
        assert auth_context.email == "test@example.com"
        assert auth_context.name == "Test User"
        assert "admin" in auth_context.roles

    def test_validate_token_with_custom_role(
        self, auth_settings, generate_test_jwt, mock_oidc_discovery
    ):
        """Test token validation with a non-admin role."""
        auth_service = _create_auth_service(
            auth_settings, mock_oidc_discovery, generate_test_jwt
        )

        token = generate_test_jwt(roles=["viewer"])
        auth_context = auth_service.validate_token(token)

        assert "viewer" in auth_context.roles
        assert "admin" not in auth_context.roles

    def test_validate_token_m2m_without_email(
        self, auth_settings, generate_test_jwt, mock_oidc_discovery
    ):
        """Test token validation for M2M client without email or name claims."""
        auth_service = _create_auth_service(
            auth_settings, mock_oidc_discovery, generate_test_jwt
        )

        token = generate_test_jwt(email=None, name=None, roles=["admin"])
        auth_context = auth_service.validate_token(token)

        assert auth_context.email is None
        assert auth_context.name is None
        assert "admin" in auth_context.roles

    def test_validate_token_expired(
        self, auth_settings, generate_test_jwt, mock_oidc_discovery
    ):
        """Test token validation fails for expired token."""
        auth_service = _create_auth_service(
            auth_settings, mock_oidc_discovery, generate_test_jwt
        )

        token = generate_test_jwt(expired=True)

        with pytest.raises(AuthenticationException) as exc_info:
            auth_service.validate_token(token)

        assert "expired" in str(exc_info.value).lower()

    def test_validate_token_invalid_signature(
        self, auth_settings, generate_test_jwt, mock_oidc_discovery
    ):
        """Test token validation fails for token with invalid signature."""
        auth_service = _create_auth_service(
            auth_settings, mock_oidc_discovery, generate_test_jwt
        )

        token = generate_test_jwt(invalid_signature=True)

        with pytest.raises(AuthenticationException) as exc_info:
            auth_service.validate_token(token)

        assert "signature" in str(exc_info.value).lower()

    def test_validate_token_invalid_issuer(
        self, auth_settings, generate_test_jwt, mock_oidc_discovery
    ):
        """Test token validation fails for token with wrong issuer."""
        auth_service = _create_auth_service(
            auth_settings, mock_oidc_discovery, generate_test_jwt
        )

        token = generate_test_jwt(invalid_issuer=True)

        with pytest.raises(AuthenticationException) as exc_info:
            auth_service.validate_token(token)

        assert "issuer" in str(exc_info.value).lower() or "audience" in str(
            exc_info.value
        ).lower()

    def test_validate_token_invalid_audience(
        self, auth_settings, generate_test_jwt, mock_oidc_discovery
    ):
        """Test token validation fails for token with wrong audience."""
        auth_service = _create_auth_service(
            auth_settings, mock_oidc_discovery, generate_test_jwt
        )

        token = generate_test_jwt(invalid_audience=True)

        with pytest.raises(AuthenticationException) as exc_info:
            auth_service.validate_token(token)

        assert "issuer" in str(exc_info.value).lower() or "audience" in str(
            exc_info.value
        ).lower()

    def test_auth_context_dataclass(self):
        """Test AuthContext dataclass construction and attributes."""
        ctx = AuthContext(
            subject="user-abc",
            email="user@example.com",
            name="Test User",
            roles={"admin", "viewer"},
        )

        assert ctx.subject == "user-abc"
        assert ctx.email == "user@example.com"
        assert ctx.name == "Test User"
        assert ctx.roles == {"admin", "viewer"}

    def test_oidc_disabled_does_not_init_jwks(self, test_settings):
        """Test that AuthService does not initialize JWKS when OIDC is disabled."""
        auth_service = AuthService(test_settings)

        assert auth_service._jwks_client is None

    def test_oidc_disabled_validate_token_raises(self, test_settings):
        """Test that validate_token raises when OIDC is disabled."""
        auth_service = AuthService(test_settings)

        with pytest.raises(AuthenticationException, match="not enabled"):
            auth_service.validate_token("some-token")

    def test_missing_issuer_url_raises_value_error(self, test_settings):
        """Test that OIDC enabled without issuer URL raises ValueError."""
        settings = test_settings.model_copy(
            update={
                "oidc_enabled": True,
                "oidc_issuer_url": None,
                "oidc_client_id": "test-backend",
            }
        )

        with pytest.raises(ValueError, match="OIDC_ISSUER_URL"):
            AuthService(settings)

    def test_missing_client_id_raises_value_error(self, test_settings):
        """Test that OIDC enabled without client ID raises ValueError."""
        settings = test_settings.model_copy(
            update={
                "oidc_enabled": True,
                "oidc_issuer_url": "https://auth.example.com/realms/test",
                "oidc_client_id": None,
            }
        )

        with pytest.raises(ValueError, match="OIDC_CLIENT_ID"):
            AuthService(settings)

    def test_extract_roles_from_realm_access(self):
        """Test role extraction from realm_access.roles."""
        settings = Settings(oidc_enabled=False)
        auth_service = AuthService(settings)

        payload = {
            "realm_access": {"roles": ["admin", "viewer"]},
        }
        roles = auth_service._extract_roles(payload, "test-backend")

        assert roles == {"admin", "viewer"}

    def test_extract_roles_from_resource_access(self):
        """Test role extraction from resource_access.<audience>.roles."""
        settings = Settings(oidc_enabled=False)
        auth_service = AuthService(settings)

        payload = {
            "realm_access": {"roles": ["admin"]},
            "resource_access": {"test-backend": {"roles": ["custom-role"]}},
        }
        roles = auth_service._extract_roles(payload, "test-backend")

        assert roles == {"admin", "custom-role"}

    def test_extract_roles_empty_payload(self):
        """Test role extraction from payload with no role claims."""
        settings = Settings(oidc_enabled=False)
        auth_service = AuthService(settings)

        payload = {}
        roles = auth_service._extract_roles(payload, "test-backend")

        assert roles == set()
