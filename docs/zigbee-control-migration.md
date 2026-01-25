# ZigbeeControl Migration Plan

This document contains the exact changes needed to migrate ZigbeeControl to use the backend template with OIDC authentication.

## Step 1: Copy Common Modules

Run from `/work/ZigbeeControl/backend`:

```bash
# Create common directory structure
mkdir -p common/core common/auth common/health common/metrics

# Copy from template (adjust path as needed)
cp -r /work/backend/test-app/common/core/*.py common/core/
cp -r /work/backend/test-app/common/auth/*.py common/auth/
cp -r /work/backend/test-app/common/health/*.py common/health/
cp -r /work/backend/test-app/common/metrics/*.py common/metrics/

# Create __init__.py files
touch common/__init__.py
touch common/core/__init__.py
touch common/auth/__init__.py
touch common/health/__init__.py
touch common/metrics/__init__.py
```

## Step 2: Remove Files Not Needed

The following common modules are NOT needed for ZigbeeControl:
- `common/database/` - No database
- `common/storage/` - No S3
- `common/sse/` - Uses internal SSE, not SSE Gateway
- `common/tasks/` - No background tasks

Delete from copied common/core:
- `common/core/container.py` - Will create app-specific one
- `common/core/app.py` - Will create app-specific one

## Step 3: Update pyproject.toml

Add new dependencies:

```toml
[tool.poetry.dependencies]
# ... existing deps ...
pydantic-settings = "^2.1.0"
dependency-injector = "^4.41.0"
httpx = "^0.27.0"
PyJWT = "^2.8.0"
prometheus-client = "^0.21.0"
```

## Step 4: Create App Config

Create `app/config.py`:

```python
"""Application configuration using Pydantic settings."""

from functools import lru_cache

from pydantic import Field

from common.core.settings import CommonSettings


class Settings(CommonSettings):
    """ZigbeeControl-specific settings."""

    # Tab configuration
    APP_TABS_CONFIG: str = Field(
        description="Path to tabs configuration YAML"
    )

    # SSE settings
    APP_SSE_HEARTBEAT_SECONDS: float | None = Field(
        default=None,
        description="SSE heartbeat interval (default: 5 dev, 30 prod)"
    )

    # Legacy auth settings (kept for reference during transition)
    APP_AUTH_DISABLED: bool = Field(
        default=False,
        description="Disable authentication (development only)"
    )

    @property
    def sse_heartbeat_interval(self) -> float:
        """Get SSE heartbeat interval with environment-aware default."""
        if self.APP_SSE_HEARTBEAT_SECONDS is not None:
            return self.APP_SSE_HEARTBEAT_SECONDS
        return 5.0 if self.FLASK_ENV == "development" else 30.0


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
```

## Step 5: Create Container

Create `app/container.py`:

```python
"""Dependency injection container for ZigbeeControl."""

from dependency_injector import containers, providers

from app.config import Settings
from app.services.config_service import ConfigService
from app.services.kubernetes_service import KubernetesService
from app.services.status_broadcaster import StatusBroadcaster
from app.utils.config_loader import load_tabs_config
from common.auth.oidc import OIDCAuthenticator
from common.auth.oidc_client import OIDCClient
from common.core.shutdown import ShutdownCoordinator
from common.metrics.service import MetricsService


class AppContainer(containers.DeclarativeContainer):
    """Application dependency injection container."""

    wiring_config = containers.WiringConfiguration(
        modules=[
            "app.api.auth",
            "app.api.config",
            "app.api.restart",
            "app.api.status",
            "common.health.routes",
            "common.metrics.routes",
        ]
    )

    config = providers.Configuration()

    # Settings instance (for services that need the full object)
    settings = providers.Singleton(Settings)

    # Core services
    shutdown_coordinator = providers.Singleton(
        ShutdownCoordinator,
        graceful_shutdown_timeout=config.GRACEFUL_SHUTDOWN_TIMEOUT,
    )

    metrics_service = providers.Singleton(MetricsService)

    # OIDC services
    oidc_authenticator = providers.Singleton(
        OIDCAuthenticator,
        settings=settings,
    )

    oidc_client = providers.Singleton(
        OIDCClient,
        settings=settings,
    )

    # App-specific services
    tabs_config = providers.Singleton(
        load_tabs_config,
        path=config.APP_TABS_CONFIG,
    )

    config_service = providers.Singleton(
        ConfigService,
        tabs=providers.Factory(lambda tc: tc.tabs, tabs_config),
    )

    status_broadcaster = providers.Factory(
        StatusBroadcaster,
        tab_count=providers.Factory(lambda cs: cs.tab_count(), config_service),
    )

    kubernetes_service = providers.Singleton(
        KubernetesService,
        status_broadcaster=status_broadcaster,
    )
```

