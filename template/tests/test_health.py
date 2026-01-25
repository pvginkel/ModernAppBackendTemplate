"""Tests for health check endpoints."""

from flask.testing import FlaskClient


def test_healthz_returns_200(client: FlaskClient) -> None:
    """Test liveness probe returns 200."""
    response = client.get("/health/healthz")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "alive"
    assert data["ready"] is True


def test_readyz_returns_200_when_healthy(client: FlaskClient) -> None:
    """Test readiness probe returns 200 when healthy."""
    response = client.get("/health/readyz")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ready"
    assert data["ready"] is True
