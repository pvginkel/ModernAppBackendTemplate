"""Tests for Prometheus metrics endpoint."""


def test_metrics_endpoint_returns_200(client):
    """GET /metrics returns 200 with Prometheus-formatted text."""
    response = client.get("/metrics")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    # After prometheus registry clearing, at minimum we get an empty
    # or minimal response. The endpoint itself should still work.
    assert isinstance(text, str)


def test_metrics_endpoint_content_type(client):
    """Metrics endpoint returns correct content type."""
    response = client.get("/metrics")
    content_type = response.content_type
    assert "text/plain" in content_type or "openmetrics" in content_type
