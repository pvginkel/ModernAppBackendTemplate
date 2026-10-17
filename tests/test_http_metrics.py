"""Tests for the always-on HTTP request metrics (app.utils.http_metrics)."""

from flask.testing import FlaskClient
from prometheus_client.metrics import MetricWrapperBase

from app.utils.http_metrics import HTTP_REQUEST_DURATION_SECONDS, HTTP_REQUEST_TOTAL


def _sample(metric: MetricWrapperBase, name: str, **labels: str) -> float:
    """A sample's value, read from the metric itself.

    Not from the registry: the test fixtures clear it when they build an app.
    """
    for family in metric.collect():
        for sample in family.samples:
            if sample.name == name and all(sample.labels.get(k) == v for k, v in labels.items()):
                return sample.value
    return 0.0


class TestHttpMetrics:
    def test_request_is_counted_and_timed_with_status(self, client: FlaskClient):
        labels = {"method": "GET", "endpoint": "health.healthz", "status": "200"}
        count_before = _sample(HTTP_REQUEST_TOTAL, "http_request_total", **labels)
        observed_before = _sample(
            HTTP_REQUEST_DURATION_SECONDS, "http_request_duration_seconds_count", **labels
        )

        assert client.get("/health/healthz").status_code == 200

        assert _sample(HTTP_REQUEST_TOTAL, "http_request_total", **labels) == count_before + 1
        assert (
            _sample(HTTP_REQUEST_DURATION_SECONDS, "http_request_duration_seconds_count", **labels)
            == observed_before + 1
        )

    def test_unmatched_path_is_labelled_unknown(self, client: FlaskClient):
        labels = {"method": "GET", "endpoint": "unknown", "status": "404"}
        before = _sample(HTTP_REQUEST_TOTAL, "http_request_total", **labels)

        assert client.get("/no/such/path/a1b2c3").status_code == 404

        assert _sample(HTTP_REQUEST_TOTAL, "http_request_total", **labels) == before + 1
        assert not any(
            "a1b2c3" in str(sample.labels)
            for family in HTTP_REQUEST_TOTAL.collect()
            for sample in family.samples
        )
