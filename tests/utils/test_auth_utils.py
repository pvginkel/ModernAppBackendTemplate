"""Tests for authentication utilities including decorators, token utilities, and state serialization."""

import time
from unittest.mock import MagicMock

import jwt
import pytest

from app.exceptions import AuthorizationException, ValidationException
from app.services.auth_service import AuthContext, AuthService
from app.services.oidc_client_service import AuthState
from app.utils.auth import (
    PendingTokenRefresh,
    allow_roles,
    check_authorization,
    deserialize_auth_state,
    get_cookie_kwargs,
    get_token_expiry_seconds,
    no_role_check,
    public,
    serialize_auth_state,
    validate_redirect_url,
)


def _make_auth_service(*additional_roles: str) -> AuthService:
    """Create a minimal AuthService (OIDC disabled) with the given additional_roles."""
    config = MagicMock()
    config.oidc_enabled = False
    return AuthService(config=config, additional_roles=list(additional_roles) if additional_roles else None)


class TestGetTokenExpirySeconds:
    """Test suite for get_token_expiry_seconds utility function."""

    def test_valid_jwt_returns_remaining_seconds(self):
        """Test that a valid JWT returns correct remaining seconds."""
        exp_time = int(time.time()) + 3600
        payload = {"sub": "test-user", "exp": exp_time}
        token = jwt.encode(payload, "secret", algorithm="HS256")

        remaining = get_token_expiry_seconds(token)

        assert remaining is not None
        assert 3590 <= remaining <= 3600

    def test_expired_jwt_returns_zero(self):
        """Test that an expired JWT returns 0 (not negative)."""
        exp_time = int(time.time()) - 3600
        payload = {"sub": "test-user", "exp": exp_time}
        token = jwt.encode(payload, "secret", algorithm="HS256")

        remaining = get_token_expiry_seconds(token)

        assert remaining == 0

    def test_jwt_without_exp_returns_none(self):
        """Test that a JWT without exp claim returns None."""
        payload = {"sub": "test-user"}
        token = jwt.encode(payload, "secret", algorithm="HS256")

        remaining = get_token_expiry_seconds(token)

        assert remaining is None

    def test_invalid_jwt_returns_none(self):
        """Test that an invalid JWT string returns None."""
        remaining = get_token_expiry_seconds("not-a-valid-jwt")

        assert remaining is None

    def test_empty_string_returns_none(self):
        """Test that empty string returns None."""
        remaining = get_token_expiry_seconds("")

        assert remaining is None

    def test_opaque_token_returns_none(self):
        """Test that an opaque (non-JWT) token returns None."""
        remaining = get_token_expiry_seconds("some-opaque-refresh-token-abc123")

        assert remaining is None


class TestPendingTokenRefresh:
    """Test suite for PendingTokenRefresh dataclass."""

    def test_create_with_refresh_token(self):
        """Test creating PendingTokenRefresh with all fields."""
        pending = PendingTokenRefresh(
            access_token="new-access-token",
            refresh_token="new-refresh-token",
            access_token_expires_in=300,
        )

        assert pending.access_token == "new-access-token"
        assert pending.refresh_token == "new-refresh-token"
        assert pending.access_token_expires_in == 300

    def test_create_without_refresh_token(self):
        """Test creating PendingTokenRefresh without refresh token."""
        pending = PendingTokenRefresh(
            access_token="new-access-token",
            refresh_token=None,
            access_token_expires_in=300,
        )

        assert pending.access_token == "new-access-token"
        assert pending.refresh_token is None
        assert pending.access_token_expires_in == 300


class TestPublicDecorator:
    """Test suite for @public decorator."""

    def test_public_sets_is_public_attribute(self):
        """Test that @public sets is_public=True on the function."""

        @public
        def my_endpoint():
            return "public data"

        assert hasattr(my_endpoint, "is_public")
        assert my_endpoint.is_public is True

    def test_public_preserves_function_behavior(self):
        """Test that @public doesn't change function behavior."""

        @public
        def my_endpoint(x, y):
            return x + y

        assert my_endpoint(2, 3) == 5

    def test_function_without_public_has_no_is_public(self):
        """Test that functions without @public don't have is_public attribute."""

        def my_endpoint():
            return "private data"

        assert not getattr(my_endpoint, "is_public", False)


