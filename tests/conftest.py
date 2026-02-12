"""Mother project test configuration.

These tests validate the template infrastructure. They run from inside
the test-app directory (cd test-app && python -m pytest ../tests/).

Fixtures are defined here directly (not imported from test-app's conftest)
to avoid circular imports and S3 infrastructure requirements.
"""

import sqlite3
import time
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask
from prometheus_client import REGISTRY
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import create_app
from app.config import Settings
from app.database import upgrade_database
from app.services.container import ServiceContainer


@pytest.fixture(autouse=True)
def clear_prometheus_registry():
    """Clear Prometheus registry before and after each test to ensure isolation."""
    collectors = list(REGISTRY._collector_to_names.keys())
    for collector in collectors:
        try:
            REGISTRY.unregister(collector)
        except (KeyError, ValueError):
            pass
    yield
    collectors = list(REGISTRY._collector_to_names.keys())
    for collector in collectors:
        try:
            REGISTRY.unregister(collector)
        except (KeyError, ValueError):
            pass


def _build_test_settings() -> Settings:
    """Construct base Settings object for tests (no S3 dependency)."""
    settings = Settings(
        database_url="sqlite:///:memory:",
        secret_key="test-secret-key",
        debug=True,
        flask_env="testing",
        cors_origins=["http://localhost:3000"],
        # Database pool
        db_pool_size=20,
        db_pool_max_overflow=30,
        db_pool_timeout=10,
        db_pool_echo=False,
        # Diagnostics
        diagnostics_enabled=False,
        diagnostics_slow_query_threshold_ms=100,
        diagnostics_slow_request_threshold_ms=500,
        diagnostics_log_all_queries=False,
        # OIDC Authentication (disabled for most tests)
        baseurl="http://localhost:3000",
        oidc_enabled=False,
        oidc_issuer_url="https://auth.example.com/realms/test-app",
        oidc_client_id="test-app-backend",
        oidc_client_secret=None,
        oidc_scopes="openid profile email",
        oidc_audience="test-app-backend",
        oidc_clock_skew_seconds=30,
        oidc_cookie_name="access_token",
        oidc_cookie_secure=False,
        oidc_cookie_samesite="Lax",
        oidc_refresh_cookie_name="refresh_token",
        # S3 configuration (stub values, S3 not required for mother project tests)
        s3_endpoint_url="http://localhost:9000",
        s3_access_key_id="admin",
        s3_secret_access_key="password",
        s3_bucket_name="test-app-test-attachments",
        s3_region="us-east-1",
        s3_use_ssl=False,
        thumbnail_storage_path="/tmp/test-app_thumbnails",
        # SSE
        sse_heartbeat_interval=1,
        frontend_version_url="http://localhost:3000/version.json",
        sse_gateway_url="http://localhost:3001",
        sse_callback_secret="",
        # Tasks
        task_max_workers=4,
        task_timeout_seconds=300,
        task_cleanup_interval_seconds=600,
        # Metrics
        metrics_update_interval=60,
        # Shutdown
        graceful_shutdown_timeout=600,
        drain_auth_key="",
    )
    settings.set_engine_options_override({})
    return settings


@pytest.fixture
def test_settings() -> Settings:
    """Create test settings with in-memory database."""
    return _build_test_settings()


