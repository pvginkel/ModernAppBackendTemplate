"""OIDC authentication module."""

from common.auth.decorators import public
from common.auth.oidc import OIDCAuthenticator, OIDCUser
from common.auth.oidc_client import OIDCClient, AuthState, TokenResponse
from common.auth.routes import register_oidc_routes, AuthCheckResponse, UserInfoResponse

__all__ = [
    "public",
    "OIDCAuthenticator",
    "OIDCUser",
    "OIDCClient",
    "AuthState",
    "TokenResponse",
    "register_oidc_routes",
    "AuthCheckResponse",
    "UserInfoResponse",
]