## Step 6: Create Auth API

Replace `app/api/auth.py` with OIDC-based auth:

```python
"""Authentication endpoints using OIDC BFF pattern."""

import base64
import json
import logging
from typing import Any

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, Response, make_response, redirect, request
from pydantic import BaseModel, Field

from app.config import Settings
from app.container import AppContainer
from common.auth.oidc import OIDCAuthenticator
from common.auth.oidc_client import OIDCClient, AuthState
from common.core.errors import handle_api_errors

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


class UserInfoResponse(BaseModel):
    """Response for current user info."""
    subject: str = Field(description="User subject from JWT")
    email: str | None = Field(description="User email")
    name: str | None = Field(description="User display name")
    authenticated: bool = Field(default=True)


def _serialize_auth_state(state: AuthState, secret_key: str) -> str:
    """Serialize auth state to signed cookie value."""
    import hmac
    data = json.dumps({
        "code_verifier": state.code_verifier,
        "redirect_url": state.redirect_url,
        "nonce": state.nonce,
    })
    signature = hmac.new(secret_key.encode(), data.encode(), "sha256").hexdigest()
    return base64.urlsafe_b64encode(f"{data}|{signature}".encode()).decode()


def _deserialize_auth_state(value: str, secret_key: str) -> AuthState:
    """Deserialize and verify auth state from cookie."""
    import hmac
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


@auth_bp.route("/self", methods=["GET"])
@handle_api_errors
@inject
def get_current_user(
    authenticator: OIDCAuthenticator = Provide[AppContainer.oidc_authenticator],
    config: Settings = Provide[AppContainer.config],
) -> tuple[dict[str, Any], int]:
    """Get current authenticated user info."""
    if not config.OIDC_ENABLED:
        return UserInfoResponse(
            subject="local-user",
            email="admin@local",
            name="Local Admin",
        ).model_dump(), 200

    user = authenticator.authenticate()
    if not user:
        return {"error": "Not authenticated", "authenticated": False}, 401

    return UserInfoResponse(
        subject=user.sub,
        email=user.email,
        name=user.name,
    ).model_dump(), 200


@auth_bp.route("/login", methods=["GET"])
@handle_api_errors
@inject
def login(
    oidc_client: OIDCClient = Provide[AppContainer.oidc_client],
    config: Settings = Provide[AppContainer.config],
) -> Response:
    """Initiate OIDC login flow."""
    if not config.OIDC_ENABLED:
        return redirect("/")

    redirect_url = request.args.get("redirect", "/")

    # Generate authorization URL with PKCE
    auth_url, auth_state = oidc_client.generate_authorization_url(redirect_url)

    response = make_response(redirect(auth_url))

    # Store state in signed cookie
    signed_state = _serialize_auth_state(auth_state, config.SECRET_KEY)
    response.set_cookie(
        "auth_state",
        signed_state,
        httponly=True,
        secure=config.BASEURL.startswith("https"),
        samesite=config.OIDC_COOKIE_SAMESITE,
        max_age=600,
    )

    return response


@auth_bp.route("/callback", methods=["GET"])
@handle_api_errors
@inject
def callback(
    oidc_client: OIDCClient = Provide[AppContainer.oidc_client],
    config: Settings = Provide[AppContainer.config],
) -> Response:
    """Handle OIDC callback."""
    code = request.args.get("code")
    state = request.args.get("state")

    if not code or not state:
        return make_response("Missing code or state", 400)

    # Verify state
    signed_state = request.cookies.get("auth_state")
    if not signed_state:
        return make_response("Missing auth state cookie", 400)

    try:
        auth_state = _deserialize_auth_state(signed_state, config.SECRET_KEY)
    except ValueError as e:
        logger.warning("Invalid auth state: %s", e)
        return make_response("Invalid auth state", 400)

    # Verify nonce matches
    if state != auth_state.nonce:
        return make_response("State mismatch", 400)

    # Exchange code for tokens
    tokens = oidc_client.exchange_code_for_tokens(code, auth_state.code_verifier)

    response = make_response(redirect(auth_state.redirect_url))

    # Set access token cookie
    secure = config.BASEURL.startswith("https")
    response.set_cookie(
        config.OIDC_COOKIE_NAME,
        tokens.access_token,
        httponly=True,
        secure=secure,
        samesite=config.OIDC_COOKIE_SAMESITE,
        max_age=tokens.expires_in,
    )

    # Set refresh token if available (180 days = 15552000 seconds)
    if tokens.refresh_token:
        response.set_cookie(
            "refresh_token",
            tokens.refresh_token,
            httponly=True,
            secure=secure,
            samesite=config.OIDC_COOKIE_SAMESITE,
            max_age=15552000,
        )

    # Set ID token for logout
    if tokens.id_token:
        response.set_cookie(
            "id_token",
            tokens.id_token,
            httponly=True,
            secure=secure,
            samesite=config.OIDC_COOKIE_SAMESITE,
            max_age=tokens.expires_in,
        )

    # Clear auth state cookie
    response.delete_cookie("auth_state")

    return response


@auth_bp.route("/logout", methods=["GET"])
@inject
def logout(
    oidc_client: OIDCClient = Provide[AppContainer.oidc_client],
    config: Settings = Provide[AppContainer.config],
) -> Response:
    """Log out and clear cookies."""
    redirect_url = request.args.get("redirect", "/")

    # Build absolute post-logout redirect
    if redirect_url.startswith("/"):
        post_logout_uri = f"{config.BASEURL}{redirect_url}"
    else:
        post_logout_uri = redirect_url

    # Try to get OIDC logout URL
    id_token = request.cookies.get("id_token")
    oidc_logout_url = None
    if config.OIDC_ENABLED:
        oidc_logout_url = oidc_client.get_logout_url(post_logout_uri, id_token)

    final_redirect = oidc_logout_url or redirect_url
    response = make_response(redirect(final_redirect))

    # Clear all auth cookies
    secure = config.BASEURL.startswith("https")
    for cookie in [config.OIDC_COOKIE_NAME, "refresh_token", "id_token"]:
        response.set_cookie(
            cookie, "", max_age=0, httponly=True,
            secure=secure, samesite=config.OIDC_COOKIE_SAMESITE
        )

    return response


@auth_bp.route("/check", methods=["GET"])
@handle_api_errors
@inject
def check(
    authenticator: OIDCAuthenticator = Provide[AppContainer.oidc_authenticator],
    config: Settings = Provide[AppContainer.config],
) -> tuple[dict[str, Any], int]:
    """Check if user is authenticated (for frontend)."""
    if not config.OIDC_ENABLED:
        return {"authenticated": True, "disabled": True}, 200

    user = authenticator.authenticate()
    if user:
        return {"authenticated": True, "disabled": False}, 200
    return {"authenticated": False, "disabled": False}, 200
```

