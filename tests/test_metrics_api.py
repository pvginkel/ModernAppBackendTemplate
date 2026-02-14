"""Tests for metrics API endpoint."""

from flask import Flask


class TestMetricsAPI:
    """Test suite for metrics API endpoint."""

    def test_get_metrics_endpoint_exists(self, client):
        """Test that /metrics endpoint exists and responds."""
        response = client.get('/metrics')
        assert response.status_code != 404

    def test_get_metrics_response_format(self, client):
        """Test that /metrics endpoint returns proper Prometheus format."""
        response = client.get('/metrics')

        assert response.status_code == 200
        assert response.content_type == 'text/plain; version=0.0.4; charset=utf-8'
        assert isinstance(response.get_data(as_text=True), str)

    def test_get_metrics_with_recorded_data(self, client):
        """Test metrics endpoint returns data after recording metrics."""
        from prometheus_client import Counter

        test_counter = Counter(
            "test_metrics_api_counter_total",
            "Temporary counter for test verification",
        )
        test_counter.inc(42)

        response = client.get('/metrics')

        assert response.status_code == 200
        content = response.get_data(as_text=True)
        assert "test_metrics_api_counter_total" in content
        assert "42.0" in content

    def test_get_metrics_method_not_allowed(self, client):
        """Test that only GET method is allowed on /metrics endpoint."""
        assert client.post('/metrics').status_code == 405
        assert client.put('/metrics').status_code == 405
        assert client.delete('/metrics').status_code == 405

    def test_get_metrics_no_authentication_required(self, client):
        """Test that /metrics endpoint doesn't require authentication."""
        response = client.get('/metrics')
        assert response.status_code not in [401, 403]
        assert response.status_code == 200

    def test_get_metrics_is_text_format(self, client):
        """Test that response is in Prometheus text format, not JSON."""
        response = client.get('/metrics')

        assert response.status_code == 200
        assert 'application/json' not in response.content_type
        assert 'text/plain' in response.content_type

    def test_get_metrics_url_path(self, client):
        """Test that metrics is at /metrics (not /api/metrics)."""
        assert client.get('/metrics').status_code == 200
        assert client.get('/api/metrics').status_code == 404