@pytest.fixture(scope="session")
def template_connection() -> Generator[sqlite3.Connection, None, None]:
    """Create a template SQLite database once and apply migrations."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)

    settings = _build_test_settings().model_copy(
        update={
            "database_url": "sqlite://",
            "sqlalchemy_engine_options": {
                "poolclass": StaticPool,
                "creator": lambda: conn,
            },
        }
    )

    template_app = create_app(settings, skip_background_services=True)
    with template_app.app_context():
        upgrade_database(recreate=True)

    yield conn

    conn.close()


@pytest.fixture
def app(
    test_settings: Settings, template_connection: sqlite3.Connection
) -> Generator[Flask, None, None]:
    """Create Flask app for testing using a fresh copy of the template database."""
    clone_conn = sqlite3.connect(":memory:", check_same_thread=False)
    template_connection.backup(clone_conn)

    settings = test_settings.model_copy(
        update={
            "database_url": "sqlite://",
            "sqlalchemy_engine_options": {
                "poolclass": StaticPool,
                "creator": lambda: clone_conn,
            },
        }
    )

    application = create_app(settings)

    try:
        yield application
    finally:
        try:
            application.container.lifecycle_coordinator().shutdown()
        except Exception:
            pass

        with application.app_context():
            from app.extensions import db as flask_db

            flask_db.session.remove()

        clone_conn.close()


@pytest.fixture
def client(app: Flask):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def runner(app: Flask):
    """Create test CLI runner."""
    return app.test_cli_runner()


@pytest.fixture
def container(app: Flask):
    """Access to the DI container for testing."""
    di_container = app.container

    with app.app_context():
        from app.extensions import db as flask_db

        SessionLocal = sessionmaker(
            bind=flask_db.engine, autoflush=True, expire_on_commit=False
        )

    di_container.session_maker.override(SessionLocal)

    return di_container


@pytest.fixture
def session(container: ServiceContainer) -> Generator[Session, None, None]:
    """Create a new database session for a test."""
    db_session = container.db_session()

    exc = None
    try:
        yield db_session
    except Exception as e:
        exc = e

    if exc:
        db_session.rollback()
    else:
        db_session.commit()
    db_session.close()

    container.db_session.reset()


@pytest.fixture
def mock_oidc_discovery() -> dict[str, Any]:
    """Mock OIDC discovery document for authentication tests."""
    return {
        "issuer": "https://auth.example.com/realms/test-app",
        "authorization_endpoint": "https://auth.example.com/realms/test-app/protocol/openid-connect/auth",
        "token_endpoint": "https://auth.example.com/realms/test-app/protocol/openid-connect/token",
        "end_session_endpoint": "https://auth.example.com/realms/test-app/protocol/openid-connect/logout",
        "jwks_uri": "https://auth.example.com/realms/test-app/protocol/openid-connect/certs",
    }


@pytest.fixture
def mock_jwks() -> dict[str, Any]:
    """Mock JWKS (JSON Web Key Set) for authentication tests."""
    return {
        "keys": [
            {
                "kid": "test-key-id",
                "kty": "RSA",
                "use": "sig",
                "n": "test-modulus",
                "e": "AQAB",
            }
        ]
    }


@pytest.fixture
def generate_test_jwt(test_settings: Settings) -> Any:
    """Factory fixture to generate test JWT tokens."""
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    def _generate(
        subject: str = "test-user",
        email: str | None = "test@example.com",
        name: str | None = "Test User",
        roles: list[str] | None = None,
        expired: bool = False,
        invalid_signature: bool = False,
        invalid_issuer: bool = False,
        invalid_audience: bool = False,
    ) -> str:
        if roles is None:
            roles = ["admin"]

        now = int(time.time())
        exp = now - 3600 if expired else now + 3600

        payload = {
            "sub": subject,
            "iss": "https://wrong.example.com"
            if invalid_issuer
            else test_settings.oidc_issuer_url,
            "aud": "wrong-client-id"
            if invalid_audience
            else test_settings.oidc_client_id,
            "exp": exp,
            "iat": now,
            "realm_access": {"roles": roles},
        }

        if email:
            payload["email"] = email
        if name:
            payload["name"] = name

        signing_key = (
            rsa.generate_private_key(public_exponent=65537, key_size=2048)
            if invalid_signature
            else private_key
        )

        token = jwt.encode(
            payload, signing_key, algorithm="RS256", headers={"kid": "test-key-id"}
        )
        return token

    _generate.public_key = public_key  # type: ignore[attr-defined]
    _generate.private_key = private_key  # type: ignore[attr-defined]

    return _generate


@pytest.fixture
def oidc_app(
    test_settings: Settings,
    template_connection: sqlite3.Connection,
    mock_oidc_discovery: dict[str, Any],
    generate_test_jwt: Any,
) -> Generator[Flask, None, None]:
    """Create Flask app with OIDC enabled."""
    clone_conn = sqlite3.connect(":memory:", check_same_thread=False)
    template_connection.backup(clone_conn)

    settings = test_settings.model_copy(
        update={
            "database_url": "sqlite://",
            "sqlalchemy_engine_options": {
                "poolclass": StaticPool,
                "creator": lambda: clone_conn,
            },
            "oidc_enabled": True,
            "oidc_client_secret": "test-secret",
        }
    )

    with patch("httpx.get") as mock_get:
        mock_response = MagicMock()
        mock_response.json.return_value = mock_oidc_discovery
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        with patch(
            "app.services.auth_service.PyJWKClient"
        ) as mock_jwk_client_class:
            mock_jwk_client = MagicMock()
            mock_signing_key = MagicMock()
            mock_signing_key.key = generate_test_jwt.public_key
            mock_jwk_client.get_signing_key_from_jwt.return_value = (
                mock_signing_key
            )
            mock_jwk_client_class.return_value = mock_jwk_client

            application = create_app(settings)

            try:
                yield application
            finally:
                try:
                    application.container.lifecycle_coordinator().shutdown()
                except Exception:
                    pass

                with application.app_context():
                    from app.extensions import db as flask_db

                    flask_db.session.remove()
                clone_conn.close()


@pytest.fixture
def oidc_client(oidc_app: Flask) -> Any:
    """Create test client for the OIDC-enabled app."""
    return oidc_app.test_client()
