"""JWT validation service with JWKS discovery and caching."""

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient
from prometheus_client import Counter, Histogram

from app.config import Settings
from app.exceptions import AuthenticationException

# Auth metrics
AUTH_VALIDATION_TOTAL = Counter(
    "auth_validation_total",
    "Total auth token validations by status",
    ["status"],
)
AUTH_VALIDATION_DURATION_SECONDS = Histogram(
    "auth_validation_duration_seconds",
    "Auth token validation duration in seconds",
)
JWKS_REFRESH_TOTAL = Counter(
    "jwks_refresh_total",
    "Total JWKS initialization/refresh events",
    ["trigger", "status"],
)

logger = logging.getLogger(__name__)


@dataclass
class AuthContext:
    """Authentication context extracted from validated JWT token."""

    subject: str  # JWT "sub" claim
    email: str | None  # JWT "email" claim
    name: str | None  # JWT "name" claim
    roles: set[str]  # Combined roles from realm_access and resource_access


class AuthService:
    """Service for JWT validation with JWKS discovery and caching.

    This is a singleton service that caches JWKS keys with a 5-minute TTL.
    Thread-safe for concurrent token validation.
    """

    def __init__(
        self,
        config: Settings,
    ) -> None:
        """Initialize auth service with OIDC configuration.

        Args:
            config: Application settings containing OIDC configuration

        Raises:
            ValueError: If OIDC is enabled but required config is missing
        """
        self.config = config

        # JWKS client instance (initialized once if OIDC enabled)
        self._jwks_client: PyJWKClient | None = None
        self._jwks_uri: str | None = None

        # Initialize JWKS client if OIDC is enabled
        if config.oidc_enabled:
            if not config.oidc_issuer_url:
                raise ValueError("OIDC_ISSUER_URL is required when OIDC_ENABLED=True")
            if not config.oidc_client_id:
                raise ValueError("OIDC_CLIENT_ID is required when OIDC_ENABLED=True")

            logger.info("Initializing AuthService with OIDC enabled")

            # Discover JWKS URI once at startup
            self._jwks_uri = self._discover_jwks_uri()

            # Initialize JWKS client with caching
            try:
                self._jwks_client = PyJWKClient(
                    self._jwks_uri,
                    cache_keys=True,
                    lifespan=300,  # 5 minutes in seconds
                )
                logger.info("Initialized JWKS client with URI: %s", self._jwks_uri)

                # Record successful JWKS initialization
                JWKS_REFRESH_TOTAL.labels(trigger="startup", status="success").inc()
            except Exception as e:
                logger.error("Failed to initialize JWKS client: %s", str(e))
                JWKS_REFRESH_TOTAL.labels(trigger="startup", status="failed").inc()
                raise
        else:
            logger.info("AuthService initialized with OIDC disabled")

    def _discover_jwks_uri(self) -> str:
        """Discover JWKS URI from OIDC provider's discovery endpoint.

        Returns:
            JWKS URI string

        Raises:
            AuthenticationException: If discovery fails or JWKS URI not found
        """
        discovery_url = f"{self.config.oidc_issuer_url}/.well-known/openid-configuration"

        try:
            response = httpx.get(discovery_url, timeout=10.0)
            response.raise_for_status()
            discovery_doc = response.json()

            jwks_uri = discovery_doc.get("jwks_uri")
            if not jwks_uri:
                raise AuthenticationException(
                    "JWKS URI not found in OIDC discovery document"
                )

            logger.debug("Discovered JWKS URI: %s", jwks_uri)
            return str(jwks_uri)

        except httpx.HTTPError as e:
            logger.error("Failed to fetch OIDC discovery document: %s", str(e))
            raise AuthenticationException(
                f"Failed to discover JWKS endpoint: {str(e)}"
            ) from e

    def validate_token(self, token: str) -> AuthContext:
        """Validate JWT token and extract authentication context.

        Validates token signature, expiration, issuer, and audience.
        Extracts user information and roles from token claims.

        Args:
            token: JWT token string

        Returns:
            AuthContext with user information and roles

        Raises:
            AuthenticationException: If token is invalid, expired, or malformed
        """
        start_time = time.perf_counter()

        try:
            # Ensure JWKS client is initialized
            if not self._jwks_client:
                raise AuthenticationException("OIDC not enabled")

            # Get signing key from JWKS
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)

            # Use resolved audience (already includes client_id fallback from Settings.load())
            expected_audience = self.config.oidc_audience

            # Validate and decode token
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "RS384", "RS512"],
                issuer=self.config.oidc_issuer_url,
                audience=expected_audience,
                leeway=self.config.oidc_clock_skew_seconds,
            )

            # Extract user information
            subject = payload.get("sub")
            if not subject:
                raise AuthenticationException("Token missing 'sub' claim")

            email = payload.get("email")
            name = payload.get("name")

            # Extract roles from token claims
            roles = self._extract_roles(payload, expected_audience)

            # Record successful validation
            duration = time.perf_counter() - start_time
            AUTH_VALIDATION_TOTAL.labels(status="success").inc()
            AUTH_VALIDATION_DURATION_SECONDS.observe(max(duration, 0.0))

            logger.info(
                "Token validated successfully for subject=%s email=%s roles=%s",
                subject,
                email,
                roles,
            )

            return AuthContext(
                subject=subject,
                email=email,
                name=name,
                roles=roles,
            )

        except jwt.ExpiredSignatureError as e:
            duration = time.perf_counter() - start_time
            AUTH_VALIDATION_TOTAL.labels(status="expired").inc()
            AUTH_VALIDATION_DURATION_SECONDS.observe(max(duration, 0.0))
            logger.warning("Token validation failed: expired")
            raise AuthenticationException("Token has expired") from e

        except jwt.InvalidSignatureError as e:
            duration = time.perf_counter() - start_time
            AUTH_VALIDATION_TOTAL.labels(status="invalid_signature").inc()
            AUTH_VALIDATION_DURATION_SECONDS.observe(max(duration, 0.0))
            logger.warning("Token validation failed: invalid signature")
            raise AuthenticationException("Invalid token signature") from e

        except (jwt.InvalidIssuerError, jwt.InvalidAudienceError) as e:
            duration = time.perf_counter() - start_time
            AUTH_VALIDATION_TOTAL.labels(status="invalid_claims").inc()
            AUTH_VALIDATION_DURATION_SECONDS.observe(max(duration, 0.0))
            logger.warning("Token validation failed: invalid issuer or audience")
            raise AuthenticationException(
                "Token issuer or audience does not match expected values"
            ) from e

        except jwt.PyJWTError as e:
            duration = time.perf_counter() - start_time
            AUTH_VALIDATION_TOTAL.labels(status="invalid_token").inc()
            AUTH_VALIDATION_DURATION_SECONDS.observe(max(duration, 0.0))
            logger.warning("Token validation failed: %s", str(e))
            raise AuthenticationException(f"Invalid token: {str(e)}") from e

        except AuthenticationException:
            # Re-raise authentication exceptions as-is
            duration = time.perf_counter() - start_time
            AUTH_VALIDATION_TOTAL.labels(status="error").inc()
            AUTH_VALIDATION_DURATION_SECONDS.observe(max(duration, 0.0))
            raise

        except Exception as e:
            duration = time.perf_counter() - start_time
            AUTH_VALIDATION_TOTAL.labels(status="error").inc()
            AUTH_VALIDATION_DURATION_SECONDS.observe(max(duration, 0.0))
            logger.error("Unexpected error during token validation: %s", str(e))
            raise AuthenticationException(
                f"Token validation failed: {str(e)}"
            ) from e

    def _extract_roles(self, payload: dict[str, Any], audience: str | None) -> set[str]:
        """Extract roles from JWT claims.

        Combines roles from realm_access.roles and resource_access.<audience>.roles.

        Args:
            payload: Decoded JWT payload
            audience: Expected audience (client ID)

        Returns:
            Set of role names
        """
        roles: set[str] = set()

        # Extract realm-level roles from realm_access.roles
        realm_access = payload.get("realm_access", {})
        if isinstance(realm_access, dict):
            realm_roles = realm_access.get("roles", [])
            if isinstance(realm_roles, list):
                roles.update(str(role) for role in realm_roles)

        # Extract resource-level roles from resource_access.<audience>.roles
        if audience:
            resource_access = payload.get("resource_access", {})
            if isinstance(resource_access, dict):
                client_access = resource_access.get(audience, {})
                if isinstance(client_access, dict):
                    client_roles = client_access.get("roles", [])
                    if isinstance(client_roles, list):
                        roles.update(str(role) for role in client_roles)

        logger.debug("Extracted roles: %s", roles)
        return roles
