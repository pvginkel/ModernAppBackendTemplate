"""Pytest fixtures for mother project (infrastructure) tests.

These tests validate the template's infrastructure code independently of
any generated app's domain fixtures. They use real Ceph/S3 and in-memory
SQLite with the template cloning pattern.

Run from inside test-app: cd test-app && python -m pytest ../tests/ -v
"""

import os
import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest
from dotenv import load_dotenv
from flask import Flask
from prometheus_client import REGISTRY
from sqlalchemy.pool import StaticPool

from app import create_app
from app.app_config import AppSettings
from app.config import Settings
from app.database import upgrade_database
from app.exceptions import InvalidOperationException

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
        # OIDC disabled
        oidc_enabled=False,
        oidc_issuer_url="https://auth.example.com/realms/test",
        oidc_client_id="test-backend",
    )


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
        pytest.fail(f"Unexpected error while verifying S3 availability for tests: {exc}")


@pytest.fixture
def test_settings() -> Settings:
    """Create test settings."""
    return _build_test_settings()


@pytest.fixture(scope="session")
def template_connection() -> Generator[sqlite3.Connection, None, None]:
    """Create a template SQLite database once and apply migrations."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)

    settings = _build_test_settings().model_copy(update={
        "database_url": "sqlite://",
        "sqlalchemy_engine_options": {
            "poolclass": StaticPool,
            "creator": lambda: conn,
        },
    })

    template_app = create_app(settings, app_settings=AppSettings(), skip_background_services=True)
    with template_app.app_context():
        upgrade_database(recreate=True)
        _assert_s3_available(template_app)

    yield conn
    conn.close()


@pytest.fixture
def app(test_settings: Settings, template_connection: sqlite3.Connection) -> Generator[Flask, None, None]:
    """Create Flask app for testing using a fresh copy of the template database."""
    clone_conn = sqlite3.connect(":memory:", check_same_thread=False)
    template_connection.backup(clone_conn)

    settings = test_settings.model_copy(update={
        "database_url": "sqlite://",
        "sqlalchemy_engine_options": {
            "poolclass": StaticPool,
            "creator": lambda: clone_conn,
        },
    })

    application = create_app(settings, app_settings=AppSettings())

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
