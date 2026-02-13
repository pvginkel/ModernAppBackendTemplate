"""Tests for OidcClientService OIDC authorization code flow with PKCE."""

import base64
import hashlib
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.config import Settings
from app.exceptions import AuthenticationException
from app.services.oidc_client_service import (
    AuthState,
    OidcClientService,
    OidcEndpoints,
    TokenResponse,
)


def _oidc_settings(base: Settings) -> Settings:
    """Create settings with OIDC enabled and all required fields populated."""
    return base.model_copy(
        update={
            "oidc_enabled": True,
            "oidc_issuer_url": "https://auth.example.com/realms/test",
            "oidc_client_id": "test-backend",
            "oidc_client_secret": "test-client-secret",
            "oidc_scopes": "openid profile email",
            "baseurl": "http://localhost:3000",
        }
    )


def _build_service(
    settings: Settings,
    discovery_doc: dict | None = None,
) -> OidcClientService:
    """Build an OidcClientService with mocked httpx.get for discovery."""
    if discovery_doc is None and settings.oidc_enabled:
        discovery_doc = {
            "authorization_endpoint": "https://auth.example.com/realms/test/protocol/openid-connect/auth",
            "token_endpoint": "https://auth.example.com/realms/test/protocol/openid-connect/token",
            "end_session_endpoint": "https://auth.example.com/realms/test/protocol/openid-connect/logout",
            "jwks_uri": "https://auth.example.com/realms/test/protocol/openid-connect/certs",
        }

    if settings.oidc_enabled:
        with patch("httpx.get") as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = discovery_doc
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response
            return OidcClientService(settings)
    else:
        return OidcClientService(settings)


class TestOidcEndpointDiscovery:
    """Tests for OIDC endpoint discovery from the well-known configuration."""

    @pytest.fixture
    def oidc_settings(self, test_settings: Settings) -> Settings:
        """Create settings with OIDC enabled."""
        return _oidc_settings(test_settings)

    def test_successful_discovery(self, oidc_settings: Settings) -> None:
        """Test that endpoints are discovered and cached from the well-known URL."""
        discovery_doc = {
            "authorization_endpoint": "https://auth.example.com/auth",
            "token_endpoint": "https://auth.example.com/token",
            "end_session_endpoint": "https://auth.example.com/logout",
            "jwks_uri": "https://auth.example.com/certs",
        }

        service = _build_service(oidc_settings, discovery_doc)

        endpoints = service.endpoints
        assert endpoints.authorization_endpoint == "https://auth.example.com/auth"
        assert endpoints.token_endpoint == "https://auth.example.com/token"
        assert endpoints.end_session_endpoint == "https://auth.example.com/logout"
        assert endpoints.jwks_uri == "https://auth.example.com/certs"

    def test_discovery_without_end_session_endpoint(self, oidc_settings: Settings) -> None:
        """Test that end_session_endpoint is optional."""
        discovery_doc = {
            "authorization_endpoint": "https://auth.example.com/auth",
            "token_endpoint": "https://auth.example.com/token",
            "jwks_uri": "https://auth.example.com/certs",
        }

        service = _build_service(oidc_settings, discovery_doc)

        endpoints = service.endpoints
        assert endpoints.end_session_endpoint is None
        assert endpoints.authorization_endpoint == "https://auth.example.com/auth"

    def test_discovery_retries_on_http_error(self, oidc_settings: Settings) -> None:
        """Test that discovery retries on transient HTTP errors."""
        valid_response = MagicMock()
        valid_response.json.return_value = {
            "authorization_endpoint": "https://auth.example.com/auth",
            "token_endpoint": "https://auth.example.com/token",
            "jwks_uri": "https://auth.example.com/certs",
        }
        valid_response.raise_for_status = MagicMock()

        with patch("httpx.get") as mock_get:
            mock_get.side_effect = [
                httpx.ConnectError("Connection refused"),
                httpx.ConnectError("Connection refused"),
                valid_response,
            ]
            service = OidcClientService(oidc_settings)

        assert mock_get.call_count == 3
        assert service.endpoints.authorization_endpoint == "https://auth.example.com/auth"

    def test_discovery_fails_after_max_retries(self, oidc_settings: Settings) -> None:
        """Test that discovery raises ValueError after all retry attempts."""
        with patch("httpx.get") as mock_get:
            mock_get.side_effect = httpx.ConnectError("Connection refused")

            with pytest.raises(
                ValueError, match="Failed to discover OIDC endpoints after 3 attempts"
            ):
                OidcClientService(oidc_settings)

        assert mock_get.call_count == 3

    def test_discovery_missing_authorization_endpoint(self, oidc_settings: Settings) -> None:
        """Test that discovery raises ValueError when authorization_endpoint is missing."""
        discovery_doc = {
            "token_endpoint": "https://auth.example.com/token",
            "jwks_uri": "https://auth.example.com/certs",
        }

        with pytest.raises(ValueError, match="Failed to discover OIDC endpoints"):
            _build_service(oidc_settings, discovery_doc)

    def test_discovery_missing_token_endpoint(self, oidc_settings: Settings) -> None:
        """Test that discovery raises ValueError when token_endpoint is missing."""
        discovery_doc = {
            "authorization_endpoint": "https://auth.example.com/auth",
            "jwks_uri": "https://auth.example.com/certs",
        }

        with pytest.raises(ValueError, match="Failed to discover OIDC endpoints"):
            _build_service(oidc_settings, discovery_doc)

    def test_discovery_missing_jwks_uri(self, oidc_settings: Settings) -> None:
        """Test that discovery raises ValueError when jwks_uri is missing."""
        discovery_doc = {
            "authorization_endpoint": "https://auth.example.com/auth",
            "token_endpoint": "https://auth.example.com/token",
        }

        with pytest.raises(ValueError, match="Failed to discover OIDC endpoints"):
            _build_service(oidc_settings, discovery_doc)

    def test_discovery_empty_document(self, oidc_settings: Settings) -> None:
        """Test that discovery raises ValueError when the document has no endpoints."""
        with pytest.raises(ValueError, match="Failed to discover OIDC endpoints"):
            _build_service(oidc_settings, discovery_doc={})

    def test_discovery_url_uses_issuer_url(self, oidc_settings: Settings) -> None:
        """Test that the discovery URL is constructed from oidc_issuer_url."""
        with patch("httpx.get") as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "authorization_endpoint": "https://auth.example.com/auth",
                "token_endpoint": "https://auth.example.com/token",
                "jwks_uri": "https://auth.example.com/certs",
            }
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response

            OidcClientService(oidc_settings)

            mock_get.assert_called_once_with(
                "https://auth.example.com/realms/test/.well-known/openid-configuration",
                timeout=10.0,
            )


