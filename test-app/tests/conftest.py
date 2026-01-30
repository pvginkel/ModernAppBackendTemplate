"""Pytest fixtures for testing."""

import os

import socket
import sqlite3
import threading
import time
from typing import Any, Generator


import tempfile


import pytest
from flask import Flask
from flask.testing import FlaskClient

from unittest.mock import patch


from prometheus_client import REGISTRY
from sqlalchemy.pool import StaticPool


# Set testing environment before importing app
os.environ["FLASK_ENV"] = "testing"

# Use SQLite for testing
_test_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_test_db.name}"


from app.config import Settings, get_settings
from app.container import AppContainer
from common.core.app import create_app

from common.database.extensions import db



@pytest.fixture(scope="session")
def test_settings() -> Settings:
    """Create test settings."""
    # Clear cached settings
    get_settings.cache_clear()
    settings = get_settings()

    # Configure SQLite-compatible engine options
    settings.set_engine_options_override({})

    return settings


@pytest.fixture
def app(test_settings: Settings) -> Flask:
    """Create application for testing."""
    app = create_app(
        AppContainer,
        settings=test_settings,
        skip_background_services=True,
    )
    app.config["TESTING"] = True


    # Create tables
    with app.app_context():
        db.create_all()

    yield app

    # Cleanup tables
    with app.app_context():
        db.drop_all()



@pytest.fixture
def client(app: Flask) -> FlaskClient:
    """Create test client."""
    return app.test_client()


@pytest.fixture
def app_context(app: Flask):
    """Create application context."""
    with app.app_context():
        yield



@pytest.fixture(autouse=True)
def mock_s3_health():
    """Mock S3 health check for all tests."""
    with patch("common.storage.health.check_s3_health", return_value=(True, "mocked")):
        yield




def _find_free_port() -> int:
    """Find a free port for the test server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


@pytest.fixture(scope="session")
def sse_server(test_settings: "Settings") -> Generator[tuple[str, Any], None, None]:
    """Start a real Flask development server for SSE integration tests.

    Returns tuple of (base_url, app) where base_url is like http://localhost:5001.
    The server runs in a background thread and is cleaned up after the session.
    """
    # Clear Prometheus registry before creating another Flask app
    collectors = list(REGISTRY._collector_to_names.keys())
    for collector in collectors:
        try:
            REGISTRY.unregister(collector)
        except (KeyError, ValueError):
            pass

    port = _find_free_port()

    # Create Flask app for SSE tests
    settings = test_settings.model_copy()
    settings.FLASK_ENV = "testing"

    app = create_app(
        AppContainer,
        settings=settings,
        skip_background_services=True,
    )

    # Start Flask development server in background thread
    def run_server() -> None:
        try:
            app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)
        except Exception:
            pass  # Server stopped (expected during cleanup)

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # Wait for server to be ready
    time.sleep(1.0)
    base_url = f"http://127.0.0.1:{port}"

    import requests
    max_attempts = 20
    for _ in range(max_attempts):
        try:
            resp = requests.get(f"{base_url}/health/healthz", timeout=1.0)
            if resp.status_code == 200:
                break
        except requests.RequestException:
            pass
        time.sleep(0.5)

    yield base_url, app


@pytest.fixture(scope="session")
def sse_server_url(sse_server: tuple[str, Any]) -> str:
    """Extract just the URL from the sse_server fixture.

    Use this when you need to make direct HTTP requests to the test server.
    """
    server_url, _ = sse_server
    return server_url

