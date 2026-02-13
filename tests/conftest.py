"""Pytest fixtures for mother project (infrastructure) tests.

These tests validate the template's infrastructure code independently of
any generated app's domain fixtures. They use real Ceph/S3 and in-memory
SQLite with the template cloning pattern.

Run from inside test-app: cd test-app && python -m pytest ../tests/ -v
"""

import os
import socket
import sqlite3
import threading
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from dotenv import load_dotenv
from flask import Flask
from prometheus_client import REGISTRY
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import create_app
from app.app_config import AppSettings
from app.config import Settings
from app.database import upgrade_database
from app.exceptions import InvalidOperationException
from app.services.container import ServiceContainer

# Load test environment variables from .env.test
_TEST_ENV_FILE = Path(__file__).parent.parent / ".env.test"
if _TEST_ENV_FILE.exists():
    load_dotenv(_TEST_ENV_FILE, override=True)


def pytest_configure(config: pytest.Config) -> None:
    """Verify S3/Ceph is reachable before running any tests."""
    import urllib.error
    import urllib.request

    endpoint = os.environ.get("S3_ENDPOINT_URL", "http://localhost:9000")
    try:
        urllib.request.urlopen(endpoint, timeout=3)
    except (urllib.error.URLError, OSError, TimeoutError):
        pytest.exit(
            f"S3/Ceph is not reachable at {endpoint}. "
            "Ensure S3_ENDPOINT_URL is configured in .env.test.",
            returncode=1,
        )


@pytest.fixture(autouse=True)
def clear_prometheus_registry():
    """Clear Prometheus registry before and after each test for isolation."""
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
    """Construct base Settings object for tests."""
    return Settings(
        database_url="sqlite:///:memory:",
        db_pool_size=20,
        db_pool_max_overflow=30,
        db_pool_timeout=10,
        db_pool_echo=False,
        diagnostics_enabled=False,
        diagnostics_slow_query_threshold_ms=100,
        diagnostics_slow_request_threshold_ms=500,
        diagnostics_log_all_queries=False,
        secret_key="test-secret-key",
        debug=True,
        flask_env="testing",
        cors_origins=["http://localhost:3000"],
        task_max_workers=4,
        task_timeout_seconds=300,
        task_cleanup_interval_seconds=600,
        metrics_update_interval=60,
        graceful_shutdown_timeout=600,
        drain_auth_key="",
        # S3 configuration (from .env.test)
        s3_endpoint_url=os.environ.get("S3_ENDPOINT_URL", "http://localhost:9000"),
        s3_access_key_id=os.environ.get("S3_ACCESS_KEY_ID", "admin"),
        s3_secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY", "password"),
        s3_bucket_name=os.environ.get("S3_BUCKET_NAME", "modern-app-template-test"),
        s3_region=os.environ.get("S3_REGION", "us-east-1"),
        s3_use_ssl=os.environ.get("S3_USE_SSL", "false").lower() == "true",
        # SSE
        sse_heartbeat_interval=1,
        frontend_version_url="http://localhost:3000/version.json",
        sse_gateway_url="http://localhost:3001",
        sse_callback_secret="",
        # OIDC Authentication (disabled for most tests)
        baseurl="http://localhost:3000",
        oidc_enabled=False,
        oidc_issuer_url="https://auth.example.com/realms/test",
        oidc_client_id="test-backend",
        oidc_client_secret=None,
        oidc_scopes="openid profile email",
        oidc_audience="test-backend",
        oidc_clock_skew_seconds=30,
        oidc_cookie_name="access_token",
        oidc_cookie_secure=False,
        oidc_cookie_samesite="Lax",
        oidc_refresh_cookie_name="refresh_token",
    )


def _build_test_app_settings() -> AppSettings:
    """Construct base AppSettings object for tests."""
    return AppSettings()


def _assert_s3_available(app: Flask) -> None:
    """Ensure S3 storage is reachable for tests."""
    try:
        app.container.s3_service().ensure_bucket_exists()
    except InvalidOperationException as exc:
        pytest.fail(
            f"S3 storage is not available for tests: {exc.message}. "
            "Ensure S3_ENDPOINT_URL, credentials, and bucket access are configured."
        )
    except Exception as exc:
        pytest.fail(
            f"Unexpected error while verifying S3 availability for tests: {exc}"
        )


@pytest.fixture
def test_settings() -> Settings:
    """Create test settings."""
    return _build_test_settings()


@pytest.fixture
def test_app_settings() -> AppSettings:
    """Create test app settings."""
    return _build_test_app_settings()


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

    template_app = create_app(
        settings, app_settings=AppSettings(), skip_background_services=True
    )
    with template_app.app_context():
        upgrade_database(recreate=True)
        _assert_s3_available(template_app)

    yield conn
    conn.close()


