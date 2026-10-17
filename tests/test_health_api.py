"""Tests for health check API endpoints."""

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from flask import Flask
from flask.testing import FlaskClient

from app.config import Settings
from app.services.health_service import HealthService
from app.services.s3_service import S3Service
from app.services.sse_connection_manager import SSEConnectionManager


@pytest.fixture(autouse=True)
def fresh_readiness(app: Flask, monkeypatch: pytest.MonkeyPatch) -> Generator[HealthService]:
    """The session app's health service, holding only create_app()'s check.

    A check another test registered (say, by building the container's SSE
    connection manager) is set aside, checks this test registers are dropped
    at teardown, and the cache is cleared both ways.
    """
    health_service = app.container.health_service()
    monkeypatch.setattr(
        health_service, "_checks", {"database": health_service._checks["database"]}
    )
    health_service.clear_cache()
    yield health_service
    health_service.clear_cache()


@pytest.fixture
def sse_gateway_check(app: Flask, fresh_readiness: HealthService) -> SSEConnectionManager:
    """A connection manager that registered its gateway check on the session app."""
    return SSEConnectionManager(
        gateway_url="http://sse-gateway.invalid", http_timeout=0.5, health_service=fresh_readiness
    )


class TestReadyz:
    """GET /health/readyz on the session app."""

    def test_ready(self, client: FlaskClient):
        response = client.get("/health/readyz")

        assert response.status_code == 200
        assert response.content_type == "application/json"
        assert response.json["status"] == "ok"
        assert response.json["ready"] is True
        assert response.json["stage"] == "local-tst"
        assert response.json["checks"]["database"] == {"status": "ok", "critical": True}

    def test_shutting_down(
        self, app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
    ):
        # monkeypatch, so the session app is not left shutting down.
        coordinator = app.container.lifecycle_coordinator()
        monkeypatch.setattr(coordinator, "is_shutting_down", lambda: True)

        with patch("app.database.check_db_connection") as check:
            response = client.get("/health/readyz")

        assert response.status_code == 503
        assert response.json == {
            "status": "shutting_down", "ready": False, "stage": "local-tst", "checks": {},
        }
        check.assert_not_called()

    def test_database_not_connected(self, client: FlaskClient):
        with patch("app.database.check_db_connection", return_value=False):
            response = client.get("/health/readyz")

        assert response.status_code == 503
        assert response.json["status"] == "not_ready"
        assert response.json["ready"] is False
        assert response.json["checks"]["database"] == {
            "status": "error", "critical": True, "detail": "connection failed",
        }

    def test_migrations_pending(self, client: FlaskClient):
        with patch("app.database.get_pending_migrations", return_value=["rev1", "rev2"]):
            response = client.get("/health/readyz")

        assert response.status_code == 503
        assert response.json["checks"]["database"]["detail"] == "2 migrations pending"

    def test_migration_check_error(self, client: FlaskClient):
        """A failing migration lookup is the check's error, not "up to date"."""
        with patch(
            "app.database.get_pending_migrations",
            side_effect=RuntimeError("bad revision graph"),
        ):
            response = client.get("/health/readyz")

        assert response.status_code == 503
        assert response.json["checks"]["database"]["status"] == "error"
        assert response.json["checks"]["database"]["detail"] == "bad revision graph"

    def test_recovery_shows_at_the_next_probe(self, client: FlaskClient):
        """A failure is not cached."""
        with patch("app.database.check_db_connection", return_value=False):
            assert client.get("/health/readyz").status_code == 503

        assert client.get("/health/readyz").status_code == 200


class TestSSEGatewayCheck:
    """The SSE gateway's check is reported but not critical."""

    @pytest.mark.usefixtures("sse_gateway_check")
    def test_gateway_ready(self, client: FlaskClient):
        with patch("requests.get", return_value=MagicMock(status_code=200)):
            response = client.get("/health/readyz")

        assert response.status_code == 200
        assert response.json["status"] == "ok"
        assert response.json["checks"]["sse_gateway"] == {"status": "ok", "critical": False}

    @pytest.mark.usefixtures("sse_gateway_check")
    def test_gateway_unreachable_degrades(self, client: FlaskClient):
        with patch("requests.get", side_effect=ConnectionError("Connection refused")):
            response = client.get("/health/readyz")

        assert response.status_code == 200
        assert response.json["status"] == "degraded"
        assert response.json["ready"] is True
        assert response.json["checks"]["sse_gateway"] == {
            "status": "error", "critical": False, "detail": "Connection refused",
        }

    @pytest.mark.usefixtures("sse_gateway_check")
    def test_gateway_not_ready_degrades(self, client: FlaskClient):
        with patch("requests.get", return_value=MagicMock(status_code=503)):
            response = client.get("/health/readyz")

        assert response.status_code == 200
        assert response.json["status"] == "degraded"
        assert response.json["checks"]["sse_gateway"]["detail"] == "gateway readyz answered 503"

    @pytest.mark.usefixtures("sse_gateway_check")
    def test_critical_failure_wins(self, client: FlaskClient):
        with patch("requests.get", side_effect=ConnectionError("Connection refused")), \
             patch("app.database.check_db_connection", return_value=False):
            response = client.get("/health/readyz")

        assert response.status_code == 503
        assert response.json["status"] == "not_ready"
        assert set(response.json["checks"]) == {"database", "sse_gateway"}


class TestS3Check:
    """S3Service registers a critical check at startup."""

    def test_reachable_bucket(
        self, client: FlaskClient, test_settings: Settings, fresh_readiness: HealthService
    ):
        S3Service(test_settings, health_service=fresh_readiness).startup()

        response = client.get("/health/readyz")

        assert response.status_code == 200
        assert response.json["checks"]["s3"] == {"status": "ok", "critical": True}

    def test_unreachable_bucket_fails_readiness(
        self, client: FlaskClient, test_settings: Settings, fresh_readiness: HealthService
    ):
        s3 = S3Service(test_settings, health_service=fresh_readiness)
        s3._s3_client = MagicMock()
        s3._s3_client.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadBucket"
        )
        s3.startup()

        response = client.get("/health/readyz")

        assert response.status_code == 503
        assert response.json["status"] == "not_ready"
        assert response.json["checks"]["s3"]["status"] == "error"
        assert "Forbidden" in response.json["checks"]["s3"]["detail"]


class TestHealthz:
    def test_always_200(self, client: FlaskClient):
        response = client.get("/health/healthz")

        assert response.status_code == 200
        assert response.content_type == "application/json"
        assert response.json == {"status": "alive", "ready": True}

    def test_200_during_shutdown(
        self, app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
    ):
        coordinator = app.container.lifecycle_coordinator()
        monkeypatch.setattr(coordinator, "is_shutting_down", lambda: True)

        response = client.get("/health/healthz")

        assert response.status_code == 200
        assert response.json["status"] == "alive"


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