class TestAllowRolesDecorator:
    """Test suite for @allow_roles decorator."""

    def test_allow_roles_sets_allowed_roles_attribute(self):
        """Test that @allow_roles sets allowed_roles as a set."""

        @allow_roles("editor")
        def my_endpoint():
            return "data"

        assert hasattr(my_endpoint, "allowed_roles")
        assert my_endpoint.allowed_roles == {"editor"}

    def test_allow_roles_multiple_roles(self):
        """Test that @allow_roles handles multiple roles."""

        @allow_roles("editor", "viewer", "admin")
        def my_endpoint():
            return "data"

        assert my_endpoint.allowed_roles == {"editor", "viewer", "admin"}

    def test_allow_roles_preserves_function_behavior(self):
        """Test that @allow_roles doesn't change function behavior."""

        @allow_roles("editor")
        def my_endpoint(x):
            return x * 2

        assert my_endpoint(5) == 10

    def test_function_without_allow_roles_has_no_allowed_roles(self):
        """Test that functions without @allow_roles don't have allowed_roles."""

        def my_endpoint():
            return "data"

        assert not hasattr(my_endpoint, "allowed_roles")


class TestCheckAuthorization:
    """Test suite for check_authorization function.

    Uses method-based role inference: GET -> read_role, POST -> write_role.
    @allow_roles overrides method inference completely.
    """

    def test_authenticated_user_passes_without_roles_configured(self):
        """Test that any authenticated user passes when no roles are configured."""
        auth_service = _make_auth_service()
        auth_context = AuthContext(
            subject="regular-user",
            email="user@example.com",
            name="Regular User",
            roles={"some-role"},
        )

        check_authorization(auth_context, auth_service, "GET", view_func=None)

    def test_authenticated_user_with_any_role_passes(self):
        """Test that a user with any role passes when no role gate is configured."""
        auth_service = _make_auth_service()

        def regular_endpoint():
            pass

        auth_context = AuthContext(
            subject="user",
            email="user@example.com",
            name="User",
            roles={"custom-role"},
        )

        check_authorization(auth_context, auth_service, "GET", view_func=regular_endpoint)

    def test_empty_roles_passes_without_roles_configured(self):
        """Test that a user with no roles still passes when no role gate is configured."""
        auth_service = _make_auth_service()
        auth_context = AuthContext(
            subject="no-role-user",
            email="norole@example.com",
            name="No Role User",
            roles=set(),
        )

        check_authorization(auth_context, auth_service, "GET", view_func=None)

    def test_allowed_role_grants_access(self):
        """Test that a role listed in @allow_roles grants access."""
        auth_service = _make_auth_service("editor")

        @allow_roles("editor")
        def editor_endpoint():
            pass

        auth_context = AuthContext(
            subject="editor-user",
            email="editor@example.com",
            name="Editor User",
            roles={"editor"},
        )

        check_authorization(auth_context, auth_service, "GET", view_func=editor_endpoint)

    def test_one_of_multiple_allowed_roles_grants_access(self):
        """Test that having one of multiple allowed roles grants access."""
        auth_service = _make_auth_service("editor", "viewer", "admin")

        @allow_roles("editor", "viewer", "admin")
        def multi_role_endpoint():
            pass

        auth_context = AuthContext(
            subject="viewer-user",
            email="viewer@example.com",
            name="Viewer User",
            roles={"viewer"},
        )

        check_authorization(auth_context, auth_service, "GET", view_func=multi_role_endpoint)

    def test_no_matching_role_denied_with_allow_roles(self):
        """Test that a user without a matching role is denied when @allow_roles is set."""
        auth_service = _make_auth_service("admin", "viewer")

        @allow_roles("admin")
        def admin_endpoint():
            pass

        auth_context = AuthContext(
            subject="viewer-user",
            email="viewer@example.com",
            name="Viewer User",
            roles={"viewer"},
        )

        with pytest.raises(AuthorizationException) as exc_info:
            check_authorization(auth_context, auth_service, "GET", view_func=admin_endpoint)

        assert "admin" in str(exc_info.value)

    def test_empty_roles_denied_with_allow_roles(self):
        """Test that a user with no roles is denied when @allow_roles is set."""
        auth_service = _make_auth_service("editor")

        @allow_roles("editor")
        def editor_endpoint():
            pass

        auth_context = AuthContext(
            subject="no-role-user",
            email="norole@example.com",
            name="No Role User",
            roles=set(),
        )

        with pytest.raises(AuthorizationException):
            check_authorization(auth_context, auth_service, "GET", view_func=editor_endpoint)

    def test_error_message_lists_required_roles(self):
        """Test error message format when endpoint has @allow_roles."""
        auth_service = _make_auth_service("editor", "admin", "viewer")

        @allow_roles("editor", "admin")
        def restricted_endpoint():
            pass

        auth_context = AuthContext(
            subject="user",
            email="user@example.com",
            name="User",
            roles={"viewer"},
        )

        with pytest.raises(AuthorizationException) as exc_info:
            check_authorization(auth_context, auth_service, "GET", view_func=restricted_endpoint)

        error_msg = str(exc_info.value)
        assert "admin" in error_msg
        assert "editor" in error_msg


