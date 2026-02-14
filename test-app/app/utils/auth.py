"""Authentication utilities for OIDC integration."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import jwt
from flask import g, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import Settings
from app.exceptions import (
    AuthenticationException,
    AuthorizationException,
    ValidationException,
)
from app.services.auth_service import AuthContext, AuthService
from app.services.oidc_client_service import AuthState, OidcClientService

logger = logging.getLogger(__name__)


@dataclass
class PendingTokenRefresh:
    """Tokens to be set on response after successful refresh."""

    access_token: str
    refresh_token: str | None
    access_token_expires_in: int


def get_token_expiry_seconds(token: str) -> int | None:
    """Extract remaining lifetime from a JWT token's exp claim.

    Decodes the token without signature verification (we just need the exp claim).

    Args:
        token: JWT token string

    Returns:
        Seconds until expiration, or None if token is not a JWT or has no exp claim
    """
    try:
        # Decode without verification - we only need the payload
        payload = jwt.decode(token, options={"verify_signature": False})
        exp = payload.get("exp")
        if exp is None:
            return None

        # time.time() is correct here: exp is an absolute Unix timestamp
        remaining = int(exp - time.time())
        return max(remaining, 0)  # Don't return negative

    except jwt.DecodeError:
        # Not a valid JWT (opaque token) - return None
        return None


def public(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to mark an endpoint as publicly accessible (no authentication required).

    Usage:
        @some_bp.route("/health")
        @public
        def health_check():
            return {"status": "healthy"}
    """
    func.is_public = True  # type: ignore[attr-defined]
    return func


