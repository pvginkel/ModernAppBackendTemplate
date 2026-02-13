"""Tests for health check API endpoints."""

from unittest.mock import patch

from flask import Flask
from flask.testing import FlaskClient

from tests.testing_utils import StubLifecycleCoordinator


class TestHealthEndpoints:
    """Test health check endpoints for Kubernetes probes."""

    def test_readyz_when_ready(self, client: FlaskClient):
        """Test readiness probe returns 200 when ready."""
        with patch('app.database.check_db_connection', return_value=True), \
             patch('app.database.get_pending_migrations', return_value=[]):
            response = client.get("/health/readyz")

            assert response.status_code == 200
            assert response.json["status"] == "ready"
            assert response.json["ready"] is True

    def test_readyz_when_shutting_down(self, app: Flask, client: FlaskClient):
        """Test readiness probe returns 503 when shutting down."""
        with app.app_context():
            coordinator = app.container.lifecycle_coordinator()

            if isinstance(coordinator, StubLifecycleCoordinator):
                coordinator.simulate_shutdown()
            else:
                coordinator._shutting_down = True

        response = client.get("/health/readyz")

        assert response.status_code == 503
        assert response.json["status"] == "shutting down"
        assert response.json["ready"] is False

    def test_healthz_always_returns_200(self, client: FlaskClient):
        """Test liveness probe always returns 200."""
        response = client.get("/health/healthz")

        assert response.status_code == 200
        assert response.json["status"] == "alive"
        assert response.json["ready"] is True

    def test_healthz_during_shutdown(self, app: Flask, client: FlaskClient):
        """Test liveness probe returns 200 even during shutdown."""
        with app.app_context():
            coordinator = app.container.lifecycle_coordinator()

            if isinstance(coordinator, StubLifecycleCoordinator):
                coordinator.simulate_shutdown()
            else:
                coordinator._shutting_down = True

        response = client.get("/health/healthz")

        assert response.status_code == 200
        assert response.json["status"] == "alive"

    def test_health_endpoints_content_type(self, client: FlaskClient):
        """Test that health endpoints return JSON content type."""
        with patch('app.database.check_db_connection', return_value=True), \
             patch('app.database.get_pending_migrations', return_value=[]):
            readyz_response = client.get("/health/readyz")
            assert readyz_response.content_type == "application/json"

        healthz_response = client.get("/health/healthz")
        assert healthz_response.content_type == "application/json"

    def test_readyz_includes_database_check_results(self, client: FlaskClient):
        """Test that readyz response includes database check details."""
        with patch('app.database.check_db_connection', return_value=True), \
             patch('app.database.get_pending_migrations', return_value=[]):
            response = client.get("/health/readyz")

            assert response.status_code == 200
            assert "database" in response.json
            assert response.json["database"]["connected"] is True
            assert response.json["database"]["ok"] is True

    def test_readyz_database_not_connected(self, client: FlaskClient):
        """Test readyz returns 503 when database is not connected."""
        with patch('app.database.check_db_connection', return_value=False):
            response = client.get("/health/readyz")

            assert response.status_code == 503
            assert response.json["ready"] is False
            assert response.json["database"]["connected"] is False

    def test_readyz_migrations_pending(self, client: FlaskClient):
        """Test readyz returns 503 when migrations are pending."""
        with patch('app.database.check_db_connection', return_value=True), \
             patch('app.database.get_pending_migrations', return_value=["rev1", "rev2"]):
            response = client.get("/health/readyz")

            assert response.status_code == 503
            assert response.json["ready"] is False
            assert response.json["database"]["migrations_pending"] == 2


class TestDrainEndpoint:
    """Test drain endpoint via HealthService."""

    def test_drain_without_auth_key_configured(self, client: FlaskClient):
        """Test drain returns 401 when DRAIN_AUTH_KEY is not configured."""
        response = client.get("/health/drain", headers={"Authorization": "Bearer test"})
        assert response.status_code == 401

    def test_drain_with_invalid_token(self, app: Flask, client: FlaskClient):
        """Test drain returns 401 with invalid bearer token."""
        with app.app_context():
            health_service = app.container.health_service()
            health_service.settings.drain_auth_key = "valid-key"

        response = client.get("/health/drain", headers={"Authorization": "Bearer wrong-key"})
        assert response.status_code == 401

    def test_drain_with_valid_token(self, app: Flask, client: FlaskClient):
        """Test drain succeeds with valid bearer token."""
        with app.app_context():
            health_service = app.container.health_service()
            health_service.settings.drain_auth_key = "test-drain-key"

        response = client.get(
            "/health/drain",
            headers={"Authorization": "Bearer test-drain-key"},
        )
        assert response.status_code == 200
        assert response.json["status"] == "alive"

    def test_drain_without_authorization_header(self, app: Flask, client: FlaskClient):
        """Test drain returns 401 without Authorization header."""
        with app.app_context():
            health_service = app.container.health_service()
            health_service.settings.drain_auth_key = "valid-key"

        response = client.get("/health/drain")
        assert response.status_code == 401
