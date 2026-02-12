"""Tests for error handling infrastructure."""


def test_404_returns_json(client):
    """Nonexistent routes return JSON 404."""
    response = client.get("/api/nonexistent")
    assert response.status_code == 404
    data = response.get_json()
    assert "error" in data


def test_405_returns_json(client):
    """Wrong HTTP method returns JSON 405."""
    response = client.delete("/health/healthz")
    assert response.status_code == 405
    data = response.get_json()
    assert "error" in data


def test_correlation_id_in_error_response(client):
    """Error responses include a correlationId field."""
    response = client.get("/api/nonexistent")
    data = response.get_json()
    assert "correlationId" in data
    assert data["correlationId"] is not None


def test_correlation_id_from_header(client):
    """X-Request-ID header is used as correlation ID in error responses."""
    response = client.get(
        "/api/nonexistent",
        headers={"X-Request-ID": "test-correlation-123"},
    )
    data = response.get_json()
    assert data["correlationId"] == "test-correlation-123"
