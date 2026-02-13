"""Baseline integration tests for version stream SSE endpoint.

These tests validate the current Flask SSE implementation behavior before migrating
to SSE Gateway. They use a real Flask server (waitress) and SSE client to test
actual streaming behavior.
"""

import time
from typing import Any

import pytest


@pytest.mark.integration
class TestVersionStreamBaseline:
    """Baseline integration tests for /api/utils/version/stream endpoint."""

    def test_version_event_received_immediately(self, sse_server: tuple[str, Any], sse_client_factory):
        """Test that version event is received immediately on connect."""
        client = sse_client_factory("/api/utils/version/stream")
        events = []

        for event in client.connect(timeout=10.0):
            events.append(event)
            if len(events) >= 3:
                break

        assert len(events) >= 1
        assert events[0]["event"] == "version"

        version_data = events[0]["data"]
        assert "version" in version_data
        assert "environment" in version_data
        assert "git_commit" in version_data

        assert isinstance(version_data["version"], str)
        assert isinstance(version_data["environment"], str)

        assert version_data["version"] == "test-1.0.0"
        assert version_data["environment"] == "test"
        assert version_data["git_commit"] == "abc123"

    def test_heartbeat_events_received(self, sse_server: tuple[str, Any], sse_client_factory):
        """Test that periodic heartbeat events are received."""
        client = sse_client_factory("/api/utils/version/stream")
        events = []
        start_time = time.perf_counter()

        for event in client.connect(timeout=10.0):
            events.append(event)
            if time.perf_counter() - start_time > 3.0:
                break

        heartbeat_events = [e for e in events if e["event"] == "heartbeat"]

        assert len(heartbeat_events) >= 1, "Should receive at least one heartbeat"

        for hb in heartbeat_events:
            assert "timestamp" in hb["data"]
            assert hb["data"]["timestamp"] == "keepalive"

    def test_heartbeat_timing_within_configured_interval(self, sse_server: tuple[str, Any], sse_client_factory):
        """Test that heartbeat events occur within 2x the configured interval."""
        client = sse_client_factory("/api/utils/version/stream")
        heartbeat_times = []
        start_time = time.perf_counter()

        for event in client.connect(timeout=10.0):
            if event["event"] == "heartbeat":
                heartbeat_times.append(time.perf_counter() - start_time)
            if len(heartbeat_times) >= 3:
                break
            if time.perf_counter() - start_time > 10.0:
                break

        max_interval = 2.0

        for i in range(1, len(heartbeat_times)):
            interval = heartbeat_times[i] - heartbeat_times[i - 1]
            assert interval <= max_interval, \
                f"Heartbeat interval {interval}s exceeds maximum {max_interval}s"

    def test_event_ordering_is_correct(self, sse_server: tuple[str, Any], sse_client_factory):
        """Test that events arrive in correct order: version -> heartbeats."""
        client = sse_client_factory("/api/utils/version/stream")
        events = []

        for event in client.connect(timeout=10.0):
            events.append(event)
            if len(events) >= 3:
                break

        assert events[0]["event"] == "version", "First event must be version"

        for event in events[1:]:
            assert event["event"] == "heartbeat", \
                f"Expected heartbeat, got {event['event']}"

    def test_correlation_id_when_present(self, sse_server: tuple[str, Any], sse_client_factory):
        """Test that correlation_id is consistent across events when present."""
        client = sse_client_factory("/api/utils/version/stream")
        events = []

        for event in client.connect(timeout=10.0):
            events.append(event)
            if len(events) >= 5:
                break

        correlation_ids = [
            event["data"].get("correlation_id")
            for event in events
            if "correlation_id" in event["data"]
        ]

        if correlation_ids:
            assert all(cid == correlation_ids[0] for cid in correlation_ids), \
                "Correlation IDs should be consistent across all events in a stream"

    def test_connection_remains_open(self, sse_server: tuple[str, Any], sse_client_factory):
        """Test that connection remains open and continues sending events."""
        client = sse_client_factory("/api/utils/version/stream")
        events = []
        start_time = time.perf_counter()

        for event in client.connect(timeout=10.0):
            events.append(event)
            if time.perf_counter() - start_time > 4.0:
                break

        assert len(events) >= 3, "Should receive multiple events over time"

        close_events = [e for e in events if e["event"] == "connection_close"]
        assert len(close_events) == 0, "Connection should remain open"

    def test_version_event_format(self, sse_server: tuple[str, Any], sse_client_factory):
        """Test that version event has correct format and event name."""
        client = sse_client_factory("/api/utils/version/stream")
        events = []

        for event in client.connect(timeout=10.0):
            events.append(event)
            if len(events) >= 3:
                break

        version_events = [e for e in events if e["event"] == "version"]
        assert len(version_events) == 1, "Should receive exactly one version event"

        version_event = version_events[0]
        assert version_event["event"] == "version"
        assert isinstance(version_event["data"], dict)

        assert "version" in version_event["data"]
        assert "environment" in version_event["data"]
        assert "git_commit" in version_event["data"]

        for key, value in version_event["data"].items():
            assert isinstance(value, str | int | float | bool | type(None)), \
                f"Field {key} has non-serializable type {type(value)}"

    def test_request_id_query_parameter_accepted(self, sse_server: tuple[str, Any], sse_client_factory):
        """Test that request_id query parameter is accepted and processed."""
        import requests

        server_url, _ = sse_server
        url = f"{server_url}/api/utils/version/stream?request_id=test-request-123"

        response = requests.get(url, stream=True, timeout=5.0)

        assert response.status_code == 200
        assert response.headers["Content-Type"].startswith("text/event-stream")

        events = []
        for line in response.iter_lines(decode_unicode=True):
            if line.startswith("event:"):
                event_name = line[6:].strip()
                events.append(event_name)
            if len(events) >= 2:
                break

        assert "version" in events
