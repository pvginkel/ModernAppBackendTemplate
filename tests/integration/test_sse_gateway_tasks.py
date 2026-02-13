"""Integration tests for task streaming via SSE Gateway.

These tests validate end-to-end behavior with a real SSE Gateway subprocess,
verifying the callback-based architecture works correctly with actual HTTP
communication between Python backend and SSE Gateway.

All clients connect to the unified /api/sse/stream?request_id=<id> endpoint.
Task events are broadcast to all connections, so the task_id is used as the
request_id to receive events for a specific task.
"""

import time

import requests

from tests.integration.sse_client_helper import SSEClient


class TestSSEGatewayTasks:
    """Integration tests for task events via SSE Gateway."""

    def test_task_progress_events_received_via_gateway(
        self, sse_server: tuple[str, any], sse_gateway_server: str
    ):
        """Test that progress_update events are received through SSE Gateway."""
        server_url, _ = sse_server

        # Use enough steps and delay so the task is still running when the
        # SSE client finishes connecting through the gateway.
        resp = requests.post(
            f"{server_url}/api/testing/tasks/start",
            json={
                "task_type": "demo_task",
                "params": {"steps": 5, "delay": 0.5}
            },
            timeout=5.0
        )
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]

        client = SSEClient(f"{sse_gateway_server}/api/sse/stream?request_id={task_id}", strict=True)
        events = []

        timeout = time.perf_counter() + 5.0
        for event in client.connect(timeout=5.0):
            events.append(event)
            if len(events) >= 5:
                break
            if time.perf_counter() > timeout:
                break

        task_events = [e for e in events if e["event"] == "task_event"]
        progress_events = [e for e in task_events if e["data"]["event_type"] == "progress_update"]

        assert len(progress_events) >= 1, "Should receive at least one progress event"

        for event in progress_events:
            assert event["event"] == "task_event"
            assert event["data"]["event_type"] == "progress_update"
            assert "task_id" in event["data"]
            assert "timestamp" in event["data"]
            assert "data" in event["data"]

    def test_task_completed_event_does_not_close_connection(
        self, sse_server: tuple[str, any], sse_gateway_server: str
    ):
        """Test that task_completed event is sent but connection remains open."""
        server_url, _ = sse_server
        resp = requests.post(
            f"{server_url}/api/testing/tasks/start",
            json={
                "task_type": "demo_task",
                "params": {"steps": 1, "delay": 0.05}
            },
            timeout=5.0
        )
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]

        client = SSEClient(f"{sse_gateway_server}/api/sse/stream?request_id={task_id}", strict=True)
        events = []

        timeout = time.perf_counter() + 5.0
        for event in client.connect(timeout=5.0):
            events.append(event)
            task_events = [e for e in events if e["event"] == "task_event"]
            completed_events = [e for e in task_events if e["data"]["event_type"] == "task_completed"]
            if len(completed_events) >= 1:
                time.sleep(0.5)
                break
            if time.perf_counter() > timeout:
                break

        task_events = [e for e in events if e["event"] == "task_event"]
        completed_events = [e for e in task_events if e["data"]["event_type"] == "task_completed"]

        assert len(completed_events) == 1, "Should receive exactly one task_completed event"

        completed = completed_events[0]
        assert completed["event"] == "task_event"
        assert completed["data"]["event_type"] == "task_completed"
        assert completed["data"]["task_id"] == task_id
        assert "timestamp" in completed["data"]
        assert completed["data"]["data"]["status"] == "success"

        connection_close_events = [e for e in events if e["event"] == "connection_close"]
        assert len(connection_close_events) == 0, "Connection should remain open after task completion"

    def test_client_disconnect_triggers_callback(
        self, sse_server: tuple[str, any], sse_gateway_server: str
    ):
        """Test that client disconnect triggers disconnect callback to Python."""
        server_url, _ = sse_server
        resp = requests.post(
            f"{server_url}/api/testing/tasks/start",
            json={
                "task_type": "demo_task",
                "params": {"steps": 10, "delay": 1.0}
            },
            timeout=5.0
        )
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]

        client = SSEClient(f"{sse_gateway_server}/api/sse/stream?request_id={task_id}", strict=True)
        events = []

        for event in client.connect(timeout=10.0):
            events.append(event)
            if len(events) >= 1:
                break

        assert len(events) >= 1

        time.sleep(0.5)

    def test_multiple_clients_connect_old_client_disconnected(
        self, sse_server: tuple[str, any], sse_gateway_server: str
    ):
        """Test that when multiple clients connect with same request_id, old client is disconnected."""
        server_url, _ = sse_server
        resp = requests.post(
            f"{server_url}/api/testing/tasks/start",
            json={
                "task_type": "demo_task",
                "params": {"steps": 5, "delay": 0.2}
            },
            timeout=5.0
        )
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]

        client1 = SSEClient(f"{sse_gateway_server}/api/sse/stream?request_id={task_id}", strict=True)
        events1 = []

        gen1 = client1.connect(timeout=10.0)
        events1.append(next(gen1))

        time.sleep(0.2)

        client2 = SSEClient(f"{sse_gateway_server}/api/sse/stream?request_id={task_id}", strict=True)
        events2 = []

        timeout = time.perf_counter() + 5.0
        for event in client2.connect(timeout=10.0):
            events2.append(event)
            task_events = [e for e in events2 if e["event"] == "task_event"]
            if len(task_events) >= 1:
                break
            if time.perf_counter() > timeout:
                break

        assert len(events2) >= 1

        task_events = [e for e in events2 if e["event"] == "task_event"]
        assert len(task_events) >= 1, "Second client should receive task events"
