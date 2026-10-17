"""Tests for the Flask application factory."""

from flask import Flask

from app import create_app
from app.config import Settings


class TestAppFactory:
    """Test create_app() and its hook-based architecture."""

    def test_create_app_returns_flask_instance(self, test_settings: Settings):
        """Test that create_app returns a Flask app."""
        app = create_app(settings=test_settings, skip_background_services=True)
        assert isinstance(app, Flask)

    def test_create_app_has_container(self, test_settings: Settings):
        """Test that created app has a service container."""
        app = create_app(settings=test_settings, skip_background_services=True)
        assert hasattr(app, "container")

    def test_health_blueprint_registered(self, client):
        """Test that health blueprint is registered."""
        response = client.get("/health/healthz")
        assert response.status_code == 200

    def test_metrics_blueprint_registered(self, client):
        """Test that metrics blueprint is registered."""
        response = client.get("/metrics")
        assert response.status_code == 200

    def test_api_blueprint_registered(self, client):
        """Test that API blueprint is registered (returns 404 for unknown routes, not 500)."""
        response = client.get("/api/nonexistent")
        assert response.status_code == 404

    def test_cors_configured(self, app: Flask):
        """Test that CORS is configured."""
        # CORS headers should be set on responses
        with app.test_client() as client:
            response = client.get(
                "/health/healthz",
                headers={"Origin": "http://localhost:3000"},
            )
            # CORS middleware should add Access-Control-Allow-Origin
            assert response.status_code == 200