## Step 7: Update App Factory

Replace `app/__init__.py`:

```python
"""Application factory for ZigbeeControl backend."""

import logging
import os

from dotenv import load_dotenv
from flask import Flask
from flask_cors import CORS
from spectree import SpecTree

from app.config import Settings, get_settings
from app.container import AppContainer

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> Flask:
    """Create and configure Flask application."""
    load_dotenv()

    if settings is None:
        settings = get_settings()

    app = Flask(__name__)
    app.config.from_object(settings)
    app.secret_key = settings.SECRET_KEY

    # Initialize container
    container = AppContainer()
    container.config.from_pydantic(settings)
    app.container = container

    # Configure CORS
    CORS(app, origins=settings.CORS_ORIGINS)

    # Register error handlers
    from common.core.errors import register_error_handlers, setup_request_id
    register_error_handlers(app)

    @app.before_request
    def before_request() -> None:
        setup_request_id()

    # Register blueprints
    from app.api.auth import auth_bp
    from app.api.config import config_bp
    from app.api.restart import restart_bp
    from app.api.status import status_bp
    from common.health.routes import health_bp
    from common.metrics.routes import metrics_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(config_bp)
    app.register_blueprint(restart_bp)
    app.register_blueprint(status_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(metrics_bp)

    # Configure SpecTree
    spectree = SpecTree("flask", title="ZigbeeControl API", version="1.0.0")
    spectree.register(app)

    # Store services in extensions for legacy access patterns
    app.extensions.setdefault("z2m", {})
    app.extensions["z2m"].update({
        "config_service": container.config_service(),
        "status_broadcaster": container.status_broadcaster(),
        "kubernetes_service": container.kubernetes_service(),
        "sse_heartbeat_seconds": settings.sse_heartbeat_interval,
    })

    return app
```