class TestNoRecognizedRole:
    """A caller holding none of the configured roles gets 403 on every endpoint (D2)."""

    @staticmethod
    def _write_gated_service(*additional_roles: str) -> AuthService:
        """write_role set, read_role unset: GETs have no role gate."""
        config = MagicMock()
        config.oidc_enabled = False
        return AuthService(
            config=config,
            write_role="editor",
            additional_roles=list(additional_roles) if additional_roles else None,
        )

    def test_get_without_read_role_denies_caller_with_no_role(self):
        auth_service = self._write_gated_service()
        assert auth_service.resolve_required_role("GET", None) is None

        auth_context = AuthContext(
            subject="outsider", email=None, name=None, roles={"default-roles-homelab"}
        )

        with pytest.raises(AuthorizationException, match="No recognized role"):
            check_authorization(auth_context, auth_service, "GET", view_func=None)

    def test_get_without_read_role_denies_caller_with_empty_roles(self):
        auth_service = self._write_gated_service()
        auth_context = AuthContext(subject="outsider", email=None, name=None, roles=set())

        with pytest.raises(AuthorizationException, match="No recognized role"):
            check_authorization(auth_context, auth_service, "GET", view_func=None)

    def test_get_without_read_role_allows_configured_role(self):
        auth_service = self._write_gated_service()
        auth_context = AuthContext(subject="editor", email=None, name=None, roles={"editor"})

        check_authorization(auth_context, auth_service, "GET", view_func=None)

    def test_additional_role_counts_as_recognized(self):
        auth_service = self._write_gated_service("pipeline")
        auth_context = AuthContext(subject="ci", email=None, name=None, roles={"pipeline"})

        check_authorization(auth_context, auth_service, "GET", view_func=None)

        with pytest.raises(AuthorizationException, match="Insufficient permissions"):
            check_authorization(auth_context, auth_service, "POST", view_func=None)

    def test_no_recognized_role_wins_over_allow_roles_message(self):
        auth_service = self._write_gated_service("viewer")

        @allow_roles("viewer")
        def viewer_endpoint():
            pass

        auth_context = AuthContext(subject="outsider", email=None, name=None, roles={"other"})

        with pytest.raises(AuthorizationException, match="No recognized role"):
            check_authorization(auth_context, auth_service, "GET", view_func=viewer_endpoint)


class TestNoRoleCheck:
    """@no_role_check skips every role check."""

    def test_sets_attribute_and_returns_function(self):
        def endpoint():
            return "ok"

        decorated = no_role_check(endpoint)

        assert decorated is endpoint
        assert endpoint.is_no_role_check is True  # type: ignore[attr-defined]
        assert decorated() == "ok"

    def test_caller_with_no_role_passes(self):
        config = MagicMock()
        config.oidc_enabled = False
        auth_service = AuthService(config=config, read_role="reader", write_role="editor")

        @no_role_check
        def logout():
            pass

        auth_context = AuthContext(subject="outsider", email=None, name=None, roles=set())

        check_authorization(auth_context, auth_service, "GET", view_func=logout)
        check_authorization(auth_context, auth_service, "POST", view_func=logout)

    def test_skips_allow_roles(self):
        auth_service = _make_auth_service("admin")

        @no_role_check
        @allow_roles("admin")
        def endpoint():
            pass

        auth_context = AuthContext(subject="user", email=None, name=None, roles={"viewer"})

        check_authorization(auth_context, auth_service, "GET", view_func=endpoint)


