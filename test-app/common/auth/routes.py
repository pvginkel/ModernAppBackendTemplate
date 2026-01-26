"""OIDC authentication routes for Flask applications."""

from __future__ import annotations

import base64
import hmac
import json
import logging
from typing import Any

from flask import Blueprint, Response, current_app, make_response, redirect, request
from pydantic import BaseModel, Field

from common.auth.decorators import public
from common.auth.oidc_client import AuthState

logger = logging.getLogger(__name__)


class UserInfoResponse(BaseModel):
    """Response for current user info."""

    subject: str = Field(description="User subject from JWT")
    email: str | None = Field(default=None, description="User email")
    name: str | None = Field(default=None, description="User display name")
    authenticated: bool = Field(default=True)


class AuthCheckResponse(BaseModel):
    """Response for auth check endpoint."""

    authenticated: bool = Field(description="Whether the user is authenticated")
    disabled: bool = Field(
        default=False,
        description="Whether authentication is disabled (always authenticated)",
    )


def _get_settings():
    """Get settings from container."""
    return current_app.container.settings()


def _get_authenticator():
    """Get OIDC authenticator from container."""
    return current_app.container.oidc_authenticator()


def _get_oidc_client():
    """Get OIDC client from container."""
    return current_app.container.oidc_client()


def _serialize_auth_state(state: AuthState, secret_key: str) -> str:
    """Serialize auth state to signed cookie value."""
    data = json.dumps({
        "code_verifier": state.code_verifier,
        "redirect_url": state.redirect_url,
        "nonce": state.nonce,
    })
    signature = hmac.new(secret_key.encode(), data.encode(), "sha256").hexdigest()
    return base64.urlsafe_b64encode(f"{data}|{signature}".encode()).decode()


def _deserialize_auth_state(value: str, secret_key: str) -> AuthState:
    """Deserialize and verify auth state from cookie."""
    decoded = base64.urlsafe_b64decode(value.encode()).decode()
    data, signature = decoded.rsplit("|", 1)
    expected_sig = hmac.new(secret_key.encode(), data.encode(), "sha256").hexdigest()
    if not hmac.compare_digest(signature, expected_sig):
        raise ValueError("Invalid auth state signature")
    parsed = json.loads(data)
    return AuthState(
        code_verifier=parsed["code_verifier"],
        redirect_url=parsed["redirect_url"],
        nonce=parsed["nonce"],
    )


