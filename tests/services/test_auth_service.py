"""Tests for AuthService JWT validation."""

import time
from unittest.mock import MagicMock, patch

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import Settings
from app.exceptions import AuthenticationException
from app.services.auth_service import (
    AUTH_VALIDATION_DURATION_SECONDS,
    AUTH_VALIDATION_TOTAL,
    JWKS_REFRESH_TOTAL,
    AuthContext,
    AuthService,
)


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


# ---------------------------------------------------------------------------
# Validation edge cases, claim shapes, JWKS discovery and OIDC start-up, each
# against an AuthService with a stubbed JWKS client.
# ---------------------------------------------------------------------------

_ISSUER = "https://auth.example.com/realms/test"
_AUDIENCE = "test-backend"
_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _oidc_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "oidc_enabled": False,
        "oidc_issuer_url": _ISSUER,
        "oidc_client_id": _AUDIENCE,
        "oidc_audience": _AUDIENCE,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _service_with_jwks() -> AuthService:
    """An AuthService built with OIDC off, then given a JWKS client holding our key."""
    service = AuthService(_oidc_settings())
    jwks_client = MagicMock()
    jwks_client.get_signing_key_from_jwt.return_value = MagicMock(key=_private_key.public_key())
    service._jwks_client = jwks_client
    return service


def _token(*, omit_sub: bool = False) -> str:
    now = int(time.time())
    payload: dict[str, object] = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "exp": now + 3600,
        "iat": now,
        "realm_access": {"roles": []},
    }
    if not omit_sub:
        payload["sub"] = "test-user"
    return pyjwt.encode(payload, _private_key, algorithm="RS256", headers={"kid": "test-key-id"})


def _validations(status: str) -> float:
    return AUTH_VALIDATION_TOTAL.labels(status=status)._value.get()


def _jwks_refreshes(status: str) -> float:
    return JWKS_REFRESH_TOTAL.labels(trigger="startup", status=status)._value.get()


def _discovery_response(document: dict[str, str]) -> MagicMock:
    response = MagicMock()
    response.json.return_value = document
    return response


class TestValidateTokenEdgeCases:
    def test_missing_sub_is_rejected_and_counted(self):
        service = _service_with_jwks()
        before = _validations("error")

        with pytest.raises(AuthenticationException, match="Token missing 'sub' claim"):
            service.validate_token(_token(omit_sub=True))

        assert _validations("error") - before == 1.0

    def test_malformed_token_is_invalid(self):
        service = _service_with_jwks()
        before = _validations("invalid_token")

        with pytest.raises(AuthenticationException, match="Invalid token"):
            service.validate_token("not.a.valid.jwt.at.all")

        assert _validations("invalid_token") - before == 1.0

    def test_unexpected_error_is_wrapped_and_counted(self):
        service = AuthService(_oidc_settings())
        jwks_client = MagicMock()
        jwks_client.get_signing_key_from_jwt.side_effect = ConnectionError("JWKS unavailable")
        service._jwks_client = jwks_client
        before = _validations("error")

        with pytest.raises(AuthenticationException, match="Token validation failed.*JWKS unavailable"):
            service.validate_token("any-token")

        assert _validations("error") - before == 1.0

    def test_success_observes_the_duration(self):
        service = _service_with_jwks()

        def observed() -> float:
            for family in AUTH_VALIDATION_DURATION_SECONDS.collect():
                for sample in family.samples:
                    if sample.name.endswith("_count"):
                        return sample.value
            return 0.0

        before = observed()

        assert service.validate_token(_token()).subject == "test-user"
        assert observed() - before == 1.0


class TestExtractRoleShapes:
    def _roles(self, payload: dict[str, object], audience: str | None = "my-app") -> set[str]:
        return AuthService(_oidc_settings())._extract_roles(payload, audience)

    def test_realm_access_not_a_dict(self):
        assert self._roles({"realm_access": "not-a-dict"}) == set()

    def test_no_resource_access(self):
        assert self._roles({"realm_access": {"roles": ["reader"]}}) == {"reader"}

    def test_no_audience_skips_resource_roles(self):
        payload = {
            "realm_access": {"roles": ["reader"]},
            "resource_access": {"my-app": {"roles": ["editor"]}},
        }

        assert self._roles(payload, audience=None) == {"reader"}

    def test_both_lists_empty(self):
        payload = {
            "realm_access": {"roles": []},
            "resource_access": {"my-app": {"roles": []}},
        }

        assert self._roles(payload) == set()


