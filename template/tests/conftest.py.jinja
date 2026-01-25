"""Pytest fixtures for testing."""

import os

import pytest
from flask import Flask
from flask.testing import FlaskClient

# Set testing environment before importing app
os.environ["FLASK_ENV"] = "testing"

from app.config import Settings, get_settings
from app.container import AppContainer
from common.core.app import create_app


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    """Create test settings."""
    # Clear cached settings
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
def app(test_settings: Settings) -> Flask:
    """Create application for testing."""
    app = create_app(
        AppContainer,
        settings=test_settings,
        skip_background_services=True,
    )
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    """Create test client."""
    return app.test_client()


@pytest.fixture
def app_context(app: Flask):
    """Create application context."""
    with app.app_context():
        yield