def allow_roles(*roles: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to restrict endpoint access to specific roles.

    When OIDC is enabled, the default authorization policy is authenticated-only
    (any valid token passes). This decorator adds role enforcement: only users
    with at least one of the listed roles are allowed.

    Args:
        *roles: Role names that are allowed to access this endpoint

    Usage:
        @some_bp.route("/admin")
        @allow_roles("admin")
        def admin_endpoint():
            return {"status": "admin only"}
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        func.allowed_roles = set(roles)  # type: ignore[attr-defined]
        return func
    return decorator


def get_auth_context() -> AuthContext | None:
    """Get the current authentication context from flask.g.

    Returns:
        AuthContext if user is authenticated, None otherwise
    """
    return getattr(g, "auth_context", None)


def extract_token_from_request(config: Settings) -> str | None:
    """Extract JWT token from request cookie or Authorization header.

    Checks cookie first, then Authorization header with Bearer prefix.

    Args:
        config: Application settings for cookie name

    Returns:
        JWT token string or None if not found
    """
    # Check cookie first (takes precedence)
    token = request.cookies.get(config.oidc_cookie_name)
    if token:
        logger.debug("Token extracted from cookie")
        return token

    # Check Authorization header
    auth_header = request.headers.get("Authorization")
    if auth_header:
        parts = auth_header.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            logger.debug("Token extracted from Authorization header")
            return parts[1]

    return None


def check_authorization(auth_context: AuthContext, view_func: Callable[..., Any] | None = None) -> None:
    """Check if user has required authorization for the current request.

    Authorization rules (EI-specific, different from IoTSupport):
    - Default: authenticated-only (any valid token passes, no role check)
    - When @allow_roles is set: user must have at least one of the listed roles

    Args:
        auth_context: Authenticated user context
        view_func: The view function being called (to check for @allow_roles decorator)

    Raises:
        AuthorizationException: If user lacks required permissions
    """
    # Check for role restrictions from @allow_roles decorator
    if view_func is not None:
        allowed_roles: set[str] = getattr(view_func, "allowed_roles", set())
        if allowed_roles:
            # Role enforcement is active: user must have at least one allowed role
            for role in allowed_roles:
                if role in auth_context.roles:
                    logger.debug("User has '%s' role - access granted via @allow_roles", role)
                    return

            # User has no matching role
            raise AuthorizationException(
                f"Insufficient permissions - requires one of: {', '.join(sorted(allowed_roles))}"
            )

    # No @allow_roles set: authenticated-only (any valid token passes)
    logger.debug("No role restriction - authenticated user granted access")


def authenticate_request(
    auth_service: AuthService,
    config: Settings,
    oidc_client_service: OidcClientService | None = None,
    view_func: Callable[..., Any] | None = None,
) -> None:
    """Authenticate the current request and store auth context in flask.g.

    This function is called by the before_request hook for all /api requests.
    If the access token is expired but a refresh token is available, it will
    attempt to refresh the tokens and store them in g.pending_token_refresh
    for the after_request hook to set as cookies.

    Args:
        auth_service: AuthService instance for token validation
        config: Application settings
        oidc_client_service: OidcClientService for token refresh (optional)
        view_func: The view function being called (to check for @allow_roles decorator)

    Raises:
        AuthenticationException: If token is missing, invalid, or expired
        AuthorizationException: If user lacks required permissions
    """
    # Try access token first (from cookie or Authorization header)
    access_token = extract_token_from_request(config)
    token_expired = False

    if access_token:
        try:
            auth_context = auth_service.validate_token(access_token)
            g.auth_context = auth_context
            check_authorization(auth_context, view_func)
            logger.info(
                "Request authenticated: subject=%s email=%s roles=%s",
                auth_context.subject,
                auth_context.email,
                auth_context.roles,
            )
            return
        except AuthenticationException as e:
            # Token invalid/expired - check if it's an expiry issue
            error_msg = str(e).lower()
            if "expired" not in error_msg:
                # Not an expiry issue - re-raise immediately
                raise
            # Token expired - we can try refresh
            token_expired = True
            logger.debug("Access token expired, attempting refresh")

    # No valid access token - try refresh if we have the service and a refresh token
    refresh_token = request.cookies.get(config.oidc_refresh_cookie_name)

    if not refresh_token:
        # No refresh token available
        if token_expired:
            raise AuthenticationException("Token has expired")
        raise AuthenticationException("No valid token provided")

    if not oidc_client_service:
        # No OIDC client service available - can't refresh
        raise AuthenticationException("Session expired, please login again")

    # Attempt refresh
    try:
        new_tokens = oidc_client_service.refresh_access_token(refresh_token)
        logger.info("Successfully refreshed access token")
    except AuthenticationException as e:
        # Refresh failed - signal to clear cookies
        g.clear_auth_cookies = True
        raise AuthenticationException("Session expired, please login again") from e

    # Validate the new access token
    auth_context = auth_service.validate_token(new_tokens.access_token)
    g.auth_context = auth_context

    # Store tokens for after_request to set cookies
    g.pending_token_refresh = PendingTokenRefresh(
        access_token=new_tokens.access_token,
        refresh_token=new_tokens.refresh_token,
        access_token_expires_in=new_tokens.expires_in,
    )

    check_authorization(auth_context, view_func)

    logger.info(
        "Request authenticated (after refresh): subject=%s email=%s roles=%s",
        auth_context.subject,
        auth_context.email,
        auth_context.roles,
    )


def serialize_auth_state(auth_state: AuthState, secret_key: str) -> str:
    """Serialize and sign AuthState for storage in cookie.

    Args:
        auth_state: AuthState to serialize
        secret_key: Secret key for signing

    Returns:
        Signed serialized auth state string
    """
    serializer = URLSafeTimedSerializer(secret_key)
    data = {
        "code_verifier": auth_state.code_verifier,
        "redirect_url": auth_state.redirect_url,
        "nonce": auth_state.nonce,
    }
    return serializer.dumps(data)


def deserialize_auth_state(signed_data: str, secret_key: str, max_age: int = 600) -> AuthState:
    """Deserialize and verify AuthState from signed cookie.

    Args:
        signed_data: Signed serialized auth state
        secret_key: Secret key for verification
        max_age: Maximum age in seconds (default 10 minutes)

    Returns:
        AuthState instance

    Raises:
        ValidationException: If signature is invalid or data expired
    """
    serializer = URLSafeTimedSerializer(secret_key)
    try:
        data = serializer.loads(signed_data, max_age=max_age)
        return AuthState(
            code_verifier=data["code_verifier"],
            redirect_url=data["redirect_url"],
            nonce=data["nonce"],
        )
    except SignatureExpired as e:
        raise ValidationException("Authentication state expired") from e
    except BadSignature as e:
        raise ValidationException("Invalid authentication state") from e
    except (KeyError, TypeError) as e:
        raise ValidationException("Malformed authentication state") from e


def get_cookie_secure(config: Settings) -> bool:
    """Determine if cookies should use Secure flag.

    In the config system, oidc_cookie_secure is always resolved
    (either explicit or inferred from baseurl), so we just return it.

    Args:
        config: Application settings

    Returns:
        True if cookies should use Secure flag, False otherwise
    """
    return config.oidc_cookie_secure


def validate_redirect_url(redirect_url: str, base_url: str) -> None:
    """Validate redirect URL to prevent open redirect attacks.

    Only allows relative URLs or URLs matching the base URL origin.

    Args:
        redirect_url: URL to validate
        base_url: Base URL (BASEURL from config)

    Raises:
        ValidationException: If redirect URL is invalid or external
    """
    # Parse URLs
    redirect_parsed = urlparse(redirect_url)
    base_parsed = urlparse(base_url)

    # Allow relative URLs (no scheme or netloc)
    if not redirect_parsed.scheme and not redirect_parsed.netloc:
        return

    # Allow URLs with same origin as base URL
    if (
        redirect_parsed.scheme == base_parsed.scheme
        and redirect_parsed.netloc == base_parsed.netloc
    ):
        return

    # Reject external URLs
    raise ValidationException(
        "Invalid redirect URL - external redirects not allowed"
    )