@pytest.fixture
def app(
    test_settings: Settings,
    test_app_settings: AppSettings,
    template_connection: sqlite3.Connection,
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

    application = create_app(settings, app_settings=test_app_settings)

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
    """Access to the DI container for testing with session provided."""
    container = app.container

    with app.app_context():
        from sqlalchemy.orm import sessionmaker

        from app.extensions import db as flask_db

        SessionLocal = sessionmaker(
            bind=flask_db.engine, autoflush=True, expire_on_commit=False
        )

    container.session_maker.override(SessionLocal)

    return container


@pytest.fixture
def session(container: ServiceContainer) -> Generator[Session, None, None]:
    """Create a new database session for a test."""
    session = container.db_session()

    exc = None
    try:
        yield session
    except Exception as e:
        exc = e

    if exc:
        session.rollback()
    else:
        session.commit()
    session.close()

    container.db_session.reset()


# ---------------------------------------------------------------------------
# OIDC fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_oidc_discovery() -> dict[str, Any]:
    """Mock OIDC discovery document for authentication tests."""
    return {
        "issuer": "https://auth.example.com/realms/test",
        "authorization_endpoint": "https://auth.example.com/realms/test/protocol/openid-connect/auth",
        "token_endpoint": "https://auth.example.com/realms/test/protocol/openid-connect/token",
        "end_session_endpoint": "https://auth.example.com/realms/test/protocol/openid-connect/logout",
        "jwks_uri": "https://auth.example.com/realms/test/protocol/openid-connect/certs",
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
    """Factory fixture to generate test JWT tokens.

    Returns a callable that generates JWT tokens with configurable claims.
    The public_key and private_key are available as attributes on the returned callable.
    """
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
            payload,
            signing_key,
            algorithm="RS256",
            headers={"kid": "test-key-id"},
        )
        return token

    _generate.public_key = public_key  # type: ignore[attr-defined]
    _generate.private_key = private_key  # type: ignore[attr-defined]

    return _generate


@pytest.fixture
def oidc_app(
    test_settings: Settings,
    test_app_settings: AppSettings,
    template_connection: sqlite3.Connection,
    mock_oidc_discovery: dict[str, Any],
    generate_test_jwt: Any,
) -> Generator[Flask, None, None]:
    """Create Flask app with OIDC enabled, using the standard template clone pattern."""
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
            mock_jwk_client.get_signing_key_from_jwt.return_value = mock_signing_key
            mock_jwk_client_class.return_value = mock_jwk_client

            application = create_app(settings, app_settings=test_app_settings)

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


# ---------------------------------------------------------------------------
# SSE Integration Test Fixtures
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    """Find a free port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


@pytest.fixture(scope="session")
def sse_server(
    template_connection: sqlite3.Connection,
) -> Generator[tuple[str, Any], None, None]:
    """Start a real Flask development server for SSE integration tests.

    Returns tuple of (base_url, app) where base_url is like http://localhost:5001.
    """
    import requests

    port = _find_free_port()

    clone_conn = sqlite3.connect(":memory:", check_same_thread=False)
    template_connection.backup(clone_conn)

    settings = _build_test_settings().model_copy(
        update={
            "database_url": "sqlite://",
            "flask_env": "testing",
            "sqlalchemy_engine_options": {
                "poolclass": StaticPool,
                "creator": lambda: clone_conn,
            },
        }
    )
    app_settings = _build_test_app_settings()

    app = create_app(settings, app_settings=app_settings)

    # Mock frontend version service to avoid external frontend dependency
    version_data = {"version": "test-1.0.0", "environment": "test", "git_commit": "abc123"}
    version_mock = patch.object(
        app.container.frontend_version_service(),
        "_fetch_frontend_version",
        return_value=version_data,
    )
    version_mock.start()

    def run_server() -> None:
        try:
            app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)
        except Exception:
            pass

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    time.sleep(1.5)

    base_url = f"http://127.0.0.1:{port}"

    max_attempts = 20
    for _ in range(max_attempts):
        try:
            resp = requests.get(f"{base_url}/health/healthz", timeout=1.0)
            if resp.status_code == 200:
                break
        except requests.RequestException:
            pass
        time.sleep(0.5)
    else:
        pytest.fail(
            f"SSE test server did not become ready after {max_attempts} attempts"
        )

    try:
        yield (base_url, app)
    finally:
        try:
            app.container.lifecycle_coordinator().shutdown()
        except Exception:
            pass

        version_mock.stop()

        with app.app_context():
            from app.extensions import db as flask_db

            flask_db.session.remove()

        clone_conn.close()


@pytest.fixture
def background_task_runner() -> (
    Generator[Callable[[Callable[[], Any]], Any], None, None]
):
    """Provide a helper to run background tasks concurrently with SSE tests."""
    threads: list[threading.Thread] = []

    def run_in_background(func: Callable[[], Any]) -> threading.Thread:
        thread = threading.Thread(target=func, daemon=False)
        threads.append(thread)
        thread.start()
        return thread

    yield run_in_background

    for thread in threads:
        thread.join(timeout=5.0)


@pytest.fixture
def sse_client_factory(sse_server: tuple[str, Any]):
    """Factory for creating SSE client instances for testing."""
    from tests.integration.sse_client_helper import SSEClient

    server_url, _ = sse_server

    def create_client(endpoint: str, strict: bool = True) -> "SSEClient":
        url = f"{server_url}{endpoint}"
        return SSEClient(url, strict=strict)

    return create_client


@pytest.fixture(scope="session")
def sse_gateway_server(
    sse_server: tuple[str, Any],
) -> Generator[str, None, None]:
    """Start SSE Gateway subprocess for integration tests.

    Returns the base URL for the gateway (e.g., http://localhost:3001).
    """
    from tests.integration.sse_gateway_helper import SSEGatewayProcess

    server_url, app = sse_server

    gateway_port = _find_free_port()

    callback_url = f"{server_url}/api/sse/callback?secret=test-secret"

    gateway = SSEGatewayProcess(
        callback_url=callback_url,
        port=gateway_port,
        startup_timeout=10.0,
        health_check_interval=0.5,
        shutdown_timeout=5.0,
    )

    try:
        gateway.start()
        gateway_url = gateway.get_base_url()

        sse_connection_manager = app.container.sse_connection_manager()
        sse_connection_manager.gateway_url = gateway_url

        yield gateway_url
    finally:
        gateway.print_logs()
        gateway.stop()