def register_oidc_routes(bp: Blueprint) -> None:
    """Register OIDC authentication routes on the blueprint.

    Routes registered:
    - GET /auth/login - Initiate OIDC login flow
    - GET /auth/callback - Handle OIDC callback
    - GET /auth/logout - Log out and clear cookies
    - GET /auth/check - Check authentication status
    - GET /auth/self - Get current user info

    Also registers a before_request hook that requires authentication for all
    endpoints not marked with the @public decorator.

    Args:
        bp: The blueprint to register routes on

    Requires the Flask app to have a container with:
    - settings() - CommonSettings instance
    - oidc_authenticator() - OIDCAuthenticator instance
    - oidc_client() - OIDCClient instance
    """
    from http import HTTPStatus
    from flask import jsonify

    @bp.before_request
    def _require_authentication():
        """Check OIDC authentication for protected endpoints."""
        settings = _get_settings()

        # Skip auth check for public endpoints (marked with @public decorator)
        if request.endpoint:
            view_func = current_app.view_functions.get(request.endpoint)
            if view_func and getattr(view_func, "is_public", False):
                return None

        # If OIDC disabled, allow all requests
        if not settings.OIDC_ENABLED:
            return None

        # Check authentication
        authenticator = _get_authenticator()
        user = authenticator.authenticate()
        if not user:
            return jsonify({"error": "Authentication required"}), HTTPStatus.UNAUTHORIZED

        return None

    @bp.route("/auth/self", methods=["GET"])
    @public
    def get_current_user() -> tuple[Any, int]:
        """Get current authenticated user info."""
        settings = _get_settings()

        if not settings.OIDC_ENABLED:
            return UserInfoResponse(
                subject="local-user",
                email="admin@local",
                name="Local Admin",
            ).model_dump(), 200

        authenticator = _get_authenticator()
        user = authenticator.authenticate()
        if not user:
            return {"error": "Not authenticated", "authenticated": False}, 401

        return UserInfoResponse(
            subject=user.sub,
            email=user.email,
            name=user.name,
        ).model_dump(), 200

    @bp.route("/auth/login", methods=["GET"])
    @public
    def login() -> Response:
        """Initiate OIDC login flow."""
        settings = _get_settings()

        if not settings.OIDC_ENABLED:
            return redirect("/")

        redirect_url = request.args.get("redirect", "/")
        oidc_client = _get_oidc_client()

        # Generate authorization URL with PKCE
        auth_url, auth_state = oidc_client.generate_authorization_url(redirect_url)

        response = make_response(redirect(auth_url))

        # Store state in signed cookie
        signed_state = _serialize_auth_state(auth_state, settings.SECRET_KEY)
        response.set_cookie(
            "auth_state",
            signed_state,
            httponly=True,
            secure=settings.BASEURL.startswith("https"),
            samesite=settings.OIDC_COOKIE_SAMESITE,
            max_age=600,
            path="/",
        )

        return response

    @bp.route("/auth/callback", methods=["GET"])
    @public
    def callback() -> Response:
        """Handle OIDC callback."""
        settings = _get_settings()
        oidc_client = _get_oidc_client()

        code = request.args.get("code")
        state = request.args.get("state")

        if not code or not state:
            return make_response("Missing code or state", 400)

        # Verify state
        signed_state = request.cookies.get("auth_state")
        if not signed_state:
            return make_response("Missing auth state cookie", 400)

        try:
            auth_state = _deserialize_auth_state(signed_state, settings.SECRET_KEY)
        except ValueError as e:
            logger.warning("Invalid auth state: %s", e)
            return make_response("Invalid auth state", 400)

        # Verify nonce matches
        if state != auth_state.nonce:
            return make_response("State mismatch", 400)

        # Exchange code for tokens
        tokens = oidc_client.exchange_code_for_tokens(code, auth_state.code_verifier)

        response = make_response(redirect(auth_state.redirect_url))

        # Cookie settings
        secure = settings.BASEURL.startswith("https")
        samesite = settings.OIDC_COOKIE_SAMESITE

        # Set access token cookie
        response.set_cookie(
            settings.OIDC_COOKIE_NAME,
            tokens.access_token,
            httponly=True,
            secure=secure,
            samesite=samesite,
            max_age=tokens.expires_in,
            path="/",
        )

        # Set refresh token (180 days for Remember Me)
        if tokens.refresh_token:
            response.set_cookie(
                "refresh_token",
                tokens.refresh_token,
                httponly=True,
                secure=secure,
                samesite=samesite,
                max_age=15552000,  # 180 days
                path="/",
            )

        # Set ID token for logout
        if tokens.id_token:
            response.set_cookie(
                "id_token",
                tokens.id_token,
                httponly=True,
                secure=secure,
                samesite=samesite,
                max_age=tokens.expires_in,
                path="/",
            )

        # Clear auth state cookie
        response.delete_cookie("auth_state", path="/")

        return response

    @bp.route("/auth/logout", methods=["GET"])
    @public
    def logout() -> Response:
        """Log out and clear cookies."""
        settings = _get_settings()
        redirect_url = request.args.get("redirect", "/")

        # Build absolute post-logout redirect
        if redirect_url.startswith("/"):
            post_logout_uri = f"{settings.BASEURL}{redirect_url}"
        else:
            post_logout_uri = redirect_url

        # Try to get OIDC logout URL
        oidc_logout_url = None
        if settings.OIDC_ENABLED:
            oidc_client = _get_oidc_client()
            id_token = request.cookies.get("id_token")
            oidc_logout_url = oidc_client.get_logout_url(post_logout_uri, id_token)

        final_redirect = oidc_logout_url or redirect_url
        response = make_response(redirect(final_redirect))

        # Clear all auth cookies
        secure = settings.BASEURL.startswith("https")
        samesite = settings.OIDC_COOKIE_SAMESITE
        for cookie in [settings.OIDC_COOKIE_NAME, "refresh_token", "id_token"]:
            response.set_cookie(
                cookie, "", max_age=0, httponly=True,
                secure=secure, samesite=samesite, path="/"
            )

        return response

    @bp.route("/auth/check", methods=["GET"])
    @public
    def auth_check() -> tuple[Any, int]:
        """Check if user is authenticated (for frontend)."""
        settings = _get_settings()

        if not settings.OIDC_ENABLED:
            return AuthCheckResponse(authenticated=True, disabled=True).model_dump(), 200

        authenticator = _get_authenticator()
        user = authenticator.authenticate()
        if user:
            return AuthCheckResponse(authenticated=True, disabled=False).model_dump(), 200
        return AuthCheckResponse(authenticated=False, disabled=False).model_dump(), 200