class TestPkceChallenge:
    """Tests for PKCE code challenge generation (S256 method)."""

    @pytest.fixture
    def service(self, test_settings: Settings) -> OidcClientService:
        """Create an OidcClientService with OIDC enabled for PKCE tests."""
        return _build_service(_oidc_settings(test_settings))

    def test_generate_pkce_challenge_s256(self, service: OidcClientService) -> None:
        """Test that PKCE challenge matches RFC 7636 S256 encoding."""
        code_verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"

        sha256_digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        expected_challenge = (
            base64.urlsafe_b64encode(sha256_digest).rstrip(b"=").decode("ascii")
        )

        actual_challenge = service.generate_pkce_challenge(code_verifier)

        assert actual_challenge == expected_challenge

    def test_pkce_challenge_is_url_safe(self, service: OidcClientService) -> None:
        """Test that the PKCE challenge output is URL-safe base64 without padding."""
        code_verifier = "test-verifier-string-with-enough-length"
        challenge = service.generate_pkce_challenge(code_verifier)

        assert "+" not in challenge
        assert "/" not in challenge
        assert "=" not in challenge

    def test_pkce_challenge_deterministic(self, service: OidcClientService) -> None:
        """Test that the same verifier always produces the same challenge."""
        verifier = "consistent-verifier-value"
        challenge_1 = service.generate_pkce_challenge(verifier)
        challenge_2 = service.generate_pkce_challenge(verifier)

        assert challenge_1 == challenge_2

    def test_pkce_challenge_different_verifiers_differ(
        self, service: OidcClientService
    ) -> None:
        """Test that different verifiers produce different challenges."""
        challenge_a = service.generate_pkce_challenge("verifier-a")
        challenge_b = service.generate_pkce_challenge("verifier-b")

        assert challenge_a != challenge_b