## Step 8: Update .env.example

Replace with:

```bash
# =============================================================================
# Core Settings
# =============================================================================
SECRET_KEY=change-me-in-production
FLASK_ENV=development
CORS_ORIGINS=["http://localhost:3000"]

# =============================================================================
# ZigbeeControl Settings
# =============================================================================
# Path to the tabs configuration YAML file
APP_TABS_CONFIG=/data/tabs.yaml

# SSE heartbeat interval (default: 5 dev, 30 prod)
# APP_SSE_HEARTBEAT_SECONDS=5

# =============================================================================
# OIDC Authentication
# =============================================================================
# App base URL (for OIDC redirects)
BASEURL=http://localhost:3000

# Enable/disable OIDC (set to false for local dev without Keycloak)
OIDC_ENABLED=false

# Keycloak configuration
OIDC_ISSUER_URL=https://keycloak.example.com/realms/home
OIDC_CLIENT_ID=zigbee-control
OIDC_CLIENT_SECRET=

# Cookie settings
OIDC_COOKIE_NAME=zigbee_auth
OIDC_COOKIE_SAMESITE=Lax

# =============================================================================
# Server Settings
# =============================================================================
HOST=0.0.0.0
PORT=5000
```

## Step 9: Keycloak Configuration

1. Create client `zigbee-control` in your realm:
   - Client Protocol: openid-connect
   - Access Type: confidential
   - Valid Redirect URIs: `https://your-domain/api/auth/callback`
   - Web Origins: `https://your-domain`

2. Configure Remember Me (Realm Settings → Sessions):
   - Enable "Remember Me"
   - SSO Session Idle Remember Me: `15552000` (180 days)
   - SSO Session Max Remember Me: `15552000` (180 days)

## Step 10: Update Existing API Files

Update other API files to use dependency injection. For example, update `app/api/config.py`:

```python
# Add at top:
from dependency_injector.wiring import Provide, inject
from app.container import AppContainer

# Update route handlers to use @inject decorator:
@config_bp.route("/config", methods=["GET"])
@inject
def get_config(
    config_service: ConfigService = Provide[AppContainer.config_service],
) -> ...:
    ...
```

## Step 11: Delete Old Auth Files

Remove:
- `app/utils/auth.py` (old AuthManager)
- `app/schemas/auth.py` (old schemas)

## Step 12: Update Tests

Update test fixtures to use the new container and settings pattern. See `/work/backend/tests/conftest.py` for reference.

## Verification

After migration:

1. Health check: `curl http://localhost:5000/health/healthz`
2. Auth check (disabled): `curl http://localhost:5000/api/auth/check`
3. Metrics: `curl http://localhost:5000/metrics`
4. Existing functionality: Verify config/status/restart endpoints work