class TestSerializeDeserializeAuthState:
    """Test suite for auth state serialization and deserialization."""

    def test_roundtrip_serialization(self):
        """Test that serialization and deserialization produce the original state."""
        state = AuthState(
            code_verifier="test-verifier-12345",
            redirect_url="/dashboard",
            nonce="test-nonce-67890",
        )

        signed = serialize_auth_state(state, "test-secret-key")
        recovered = deserialize_auth_state(signed, "test-secret-key")

        assert recovered.code_verifier == state.code_verifier
        assert recovered.redirect_url == state.redirect_url
        assert recovered.nonce == state.nonce
        assert recovered.origin is None

    def test_origin_roundtrips(self):
        """The origin login stored comes back for the callback."""
        state = AuthState(
            code_verifier="test-verifier",
            redirect_url="/dashboard",
            nonce="test-nonce",
            origin="https://app.example.com:8443",
        )

        recovered = deserialize_auth_state(
            serialize_auth_state(state, "test-secret-key"), "test-secret-key"
        )

        assert recovered.origin == "https://app.example.com:8443"

    def test_state_without_origin_key_reads_as_none(self):
        """A state made before origins were stored still deserializes."""
        import json

        from cryptography.fernet import Fernet

        from app.utils.auth import _derive_fernet_key

        legacy = Fernet(_derive_fernet_key("test-secret-key")).encrypt(
            json.dumps(
                {"code_verifier": "v", "redirect_url": "/", "nonce": "n"}
            ).encode()
        ).decode("ascii")

        recovered = deserialize_auth_state(legacy, "test-secret-key")

        assert recovered.origin is None
        assert recovered.redirect_url == "/"

    def test_wrong_secret_raises_validation_exception(self):
        """Test that deserialization with wrong key fails."""
        state = AuthState(
            code_verifier="test-verifier",
            redirect_url="/home",
            nonce="test-nonce",
        )

        signed = serialize_auth_state(state, "correct-secret")

        with pytest.raises(ValidationException, match="Invalid"):
            deserialize_auth_state(signed, "wrong-secret")

    def test_tampered_data_raises_validation_exception(self):
        """Test that tampered signed data fails deserialization."""
        with pytest.raises(ValidationException):
            deserialize_auth_state("tampered-data", "test-secret-key")

    def test_empty_string_raises_validation_exception(self):
        """Test that empty string fails deserialization."""
        with pytest.raises(ValidationException):
            deserialize_auth_state("", "test-secret-key")


class TestGetCookieKwargs:
    """Test suite for get_cookie_kwargs utility."""

    def test_returns_expected_keys(self):
        """Test that get_cookie_kwargs returns the expected keys."""
        from app.config import Settings

        settings = Settings(oidc_cookie_secure=True, oidc_cookie_samesite="Strict")

        kw = get_cookie_kwargs(settings)

        assert set(kw.keys()) == {"httponly", "secure", "samesite", "partitioned"}

    def test_reflects_secure_and_samesite_settings(self):
        """Test that cookie kwargs reflect config values."""
        from app.config import Settings

        settings = Settings(oidc_cookie_secure=True, oidc_cookie_samesite="None", oidc_cookie_partitioned=True)

        kw = get_cookie_kwargs(settings)

        assert kw["httponly"] is True
        assert kw["secure"] is True
        assert kw["samesite"] == "None"
        assert kw["partitioned"] is True

    def test_defaults(self):
        """Test default cookie kwargs."""
        from app.config import Settings

        settings = Settings()

        kw = get_cookie_kwargs(settings)

        assert kw["httponly"] is True
        assert kw["secure"] is False
        assert kw["samesite"] == "Lax"
        assert kw["partitioned"] is False


class TestValidateRedirectUrl:
    """validate_redirect_url accepts only a path on the app's own origin."""

    @pytest.mark.parametrize(
        "redirect_url",
        ["/", "/dashboard", "/some/deep/path?query=1", "/items?filter=a%2F%2Fb#top"],
    )
    def test_path_allowed(self, redirect_url: str):
        validate_redirect_url(redirect_url)

    @pytest.mark.parametrize(
        "redirect_url",
        [
            "https://evil.com/steal",
            # Absolute URLs are refused even on the app's own origin
            "http://localhost:3000/dashboard",
            # Protocol-relative: a host without a scheme
            "//evil.com",
            "//evil.com/path",
            "///evil.com",
            # Browsers read backslashes as slashes
            "/\\evil.com",
            "\\\\evil.com",
            "/path\\with-backslash",
            # Browsers drop tabs and newlines, which would leave //evil.com
            "/\t/evil.com",
            "/\n/evil.com",
            "/\r/evil.com",
            "/path\x7f",
            # No leading slash: would resolve against /api/auth/
            "dashboard",
            ".evil.com",
            "javascript:alert(1)",
            "",
        ],
    )
    def test_anything_else_rejected(self, redirect_url: str):
        with pytest.raises(ValidationException, match="must be a path on this site"):
            validate_redirect_url(redirect_url)