class TestDiscoverJwksUri:
    def test_returns_the_jwks_uri(self):
        service = AuthService(_oidc_settings())
        response = _discovery_response({"jwks_uri": "https://auth.example.com/certs"})

        with patch("app.services.auth_service.httpx.get", return_value=response):
            assert service._discover_jwks_uri() == "https://auth.example.com/certs"

    def test_http_error_is_an_authentication_error(self):
        service = AuthService(_oidc_settings())

        with (
            patch("app.services.auth_service.httpx.get", side_effect=httpx.ConnectError("refused")),
            pytest.raises(AuthenticationException, match="Failed to discover JWKS endpoint"),
        ):
            service._discover_jwks_uri()

    def test_document_without_jwks_uri_is_rejected(self):
        service = AuthService(_oidc_settings())
        response = _discovery_response({"issuer": _ISSUER})

        with (
            patch("app.services.auth_service.httpx.get", return_value=response),
            pytest.raises(AuthenticationException, match="JWKS URI not found"),
        ):
            service._discover_jwks_uri()


class TestOidcStartup:
    def test_startup_builds_the_jwks_client_and_counts_it(self):
        settings = _oidc_settings(oidc_enabled=True, oidc_client_secret="secret")
        response = _discovery_response({"jwks_uri": "https://auth.example.com/certs"})
        before = _jwks_refreshes("success")

        with (
            patch("app.services.auth_service.httpx.get", return_value=response),
            patch("app.services.auth_service.PyJWKClient", return_value=MagicMock()),
        ):
            service = AuthService(settings)

        assert service._jwks_client is not None
        assert service._jwks_uri == "https://auth.example.com/certs"
        assert _jwks_refreshes("success") - before == 1.0

    def test_jwks_client_failure_propagates_and_is_counted(self):
        settings = _oidc_settings(oidc_enabled=True, oidc_client_secret="secret")
        response = _discovery_response({"jwks_uri": "https://auth.example.com/certs"})
        before = _jwks_refreshes("failed")

        with (
            patch("app.services.auth_service.httpx.get", return_value=response),
            patch("app.services.auth_service.PyJWKClient", side_effect=RuntimeError("JWKS init failed")),
            pytest.raises(RuntimeError, match="JWKS init failed"),
        ):
            AuthService(settings)

        assert _jwks_refreshes("failed") - before == 1.0


def _role_service(
    read_role: str | None = None,
    write_role: str | None = None,
    admin_role: str | None = None,
) -> AuthService:
    """An AuthService with OIDC off and the given role tiers."""
    config = MagicMock()
    config.oidc_enabled = False
    return AuthService(
        config=config, read_role=read_role, write_role=write_role, admin_role=admin_role
    )


class TestResolveRequiredRole:
    """resolve_required_role: the role a request needs, by method and decorator."""

    def test_get_returns_read_role(self) -> None:
        svc = _role_service(read_role="reader", write_role="editor")
        assert svc.resolve_required_role("GET", None) == "reader"

    def test_post_returns_write_role(self) -> None:
        svc = _role_service(read_role="reader", write_role="editor")
        assert svc.resolve_required_role("POST", None) == "editor"

    def test_allow_roles_override(self) -> None:
        from app.utils.auth import allow_roles

        svc = _role_service(write_role="editor")

        @allow_roles("special")
        def view() -> None:
            pass

        assert svc.resolve_required_role("POST", view) == {"special"}

    def test_safe_query_forces_read(self) -> None:
        from app.utils.auth import safe_query

        svc = _role_service(read_role="reader", write_role="editor")

        @safe_query
        def view() -> None:
            pass

        assert svc.resolve_required_role("POST", view) == "reader"

    def test_write_falls_back_to_admin_role(self) -> None:
        svc = _role_service(admin_role="admin")
        assert svc.resolve_required_role("POST", None) == "admin"

    def test_no_roles_means_no_gate(self) -> None:
        svc = _role_service()
        assert svc.resolve_required_role("GET", None) is None
        assert svc.resolve_required_role("POST", None) is None

    def test_head_is_a_read(self) -> None:
        svc = _role_service(write_role="editor")
        assert svc.resolve_required_role("HEAD", None) is None
        svc = _role_service(read_role="reader", write_role="editor")
        assert svc.resolve_required_role("HEAD", None) == "reader"
