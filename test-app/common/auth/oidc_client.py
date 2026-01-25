"""OIDC client service for authorization code flow with PKCE."""

import base64
import hashlib
import logging
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from common.core.settings import CommonSettings

logger = logging.getLogger(__name__)


class OIDCClientError(Exception):
    """Base exception for OIDC client errors."""

    pass


@dataclass
class OIDCEndpoints:
    """OIDC provider endpoints discovered from well-known configuration."""

    authorization_endpoint: str
    token_endpoint: str
    end_session_endpoint: str | None
    jwks_uri: str


@dataclass
class AuthState:
    """State for OIDC authorization flow with PKCE."""

    code_verifier: str  # PKCE code verifier
    redirect_url: str  # Original URL to redirect after login
    nonce: str  # Random nonce for CSRF protection


@dataclass
class TokenResponse:
    """Token response from OIDC provider."""

    access_token: str
    id_token: str | None  # ID token for logout
    refresh_token: str | None
    token_type: str
    expires_in: int


class OIDCClient:
    """OIDC client for authorization code flow with PKCE.

    Handles the OAuth 2.0 authorization code flow:
    1. Generate authorization URL for login redirect
    2. Exchange authorization code for tokens
    3. Refresh tokens when expired

    Thread-safe and caches OIDC provider endpoints.
    """

    def __init__(
        self,
        settings: CommonSettings,
        callback_path: str = "/api/auth/callback",
    ) -> None:
        """Initialize OIDC client.

        Args:
            settings: Application settings with OIDC configuration
            callback_path: Path for the OAuth callback endpoint

        Raises:
            OIDCClientError: If OIDC is enabled but required config is missing
        """
        self._settings = settings
        self._callback_path = callback_path
        self._endpoints: OIDCEndpoints | None = None

        if settings.OIDC_ENABLED:
            if not settings.OIDC_ISSUER_URL:
                raise OIDCClientError(
                    "OIDC_ISSUER_URL is required when OIDC_ENABLED=True"
                )
            if not settings.OIDC_CLIENT_ID:
                raise OIDCClientError(
                    "OIDC_CLIENT_ID is required when OIDC_ENABLED=True"
                )
            if not settings.OIDC_CLIENT_SECRET:
                raise OIDCClientError(
                    "OIDC_CLIENT_SECRET is required when OIDC_ENABLED=True"
                )

            # Discover endpoints at initialization
            self._discover_endpoints()
            logger.info("OIDCClient initialized with OIDC enabled")
        else:
            logger.info("OIDCClient initialized with OIDC disabled")

    @property
    def enabled(self) -> bool:
        """Check if OIDC is enabled."""
        return self._settings.OIDC_ENABLED

    @property
    def endpoints(self) -> OIDCEndpoints:
        """Get discovered OIDC endpoints.

        Raises:
            OIDCClientError: If endpoints not discovered
        """
        if not self._endpoints:
            raise OIDCClientError(
                "OIDC endpoints not available. Ensure OIDC_ENABLED=True."
            )
        return self._endpoints

    def _discover_endpoints(self) -> None:
        """Discover OIDC endpoints from provider's well-known configuration."""
        discovery_url = (
            f"{self._settings.OIDC_ISSUER_URL}/.well-known/openid-configuration"
        )

        logger.info("Discovering OIDC endpoints from %s", discovery_url)

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = httpx.get(discovery_url, timeout=10.0)
                response.raise_for_status()
                discovery_doc = response.json()

                authorization_endpoint = discovery_doc.get("authorization_endpoint")
                token_endpoint = discovery_doc.get("token_endpoint")
                jwks_uri = discovery_doc.get("jwks_uri")

                if not authorization_endpoint or not token_endpoint or not jwks_uri:
                    raise OIDCClientError(
                        "OIDC discovery document missing required endpoints"
                    )

                self._endpoints = OIDCEndpoints(
                    authorization_endpoint=str(authorization_endpoint),
                    token_endpoint=str(token_endpoint),
                    end_session_endpoint=discovery_doc.get("end_session_endpoint"),
                    jwks_uri=str(jwks_uri),
                )

                logger.info(
                    "Discovered OIDC endpoints: auth=%s token=%s",
                    authorization_endpoint,
                    token_endpoint,
                )
                return

            except (httpx.HTTPError, OIDCClientError) as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        "OIDC discovery attempt %d/%d failed: %s. Retrying...",
                        attempt + 1,
                        max_retries,
                        str(e),
                    )
                else:
                    logger.error(
                        "OIDC discovery failed after %d attempts: %s",
                        max_retries,
                        str(e),
                    )
                    raise OIDCClientError(
                        f"Failed to discover OIDC endpoints: {e}"
                    ) from e

    def _generate_pkce_challenge(self, code_verifier: str) -> str:
        """Generate PKCE code challenge from verifier."""
        sha256_hash = hashlib.sha256(code_verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(sha256_hash).rstrip(b"=").decode("ascii")

    def generate_authorization_url(
        self,
        redirect_url: str,
        remember_me: bool = False,
    ) -> tuple[str, AuthState]:
        """Generate OIDC authorization URL with PKCE.

        Args:
            redirect_url: URL to redirect to after successful authentication
            remember_me: Request extended session (adds prompt parameter)

        Returns:
            Tuple of (authorization_url, auth_state)
        """
        # Generate PKCE code verifier (43-128 characters)
        code_verifier = secrets.token_urlsafe(32)
        code_challenge = self._generate_pkce_challenge(code_verifier)

        # Generate random nonce for CSRF protection
        nonce = secrets.token_urlsafe(32)

        auth_state = AuthState(
            code_verifier=code_verifier,
            redirect_url=redirect_url,
            nonce=nonce,
        )

        # Construct redirect URI
        base_url = getattr(self._settings, "BASEURL", "http://localhost:5000")
        redirect_uri = f"{base_url}{self._callback_path}"

        # Build authorization URL parameters
        params: dict[str, str] = {
            "client_id": self._settings.OIDC_CLIENT_ID or "",
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": self._settings.OIDC_SCOPES,
            "state": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        authorization_url = f"{self.endpoints.authorization_endpoint}?{urlencode(params)}"

        logger.info(
            "Generated authorization URL for redirect=%s nonce=%s",
            redirect_url,
            nonce[:8] + "...",
        )

        return authorization_url, auth_state

    def exchange_code_for_tokens(
        self,
        code: str,
        code_verifier: str,
    ) -> TokenResponse:
        """Exchange authorization code for tokens.

        Args:
            code: Authorization code from callback
            code_verifier: PKCE code verifier from auth state

        Returns:
            TokenResponse with access token and optional refresh token

        Raises:
            OIDCClientError: If token exchange fails
        """
        base_url = getattr(self._settings, "BASEURL", "http://localhost:5000")
        redirect_uri = f"{base_url}{self._callback_path}"

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self._settings.OIDC_CLIENT_ID,
            "client_secret": self._settings.OIDC_CLIENT_SECRET,
            "code_verifier": code_verifier,
        }

        try:
            response = httpx.post(
                self.endpoints.token_endpoint,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10.0,
            )
            response.raise_for_status()

            token_data = response.json()

            access_token = token_data.get("access_token")
            if not access_token:
                raise OIDCClientError("Token response missing access_token")

            logger.info("Successfully exchanged authorization code for tokens")

            return TokenResponse(
                access_token=str(access_token),
                id_token=token_data.get("id_token"),
                refresh_token=token_data.get("refresh_token"),
                token_type=token_data.get("token_type", "Bearer"),
                expires_in=int(token_data.get("expires_in", 300)),
            )

        except httpx.HTTPError as e:
            error_detail = str(e)
            try:
                if hasattr(e, "response") and e.response is not None:
                    error_data = e.response.json()
                    error_detail = error_data.get(
                        "error_description", error_data.get("error", str(e))
                    )
            except Exception:
                pass

            logger.error("Token exchange failed: %s", error_detail)
            raise OIDCClientError(f"Failed to exchange code: {error_detail}") from e

    def refresh_access_token(self, refresh_token: str) -> TokenResponse:
        """Refresh access token using refresh token.

        Args:
            refresh_token: Refresh token from previous token exchange

        Returns:
            TokenResponse with new access token

        Raises:
            OIDCClientError: If token refresh fails
        """
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._settings.OIDC_CLIENT_ID,
            "client_secret": self._settings.OIDC_CLIENT_SECRET,
        }

        try:
            response = httpx.post(
                self.endpoints.token_endpoint,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10.0,
            )
            response.raise_for_status()

            token_data = response.json()

            access_token = token_data.get("access_token")
            if not access_token:
                raise OIDCClientError("Token response missing access_token")

            logger.info("Successfully refreshed access token")

            return TokenResponse(
                access_token=str(access_token),
                id_token=token_data.get("id_token"),
                refresh_token=token_data.get("refresh_token", refresh_token),
                token_type=token_data.get("token_type", "Bearer"),
                expires_in=int(token_data.get("expires_in", 300)),
            )

        except httpx.HTTPError as e:
            logger.error("Token refresh failed: %s", str(e))
            raise OIDCClientError(f"Failed to refresh token: {e}") from e

    def get_logout_url(self, post_logout_redirect: str, id_token: str | None = None) -> str | None:
        """Get the OIDC logout URL.

        Args:
            post_logout_redirect: URL to redirect to after logout
            id_token: Optional ID token for logout hint

        Returns:
            Logout URL, or None if end_session_endpoint not available
        """
        if not self._endpoints or not self._endpoints.end_session_endpoint:
            return None

        params: dict[str, str] = {
            "client_id": self._settings.OIDC_CLIENT_ID or "",
            "post_logout_redirect_uri": post_logout_redirect,
        }

        if id_token:
            params["id_token_hint"] = id_token

        return f"{self._endpoints.end_session_endpoint}?{urlencode(params)}"
