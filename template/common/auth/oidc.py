"""OIDC authentication with JWT validation."""

import logging
import threading
import time
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, TypeVar

import httpx
import jwt
from flask import Response, g, request
from jwt import PyJWKClient, PyJWKClientError

from common.core.settings import CommonSettings

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


class OIDCError(Exception):
    """Base exception for OIDC errors."""

    pass


class OIDCConfigurationError(OIDCError):
    """Raised when OIDC configuration is invalid or missing."""

    pass


class OIDCTokenError(OIDCError):
    """Raised when token validation fails."""

    pass


@dataclass
class OIDCUser:
    """Authenticated user information from JWT token."""

    sub: str  # Subject (unique user identifier)
    email: str | None = None
    name: str | None = None
    preferred_username: str | None = None
    groups: list[str] | None = None
    roles: list[str] | None = None
    raw_claims: dict[str, Any] | None = None

    @classmethod
    def from_claims(cls, claims: dict[str, Any]) -> "OIDCUser":
        """Create OIDCUser from JWT claims."""
        # Extract groups/roles from various claim locations (provider-specific)
        groups = claims.get("groups", [])
        roles = claims.get("roles", [])

        # Keycloak puts roles in realm_access
        if "realm_access" in claims:
            roles = claims["realm_access"].get("roles", roles)

        return cls(
            sub=claims["sub"],
            email=claims.get("email"),
            name=claims.get("name"),
            preferred_username=claims.get("preferred_username"),
            groups=groups if groups else None,
            roles=roles if roles else None,
            raw_claims=claims,
        )


