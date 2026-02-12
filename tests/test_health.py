"""Tests for health check endpoints."""


def test_healthz_returns_alive(client):
    """GET /health/healthz returns alive status."""
    response = client.get("/health/healthz")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "alive"
    assert data["ready"] is True


def test_readyz_returns_ready(client):
    """GET /health/readyz returns ready status."""
    response = client.get("/health/readyz")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ready"
    assert data["ready"] is True


def test_readyz_returns_not_ready_when_shutting_down(app, client):
    """Readyz returns 503 when lifecycle coordinator is shutting down."""
    lifecycle = app.container.lifecycle_coordinator()
    # Directly set the internal shutting-down flag
    lifecycle._shutting_down = True
    try:
        response = client.get("/health/readyz")
        assert response.status_code == 503
        data = response.get_json()
        assert data["ready"] is False
    finally:
        lifecycle._shutting_down = False


def test_drain_endpoint_requires_auth_key(client):
    """GET /health/drain without key returns 401."""
    response = client.get("/health/drain")
    assert response.status_code == 401


def test_drain_endpoint_with_valid_key(app, client):
    """GET /health/drain with valid key triggers shutdown."""
    settings = app.container.config()
    original_key = settings.drain_auth_key
    settings.drain_auth_key = "test-drain-key"
    try:
        response = client.get(
            "/health/drain",
            headers={"Authorization": "Bearer test-drain-key"},
        )
        assert response.status_code == 200
    finally:
        settings.drain_auth_key = original_key
