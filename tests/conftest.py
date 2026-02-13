"""Pytest fixtures for mother project (infrastructure) tests.

These tests validate the template's infrastructure code independently of
any generated app's domain fixtures. They create a minimal Flask app with
in-memory SQLite and skip all background services (S3, SSE, etc.).
"""

import sqlite3
from collections.abc import Generator

import pytest
from flask import Flask
from prometheus_client import REGISTRY
from sqlalchemy.pool import StaticPool

from app import create_app
from app.app_config import AppSettings
from app.config import Settings


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


@pytest.fixture
def test_settings() -> Settings:
    """Create test settings with in-memory SQLite database."""
    settings = Settings(
        flask_env="testing",
        secret_key="test-secret-key",
        debug=True,
        database_url="sqlite:///:memory:",
        cors_origins=["http://localhost:3000"],
        drain_auth_key="",
        # S3 defaults (won't be used - background services skipped)
        s3_endpoint_url="http://localhost:9000",
        s3_access_key_id="admin",
        s3_secret_access_key="password",
        s3_bucket_name="test-bucket",
        s3_region="us-east-1",
        s3_use_ssl=False,
        # SSE defaults
        sse_heartbeat_interval=1,
        frontend_version_url="http://localhost:3000/version.json",
        sse_gateway_url="http://localhost:3001",
        sse_callback_secret="",
        # OIDC disabled
        oidc_enabled=False,
        oidc_issuer_url="https://auth.example.com/realms/test",
        oidc_client_id="test-backend",
    )
    settings.set_engine_options_override({})
    return settings


@pytest.fixture
def app(test_settings: Settings) -> Generator[Flask, None, None]:
    """Create Flask app for testing with in-memory SQLite."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)

    settings = test_settings.model_copy(update={
        "database_url": "sqlite://",
        "sqlalchemy_engine_options": {
            "poolclass": StaticPool,
            "creator": lambda: conn,
        },
    })

    app_settings = AppSettings()
    application = create_app(settings, app_settings=app_settings, skip_background_services=True)

    # Run migrations so the database schema exists
    with application.app_context():
        from app.database import upgrade_database
        upgrade_database(recreate=True)

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

        conn.close()


@pytest.fixture
def client(app: Flask):
    """Create test client."""
    return app.test_client()