class OIDCProvider:
    """OIDC Provider with discovery and JWKS caching.

    Thread-safe implementation that caches OIDC configuration and JWKS.
    """

    def __init__(
        self,
        issuer_url: str,
        client_id: str,
        audience: str | None = None,
        clock_skew: int = 30,
        cache_ttl: int = 300,
    ) -> None:
        """Initialize OIDC provider.

        Args:
            issuer_url: OIDC issuer URL (e.g., https://auth.example.com/realms/app)
            client_id: OIDC client ID for audience validation
            audience: Expected 'aud' claim (defaults to client_id)
            clock_skew: Seconds of clock skew tolerance
            cache_ttl: Seconds to cache OIDC configuration
        """
        self._issuer_url = issuer_url.rstrip("/")
        self._client_id = client_id
        self._audience = audience or client_id
        self._clock_skew = clock_skew
        self._cache_ttl = cache_ttl

        self._config: dict[str, Any] | None = None
        self._config_fetched_at: float = 0
        self._jwks_client: PyJWKClient | None = None
        self._lock = threading.Lock()

    @property
    def discovery_url(self) -> str:
        """Get the OIDC discovery URL."""
        return f"{self._issuer_url}/.well-known/openid-configuration"

    def _fetch_config(self) -> dict[str, Any]:
        """Fetch OIDC configuration from discovery endpoint."""
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(self.discovery_url)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as e:
            raise OIDCConfigurationError(
                f"Failed to fetch OIDC configuration: {e}"
            ) from e

    def _get_config(self) -> dict[str, Any]:
        """Get OIDC configuration with caching."""
        now = time.time()
        if self._config is None or (now - self._config_fetched_at) > self._cache_ttl:
            with self._lock:
                # Double-check after acquiring lock
                if self._config is None or (now - self._config_fetched_at) > self._cache_ttl:
                    self._config = self._fetch_config()
                    self._config_fetched_at = now
                    # Reset JWKS client when config changes
                    self._jwks_client = None
        return self._config

    def _get_jwks_client(self) -> PyJWKClient:
        """Get JWKS client with lazy initialization."""
        if self._jwks_client is None:
            with self._lock:
                if self._jwks_client is None:
                    config = self._get_config()
                    jwks_uri = config.get("jwks_uri")
                    if not jwks_uri:
                        raise OIDCConfigurationError("JWKS URI not found in OIDC config")
                    self._jwks_client = PyJWKClient(jwks_uri, cache_keys=True)
        return self._jwks_client

    def validate_token(self, token: str) -> dict[str, Any]:
        """Validate a JWT token and return claims.

        Args:
            token: JWT access token string

        Returns:
            Decoded token claims

        Raises:
            OIDCTokenError: If token validation fails
        """
        config = self._get_config()
        jwks_client = self._get_jwks_client()

        try:
            signing_key = jwks_client.get_signing_key_from_jwt(token)
        except PyJWKClientError as e:
            raise OIDCTokenError(f"Failed to get signing key: {e}") from e

        # Get supported algorithms from config, default to RS256
        algorithms = config.get("id_token_signing_alg_values_supported", ["RS256"])

        try:
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=algorithms,
                issuer=self._issuer_url,
                audience=self._audience,
                leeway=self._clock_skew,
                options={
                    "require": ["exp", "iat", "sub"],
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
            return claims
        except jwt.ExpiredSignatureError as e:
            raise OIDCTokenError("Token has expired") from e
        except jwt.InvalidAudienceError as e:
            raise OIDCTokenError("Invalid token audience") from e
        except jwt.InvalidIssuerError as e:
            raise OIDCTokenError("Invalid token issuer") from e
        except jwt.InvalidTokenError as e:
            raise OIDCTokenError(f"Invalid token: {e}") from e


class OIDCAuthenticator:
    """OIDC Authenticator for Flask applications.

    Provides middleware and decorators for protecting routes with OIDC authentication.
    """

    def __init__(self, settings: CommonSettings) -> None:
        """Initialize authenticator with settings.

        Args:
            settings: Application settings with OIDC configuration
        """
        self._settings = settings
        self._provider: OIDCProvider | None = None

        if settings.OIDC_ENABLED:
            if not settings.OIDC_ISSUER_URL or not settings.OIDC_CLIENT_ID:
                raise OIDCConfigurationError(
                    "OIDC_ISSUER_URL and OIDC_CLIENT_ID are required when OIDC is enabled"
                )
            self._provider = OIDCProvider(
                issuer_url=settings.OIDC_ISSUER_URL,
                client_id=settings.OIDC_CLIENT_ID,
                audience=settings.OIDC_AUDIENCE,
                clock_skew=settings.OIDC_CLOCK_SKEW_SECONDS,
            )

    @property
    def enabled(self) -> bool:
        """Check if OIDC authentication is enabled."""
        return self._settings.OIDC_ENABLED and self._provider is not None

    def _extract_token(self) -> str | None:
        """Extract JWT token from request.

        Checks Authorization header first, then cookie.
        """
        # Check Authorization header (Bearer token)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:]

        # Check cookie
        return request.cookies.get(self._settings.OIDC_COOKIE_NAME)

    def authenticate(self) -> OIDCUser | None:
        """Authenticate the current request.

        Returns:
            OIDCUser if authentication succeeds, None otherwise.
        """
        if not self.enabled:
            return None

        token = self._extract_token()
        if not token:
            return None

        try:
            claims = self._provider.validate_token(token)  # type: ignore
            return OIDCUser.from_claims(claims)
        except OIDCTokenError as e:
            logger.debug("Token validation failed: %s", e)
            return None

    def require_auth(self, f: F) -> F:
        """Decorator to require authentication on a route.

        Usage:
            @app.route('/protected')
            @oidc.require_auth
            def protected_route():
                user = g.oidc_user
                return f"Hello {user.email}"
        """

        @wraps(f)
        def decorated(*args: Any, **kwargs: Any) -> Any:
            if not self.enabled:
                # OIDC disabled, allow access but no user
                g.oidc_user = None
                return f(*args, **kwargs)

            user = self.authenticate()
            if user is None:
                return Response(
                    '{"error": "Authentication required"}',
                    status=401,
                    mimetype="application/json",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            g.oidc_user = user
            return f(*args, **kwargs)

        return decorated  # type: ignore

    def require_roles(self, *required_roles: str) -> Callable[[F], F]:
        """Decorator to require specific roles.

        Usage:
            @app.route('/admin')
            @oidc.require_roles('admin', 'superuser')
            def admin_route():
                return "Admin area"
        """

        def decorator(f: F) -> F:
            @wraps(f)
            def decorated(*args: Any, **kwargs: Any) -> Any:
                if not self.enabled:
                    g.oidc_user = None
                    return f(*args, **kwargs)

                user = self.authenticate()
                if user is None:
                    return Response(
                        '{"error": "Authentication required"}',
                        status=401,
                        mimetype="application/json",
                        headers={"WWW-Authenticate": "Bearer"},
                    )

                user_roles = set(user.roles or [])
                if not user_roles.intersection(required_roles):
                    return Response(
                        '{"error": "Insufficient permissions"}',
                        status=403,
                        mimetype="application/json",
                    )

                g.oidc_user = user
                return f(*args, **kwargs)

            return decorated  # type: ignore

        return decorator

    def require_groups(self, *required_groups: str) -> Callable[[F], F]:
        """Decorator to require membership in specific groups.

        Usage:
            @app.route('/team')
            @oidc.require_groups('engineering', 'devops')
            def team_route():
                return "Team area"
        """

        def decorator(f: F) -> F:
            @wraps(f)
            def decorated(*args: Any, **kwargs: Any) -> Any:
                if not self.enabled:
                    g.oidc_user = None
                    return f(*args, **kwargs)

                user = self.authenticate()
                if user is None:
                    return Response(
                        '{"error": "Authentication required"}',
                        status=401,
                        mimetype="application/json",
                        headers={"WWW-Authenticate": "Bearer"},
                    )

                user_groups = set(user.groups or [])
                if not user_groups.intersection(required_groups):
                    return Response(
                        '{"error": "Insufficient permissions"}',
                        status=403,
                        mimetype="application/json",
                    )

                g.oidc_user = user
                return f(*args, **kwargs)

            return decorated  # type: ignore

        return decorator

    def optional_auth(self, f: F) -> F:
        """Decorator for optional authentication.

        Sets g.oidc_user to the user if authenticated, None otherwise.
        Does not reject unauthenticated requests.

        Usage:
            @app.route('/public')
            @oidc.optional_auth
            def public_route():
                if g.oidc_user:
                    return f"Hello {g.oidc_user.email}"
                return "Hello guest"
        """

        @wraps(f)
        def decorated(*args: Any, **kwargs: Any) -> Any:
            g.oidc_user = self.authenticate() if self.enabled else None
            return f(*args, **kwargs)

        return decorated  # type: ignore
