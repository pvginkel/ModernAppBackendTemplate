"""Pytest configuration and fixtures for template tests.

These tests run against the generated test-app to validate the template.
"""

import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from prometheus_client import REGISTRY

# Add test-app to path so we can import from it
TEST_APP_PATH = Path(__file__).parent.parent / "test-app"
sys.path.insert(0, str(TEST_APP_PATH))


@pytest.fixture(autouse=True)
def clear_prometheus_registry() -> Generator[None, None, None]:
    """Clear Prometheus registry before and after each test.

    Metrics cannot be registered twice, so we need to clear between tests
    that create Flask apps or services with metrics.
    """
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


@pytest.fixture
def test_settings() -> Any:
    """Create test settings with in-memory database and mocked externals."""
    from app.config import Settings

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        SECRET_KEY="test-secret-key",
        DEBUG=True,
        FLASK_ENV="testing",
        HOST="127.0.0.1",
        PORT=5000,
        CORS_ORIGINS=["http://localhost:3000"],
        GRACEFUL_SHUTDOWN_TIMEOUT=5,
        METRICS_UPDATE_INTERVAL=60,
        TASK_MAX_WORKERS=2,
        TASK_TIMEOUT_SECONDS=30,
        TASK_CLEANUP_INTERVAL_SECONDS=60,
        # Database - in-memory SQLite
        DATABASE_URL="sqlite:///:memory:",
        DB_POOL_SIZE=1,
        DB_POOL_MAX_OVERFLOW=0,
        DB_POOL_TIMEOUT=30,
        # SSE Gateway
        SSE_GATEWAY_URL="http://localhost:3001",
        SSE_CALLBACK_SECRET="test-callback-secret",
        # S3 - will be mocked
        S3_ENDPOINT_URL="http://localhost:9000",
        S3_ACCESS_KEY_ID="test-access-key",
        S3_SECRET_ACCESS_KEY="test-secret-key",
        S3_BUCKET_NAME="test-bucket",
        S3_REGION="us-east-1",
        S3_USE_SSL=False,
        # OIDC - disabled for most tests
        OIDC_ENABLED=False,
        OIDC_ISSUER_URL="https://auth.example.com/realms/test",
        OIDC_CLIENT_ID="test-client",
        OIDC_CLIENT_SECRET="test-secret",
        OIDC_SCOPES="openid profile email",
        OIDC_AUDIENCE="test-client",
        OIDC_CLOCK_SKEW_SECONDS=30,
        OIDC_COOKIE_NAME="access_token",
    )
    # SQLite doesn't support pool_size/max_overflow/pool_timeout, use empty options
    settings.set_engine_options_override({})
    return settings


@pytest.fixture
def mock_s3_client() -> MagicMock:
    """Create a mock S3 client."""
    mock = MagicMock()
    mock.head_bucket.return_value = {}
    mock.create_bucket.return_value = {}
    mock.list_objects_v2.return_value = {"Contents": []}
    return mock


@pytest.fixture
def app(test_settings: Any, mock_s3_client: MagicMock) -> Generator[Any, None, None]:
    """Create Flask test application."""
    from app.container import AppContainer
    from common.core.app import create_app

    # Patch boto3 client creation to return our mock
    with patch("boto3.client", return_value=mock_s3_client):
        flask_app = create_app(
            container_class=AppContainer,
            settings=test_settings,
            skip_background_services=True,
        )
        flask_app.testing = True

        # Create tables for in-memory database
        from common.database.extensions import db
        with flask_app.app_context():
            db.create_all()

        yield flask_app


@pytest.fixture
def client(app: Any) -> Generator[Any, None, None]:
    """Create Flask test client."""
    with app.test_client() as test_client:
        yield test_client


@pytest.fixture
def app_context(app: Any) -> Generator[Any, None, None]:
    """Create Flask application context."""
    with app.app_context() as ctx:
        yield ctx


@pytest.fixture
def container(app: Any) -> Any:
    """Get the service container from the app."""
    return app.container


@pytest.fixture
def mock_oidc_config() -> dict[str, Any]:
    """Mock OIDC discovery configuration."""
    return {
        "issuer": "https://auth.example.com/realms/test",
        "authorization_endpoint": "https://auth.example.com/realms/test/protocol/openid-connect/auth",
        "token_endpoint": "https://auth.example.com/realms/test/protocol/openid-connect/token",
        "jwks_uri": "https://auth.example.com/realms/test/protocol/openid-connect/certs",
        "id_token_signing_alg_values_supported": ["RS256"],
    }