class TestGenerateAuthorizationUrl:
    """Tests for OIDC authorization URL generation."""

    @pytest.fixture
    def service(self, test_settings: Settings) -> OidcClientService:
        """Create an OidcClientService with OIDC enabled."""
        return _build_service(_oidc_settings(test_settings))

    def test_authorization_url_contains_required_params(
        self, service: OidcClientService
    ) -> None:
        """Test that the generated authorization URL contains all required OAuth2/PKCE parameters."""
        url, auth_state = service.generate_authorization_url(
            "http://localhost:3000/dashboard"
        )

        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        assert params["client_id"] == ["test-backend"]
        assert params["response_type"] == ["code"]
        assert params["redirect_uri"] == ["http://localhost:3000/api/auth/callback"]
        assert params["scope"] == ["openid profile email"]
        assert params["code_challenge_method"] == ["S256"]

        assert "code_challenge" in params
        assert len(params["code_challenge"][0]) > 0

        assert params["state"] == [auth_state.nonce]

    def test_authorization_url_base(self, service: OidcClientService) -> None:
        """Test that the authorization URL uses the discovered authorization endpoint."""
        url, _ = service.generate_authorization_url("http://localhost:3000/")

        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        assert (
            base_url
            == "https://auth.example.com/realms/test/protocol/openid-connect/auth"
        )

    def test_auth_state_contains_pkce_verifier(
        self, service: OidcClientService
    ) -> None:
        """Test that the returned AuthState contains the PKCE code verifier."""
        _, auth_state = service.generate_authorization_url("http://localhost:3000/")

        assert isinstance(auth_state, AuthState)
        assert len(auth_state.code_verifier) > 0

    def test_auth_state_contains_redirect_url(
        self, service: OidcClientService
    ) -> None:
        """Test that the returned AuthState preserves the original redirect URL."""
        redirect = "http://localhost:3000/items/ABCD"
        _, auth_state = service.generate_authorization_url(redirect)

        assert auth_state.redirect_url == redirect

    def test_auth_state_nonce_is_random(self, service: OidcClientService) -> None:
        """Test that each authorization request produces a unique nonce."""
        _, state_1 = service.generate_authorization_url("http://localhost:3000/")
        _, state_2 = service.generate_authorization_url("http://localhost:3000/")

        assert state_1.nonce != state_2.nonce

    def test_pkce_challenge_matches_verifier(
        self, service: OidcClientService
    ) -> None:
        """Test that the code_challenge in the URL matches S256(code_verifier) from AuthState."""
        url, auth_state = service.generate_authorization_url("http://localhost:3000/")

        params = parse_qs(urlparse(url).query)
        url_challenge = params["code_challenge"][0]

        expected_challenge = service.generate_pkce_challenge(auth_state.code_verifier)

        assert url_challenge == expected_challenge


