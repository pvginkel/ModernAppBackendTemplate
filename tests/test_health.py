"""Tests for health check endpoints."""

from typing import Any
from unittest.mock import patch


class TestHealthEndpoints:
    """Tests for /health/* endpoints."""

    def test_healthz_returns_200(self, client: Any) -> None:
        """Test liveness probe returns 200."""
        response = client.get("/health/healthz")

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "alive"
        assert data["ready"] is True

    def test_readyz_returns_200_when_healthy(self, client: Any) -> None:
        """Test readiness probe returns 200 when all checks pass."""
        # Mock S3 health check to return healthy
        with patch("common.health.routes.check_s3_health") as mock_s3:
            mock_s3.return_value = (True, "S3 storage is accessible")

            response = client.get("/health/readyz")

            assert response.status_code == 200
            data = response.get_json()
            assert data["status"] == "ready"
            assert data["ready"] is True
            assert "database" in data
            assert "s3" in data

    def test_readyz_returns_503_when_database_unhealthy(self, client: Any) -> None:
        """Test readiness probe returns 503 when database is unhealthy."""
        with patch("common.health.routes.check_db_connection") as mock_db:
            mock_db.return_value = False

            with patch("common.health.routes.check_s3_health") as mock_s3:
                mock_s3.return_value = (True, "OK")

                response = client.get("/health/readyz")

                assert response.status_code == 503
                data = response.get_json()
                assert data["ready"] is False
                assert data["database"]["connected"] is False

    def test_readyz_returns_503_when_s3_unhealthy(self, client: Any) -> None:
        """Test readiness probe returns 503 when S3 is unhealthy."""
        with patch("common.health.routes.check_s3_health") as mock_s3:
            mock_s3.return_value = (False, "S3 bucket not found")

            response = client.get("/health/readyz")

            assert response.status_code == 503
            data = response.get_json()
            assert data["ready"] is False
            assert data["s3"]["healthy"] is False

    def test_readyz_returns_503_when_shutting_down(
        self, client: Any, container: Any
    ) -> None:
        """Test readiness probe returns 503 during shutdown."""
        shutdown_coordinator = container.shutdown_coordinator()
        # Directly set the flag to simulate shutdown without triggering full sequence
        shutdown_coordinator._shutting_down = True

        try:
            response = client.get("/health/readyz")

            assert response.status_code == 503
            data = response.get_json()
            assert data["status"] == "shutting down"
            assert data["ready"] is False
        finally:
            # Reset shutdown state for other tests
            shutdown_coordinator._shutting_down = False
