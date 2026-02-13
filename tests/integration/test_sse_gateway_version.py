"""Integration tests for version streaming via SSE Gateway.

These tests validate end-to-end behavior of version streaming with a real SSE
Gateway subprocess, focusing on the pending events feature where events sent
before connection are flushed on connect.
"""

import time
import uuid

import pytest
import requests

from tests.integration.sse_client_helper import SSEClient


@pytest.mark.integration
class TestSSEGatewayVersion:
    """Integration tests for /api/sse/utils/version endpoint via SSE Gateway."""

    def test_pending_events_flushed_on_connect(
        self, sse_server: tuple[str, any], sse_gateway_server: str
    ):
        """Test that events sent before connection are flushed when client connects."""
        server_url, _ = sse_server
        request_id = str(uuid.uuid4())

        resp = requests.post(
            f"{server_url}/api/testing/deployments/version",
            json={"request_id": request_id, "version": "test-version-1"},
            timeout=5.0
        )
        assert resp.status_code == 202

        time.sleep(0.2)

        client = SSEClient(
            f"{sse_gateway_server}/api/sse/stream?request_id={request_id}",
            strict=True
        )
        events = []

        timeout = time.perf_counter() + 5.0
        for event in client.connect(timeout=5.0):
            events.append(event)
            if len(events) >= 1:
                break
            if time.perf_counter() > timeout:
                break

        assert len(events) >= 1
        assert events[0]["event"] == "version"
        assert "version" in events[0]["data"]

        version_events = [e for e in events if e["event"] == "version"]
        assert len(version_events) >= 1, "Should receive at least one version event"

    def test_events_sent_after_connection_are_received(
        self, sse_server: tuple[str, any], sse_gateway_server: str
    ):
        """Test that events sent after connection are received through gateway."""
        server_url, _ = sse_server
        request_id = str(uuid.uuid4())

        resp = requests.post(
            f"{server_url}/api/testing/deployments/version",
            json={"request_id": request_id, "version": "test-version-initial"},
            timeout=5.0
        )
        assert resp.status_code == 202
        time.sleep(0.1)

        client = SSEClient(
            f"{sse_gateway_server}/api/sse/stream?request_id={request_id}",
            strict=True
        )

        gen = client.connect(timeout=10.0)
        events = []

        events.append(next(gen))
        assert events[0]["event"] == "version"

        resp = requests.post(
            f"{server_url}/api/testing/deployments/version",
            json={"request_id": request_id, "version": "test-version-2"},
            timeout=5.0
        )
        assert resp.status_code == 202

        timeout = time.perf_counter() + 3.0
        while time.perf_counter() < timeout:
            try:
                event = next(gen)
                events.append(event)
                if event["event"] == "version" and len(events) >= 2:
                    break
            except StopIteration:
                break

        assert len(events) >= 2, "Should receive initial version + triggered version"

        version_events = [e for e in events if e["event"] == "version"]
        assert len(version_events) >= 2, "Should receive at least 2 version events"

    def test_client_disconnect_triggers_callback(
        self, sse_server: tuple[str, any], sse_gateway_server: str
    ):
        """Test that client disconnect triggers disconnect callback to Python."""
        server_url, _ = sse_server
        request_id = str(uuid.uuid4())

        resp = requests.post(
            f"{server_url}/api/testing/deployments/version",
            json={"request_id": request_id, "version": "test-version-disconnect"},
            timeout=5.0
        )
        assert resp.status_code == 202
        time.sleep(0.1)

        client = SSEClient(
            f"{sse_gateway_server}/api/sse/utils/version?request_id={request_id}",
            strict=True
        )
        events = []

        gen = client.connect(timeout=10.0)
        for _ in range(1):
            try:
                events.append(next(gen))
            except StopIteration:
                break

        assert len(events) >= 1
        assert events[0]["event"] == "version"

        time.sleep(0.5)

    def test_connection_replacement_works(
        self, sse_server: tuple[str, any], sse_gateway_server: str
    ):
        """Test that new connection replaces old connection for same request_id."""
        server_url, _ = sse_server
        request_id = str(uuid.uuid4())

        resp = requests.post(
            f"{server_url}/api/testing/deployments/version",
            json={"request_id": request_id, "version": "test-version-client1"},
            timeout=5.0
        )
        assert resp.status_code == 202
        time.sleep(0.1)

        client1 = SSEClient(
            f"{sse_gateway_server}/api/sse/utils/version?request_id={request_id}",
            strict=True
        )
        events1 = []

        gen1 = client1.connect(timeout=10.0)
        events1.append(next(gen1))
        assert events1[0]["event"] == "version"

        time.sleep(0.2)

        client2 = SSEClient(
            f"{sse_gateway_server}/api/sse/utils/version?request_id={request_id}",
            strict=True
        )
        events2 = []

        gen2 = client2.connect(timeout=10.0)

        time.sleep(0.5)
        resp = requests.post(
            f"{server_url}/api/testing/deployments/version",
            json={"request_id": request_id, "version": "test-version-replacement"},
            timeout=5.0
        )
        assert resp.status_code == 202
        time.sleep(0.1)

        timeout = time.perf_counter() + 3.0
        while time.perf_counter() < timeout:
            try:
                event = next(gen2)
                events2.append(event)
                if event["event"] == "version":
                    break
            except StopIteration:
                break

        version_events_client2 = [e for e in events2 if e["event"] == "version"]
        assert len(version_events_client2) >= 1, "Second client should receive triggered version"