class TestExchangeCodeForTokens:
    """Tests for authorization code to token exchange."""

    @pytest.fixture
    def service(self, test_settings: Settings) -> OidcClientService:
        """Create an OidcClientService with OIDC enabled."""
        return _build_service(_oidc_settings(test_settings))

    def test_exchange_success(self, service: OidcClientService) -> None:
        """Test successful token exchange returns a valid TokenResponse."""
        token_data = {
            "access_token": "eyJ-access-token",
            "id_token": "eyJ-id-token",
            "refresh_token": "eyJ-refresh-token",
            "token_type": "Bearer",
            "expires_in": 300,
        }

        with patch("httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = token_data
            mock_response.raise_for_status = MagicMock()
            mock_post.return_value = mock_response

            result = service.exchange_code_for_tokens("auth-code-123", "pkce-verifier")

        assert isinstance(result, TokenResponse)
        assert result.access_token == "eyJ-access-token"
        assert result.id_token == "eyJ-id-token"
        assert result.refresh_token == "eyJ-refresh-token"
        assert result.token_type == "Bearer"
        assert result.expires_in == 300

    def test_exchange_sends_correct_request(self, service: OidcClientService) -> None:
        """Test that token exchange sends the correct form data and headers."""
        token_data = {
            "access_token": "eyJ-token",
            "token_type": "Bearer",
            "expires_in": 300,
        }

        with patch("httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = token_data
            mock_response.raise_for_status = MagicMock()
            mock_post.return_value = mock_response

            service.exchange_code_for_tokens("the-auth-code", "the-verifier")

        mock_post.assert_called_once()
        call_args = mock_post.call_args

        assert (
            call_args.args[0]
            == "https://auth.example.com/realms/test/protocol/openid-connect/token"
        )

        posted_data = call_args.kwargs["data"]
        assert posted_data["grant_type"] == "authorization_code"
        assert posted_data["code"] == "the-auth-code"
        assert posted_data["redirect_uri"] == "http://localhost:3000/api/auth/callback"
        assert posted_data["client_id"] == "test-backend"
        assert posted_data["client_secret"] == "test-client-secret"
        assert posted_data["code_verifier"] == "the-verifier"

        assert (
            call_args.kwargs["headers"]["Content-Type"]
            == "application/x-www-form-urlencoded"
        )

    def test_exchange_records_success_metric(self, service: OidcClientService) -> None:
        """Test that a successful exchange records the success metric."""
        from app.services.oidc_client_service import OIDC_TOKEN_EXCHANGE_TOTAL

        token_data = {
            "access_token": "eyJ-token",
            "token_type": "Bearer",
            "expires_in": 300,
        }

        before = OIDC_TOKEN_EXCHANGE_TOTAL.labels(status="success")._value.get()

        with patch("httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = token_data
            mock_response.raise_for_status = MagicMock()
            mock_post.return_value = mock_response

            service.exchange_code_for_tokens("code", "verifier")

        after = OIDC_TOKEN_EXCHANGE_TOTAL.labels(status="success")._value.get()
        assert after - before == 1.0

    def test_exchange_missing_access_token_raises(
        self, service: OidcClientService
    ) -> None:
        """Test that AuthenticationException is raised if response lacks access_token."""
        token_data = {
            "token_type": "Bearer",
            "expires_in": 300,
        }

        with patch("httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = token_data
            mock_response.raise_for_status = MagicMock()
            mock_post.return_value = mock_response

            with pytest.raises(AuthenticationException, match="missing access_token"):
                service.exchange_code_for_tokens("code", "verifier")

    def test_exchange_http_error_raises_authentication_exception(
        self, service: OidcClientService
    ) -> None:
        """Test that an HTTP error during exchange raises AuthenticationException."""
        with patch("httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 400
            mock_response.json.return_value = {
                "error": "invalid_grant",
                "error_description": "Code has expired",
            }

            error = httpx.HTTPStatusError(
                "Bad Request",
                request=MagicMock(),
                response=mock_response,
            )
            mock_post.side_effect = error

            with pytest.raises(
                AuthenticationException,
                match="Failed to exchange authorization code",
            ):
                service.exchange_code_for_tokens("expired-code", "verifier")

    def test_exchange_defaults_token_type_and_expires(
        self, service: OidcClientService
    ) -> None:
        """Test that token_type defaults to 'Bearer' and expires_in to 300."""
        token_data = {
            "access_token": "eyJ-token",
        }

        with patch("httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = token_data
            mock_response.raise_for_status = MagicMock()
            mock_post.return_value = mock_response

            result = service.exchange_code_for_tokens("code", "verifier")

        assert result.token_type == "Bearer"
        assert result.expires_in == 300

    def test_exchange_optional_fields_none_when_absent(
        self, service: OidcClientService
    ) -> None:
        """Test that id_token and refresh_token are None when not in the response."""
        token_data = {
            "access_token": "eyJ-token",
            "token_type": "Bearer",
            "expires_in": 600,
        }

        with patch("httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = token_data
            mock_response.raise_for_status = MagicMock()
            mock_post.return_value = mock_response

            result = service.exchange_code_for_tokens("code", "verifier")

        assert result.id_token is None
        assert result.refresh_token is None


class TestRefreshAccessToken:
    """Tests for access token refresh using a refresh token."""

    @pytest.fixture
    def service(self, test_settings: Settings) -> OidcClientService:
        """Create an OidcClientService with OIDC enabled."""
        return _build_service(_oidc_settings(test_settings))

    def test_refresh_success(self, service: OidcClientService) -> None:
        """Test successful token refresh returns a valid TokenResponse."""
        token_data = {
            "access_token": "eyJ-new-access-token",
            "id_token": "eyJ-new-id-token",
            "refresh_token": "eyJ-new-refresh-token",
            "token_type": "Bearer",
            "expires_in": 300,
        }

        with patch("httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = token_data
            mock_response.raise_for_status = MagicMock()
            mock_post.return_value = mock_response

            result = service.refresh_access_token("old-refresh-token")

        assert isinstance(result, TokenResponse)
        assert result.access_token == "eyJ-new-access-token"

    def test_refresh_sends_correct_request(self, service: OidcClientService) -> None:
        """Test that refresh sends the correct form data."""
        token_data = {
            "access_token": "eyJ-token",
            "token_type": "Bearer",
            "expires_in": 300,
        }

        with patch("httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = token_data
            mock_response.raise_for_status = MagicMock()
            mock_post.return_value = mock_response

            service.refresh_access_token("the-refresh-token")

        mock_post.assert_called_once()
        call_args = mock_post.call_args

        posted_data = call_args.kwargs["data"]
        assert posted_data["grant_type"] == "refresh_token"
        assert posted_data["refresh_token"] == "the-refresh-token"
        assert posted_data["client_id"] == "test-backend"
        assert posted_data["client_secret"] == "test-client-secret"

    def test_refresh_missing_access_token_raises(
        self, service: OidcClientService
    ) -> None:
        """Test that AuthenticationException is raised if refresh response lacks access_token."""
        token_data = {
            "token_type": "Bearer",
            "expires_in": 300,
        }

        with patch("httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = token_data
            mock_response.raise_for_status = MagicMock()
            mock_post.return_value = mock_response

            with pytest.raises(AuthenticationException, match="missing access_token"):
                service.refresh_access_token("refresh-token")

    def test_refresh_http_error_raises_authentication_exception(
        self, service: OidcClientService
    ) -> None:
        """Test that an HTTP error during refresh raises AuthenticationException."""
        with patch("httpx.post") as mock_post:
            mock_post.side_effect = httpx.ConnectError("Connection refused")

            with pytest.raises(
                AuthenticationException, match="Failed to refresh access token"
            ):
                service.refresh_access_token("refresh-token")

    def test_refresh_preserves_original_refresh_token_when_not_rotated(
        self, service: OidcClientService
    ) -> None:
        """Test that the original refresh token is kept when the provider does not rotate it."""
        token_data = {
            "access_token": "eyJ-new-access",
            "token_type": "Bearer",
            "expires_in": 300,
        }

        with patch("httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = token_data
            mock_response.raise_for_status = MagicMock()
            mock_post.return_value = mock_response

            result = service.refresh_access_token("original-refresh-token")

        assert result.refresh_token == "original-refresh-token"


class TestOidcDisabled:
    """Tests for service behavior when OIDC is disabled."""

    def test_endpoints_property_raises_when_oidc_disabled(
        self, test_settings: Settings
    ) -> None:
        """Test that accessing endpoints raises ValueError when OIDC is disabled."""
        service = _build_service(test_settings)

        with pytest.raises(ValueError, match="OIDC endpoints not available"):
            _ = service.endpoints

    def test_no_discovery_when_oidc_disabled(self, test_settings: Settings) -> None:
        """Test that no HTTP calls are made for discovery when OIDC is disabled."""
        with patch("httpx.get") as mock_get:
            OidcClientService(test_settings)

        mock_get.assert_not_called()

    def test_endpoints_internal_state_is_none_when_disabled(
        self, test_settings: Settings
    ) -> None:
        """Test that _endpoints is None when OIDC is disabled."""
        service = _build_service(test_settings)
        assert service._endpoints is None


class TestDataclasses:
    """Tests for the dataclass definitions used by OidcClientService."""

    def test_oidc_endpoints_dataclass(self) -> None:
        """Test OidcEndpoints dataclass construction."""
        endpoints = OidcEndpoints(
            authorization_endpoint="https://auth.example.com/auth",
            token_endpoint="https://auth.example.com/token",
            end_session_endpoint="https://auth.example.com/logout",
            jwks_uri="https://auth.example.com/certs",
        )
        assert endpoints.authorization_endpoint == "https://auth.example.com/auth"
        assert endpoints.token_endpoint == "https://auth.example.com/token"
        assert endpoints.end_session_endpoint == "https://auth.example.com/logout"
        assert endpoints.jwks_uri == "https://auth.example.com/certs"

    def test_auth_state_dataclass(self) -> None:
        """Test AuthState dataclass construction."""
        state = AuthState(
            code_verifier="verifier-abc",
            redirect_url="http://localhost:3000/",
            nonce="nonce-xyz",
        )
        assert state.code_verifier == "verifier-abc"
        assert state.redirect_url == "http://localhost:3000/"
        assert state.nonce == "nonce-xyz"

    def test_token_response_dataclass(self) -> None:
        """Test TokenResponse dataclass construction."""
        response = TokenResponse(
            access_token="at-123",
            id_token="id-456",
            refresh_token="rt-789",
            token_type="Bearer",
            expires_in=3600,
        )
        assert response.access_token == "at-123"
        assert response.id_token == "id-456"
        assert response.refresh_token == "rt-789"
        assert response.token_type == "Bearer"
        assert response.expires_in == 3600

    def test_token_response_optional_fields(self) -> None:
        """Test TokenResponse with optional fields set to None."""
        response = TokenResponse(
            access_token="at-123",
            id_token=None,
            refresh_token=None,
            token_type="Bearer",
            expires_in=300,
        )
        assert response.id_token is None
        assert response.refresh_token is None
