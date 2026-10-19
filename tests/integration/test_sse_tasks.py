"""Task events over the backend's own SSE stream, on a real HTTP server.

All clients connect to /api/sse/stream?request_id=<id> on Flask's dev
server. The tasks start through the testing endpoint, outside the API
blueprint, so they have no subject: their events go to the local admin,
which every connection is with OIDC off. The task_id is used as the
request_id to tell the tests' connections apart.
"""

import time
from typing import Any

import requests

from app.services.sse_connection_manager import SSEConnectionManager
from tests.integration.sse_client_helper import SSEClient


def _start_task(server_url: str, steps: int, delay: float) -> str:
    resp = requests.post(
        f"{server_url}/api/testing/tasks/start",
        json={"task_type": "demo_task", "params": {"steps": steps, "delay": delay}},
        timeout=5.0,
    )
    assert resp.status_code == 200
    return str(resp.json()["task_id"])


def _manager(sse_server: tuple[str, Any]) -> SSEConnectionManager:
    _, app = sse_server
    manager: SSEConnectionManager = app.container.sse_connection_manager()
    return manager


def _wait_until_gone(
    manager: SSEConnectionManager, request_id: str, timeout: float
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not manager.has_connection(request_id):
            return True
        time.sleep(0.05)
    return False


class TestSSETasks:
    """Task events reach the browser's stream."""

    def test_task_progress_events_are_received(self, sse_server: tuple[str, Any]):
        server_url, _ = sse_server
        # Enough steps and delay that the task is still running when the
        # client has connected.
        task_id = _start_task(server_url, steps=5, delay=0.5)

        client = SSEClient(
            f"{server_url}/api/sse/stream?request_id={task_id}", strict=True
        )
        events = []
        for event in client.connect(timeout=5.0):
            events.append(event)
            if len(events) >= 5:
                break

        task_events = [e for e in events if e["event"] == "task_event"]
        progress_events = [
            e for e in task_events if e["data"]["event_type"] == "progress_update"
        ]

        assert len(progress_events) >= 1, "Should receive at least one progress event"

        for event in progress_events:
            assert event["event"] == "task_event"
            assert event["data"]["event_type"] == "progress_update"
            assert "task_id" in event["data"]
            assert "timestamp" in event["data"]
            assert "data" in event["data"]

    def test_task_completed_event_does_not_close_connection(
        self, sse_server: tuple[str, Any]
    ):
        server_url, _ = sse_server
        manager = _manager(sse_server)
        # Long enough to connect before it completes
        task_id = _start_task(server_url, steps=2, delay=0.5)

        client = SSEClient(
            f"{server_url}/api/sse/stream?request_id={task_id}", strict=True
        )
        stream = client.connect(timeout=5.0)
        completed_events = []
        for event in stream:
            if (
                event["event"] == "task_event"
                and event["data"]["event_type"] == "task_completed"
            ):
                completed_events.append(event)
                break

        # The stream outlives the task: past a heartbeat, still registered
        time.sleep(1.5)
        assert manager.has_connection(task_id), (
            "The stream should stay open after the task"
        )
        stream.close()

        assert len(completed_events) == 1, "Should receive the task_completed event"
        completed = completed_events[0]
        assert completed["data"]["task_id"] == task_id
        assert "timestamp" in completed["data"]
        assert completed["data"]["data"]["status"] == "success"

    def test_client_disconnect_unregisters_the_connection(
        self, sse_server: tuple[str, Any]
    ):
        """A browser that goes is noticed on the next write, a heartbeat at the latest."""
        server_url, _ = sse_server
        manager = _manager(sse_server)
        task_id = _start_task(server_url, steps=10, delay=1.0)

        client = SSEClient(
            f"{server_url}/api/sse/stream?request_id={task_id}", strict=True
        )
        stream = client.connect(timeout=10.0)
        assert next(stream)["event"] == "task_event"
        assert manager.has_connection(task_id)

        stream.close()

        assert _wait_until_gone(manager, task_id, timeout=5.0), (
            "The connection should be unregistered once the client has gone"
        )

    def test_a_new_client_with_the_same_request_id_replaces_the_old(
        self, sse_server: tuple[str, Any]
    ):
        server_url, _ = sse_server
        task_id = _start_task(server_url, steps=5, delay=0.2)

        client1 = SSEClient(
            f"{server_url}/api/sse/stream?request_id={task_id}", strict=True
        )
        stream1 = client1.connect(timeout=10.0)
        assert next(stream1)["event"] == "task_event"

        client2 = SSEClient(
            f"{server_url}/api/sse/stream?request_id={task_id}", strict=True
        )
        events2 = []
        for event in client2.connect(timeout=10.0):
            events2.append(event)
            if event["event"] == "task_event":
                break

        assert any(e["event"] == "task_event" for e in events2), (
            "Second client should receive task events"
        )

        # The replaced stream ends (its last events may still come first);
        # the client's deadline fails the test if it stays open
        list(stream1)
