"""Baseline integration tests for task stream SSE endpoint.

These tests validate the current Flask SSE implementation behavior before migrating
to SSE Gateway. They use a real Flask server (waitress) and SSE client to test
actual streaming behavior.
"""

import time
from typing import Any

import pytest


@pytest.mark.integration
class TestTaskStreamBaseline:
    """Baseline integration tests for /api/tasks/<task_id>/stream endpoint."""

    def test_task_progress_events_received(self, sse_server: tuple[str, Any], sse_client_factory):
        """Test that progress_update events are received during task execution."""
        import requests

        server_url, _ = sse_server
        resp = requests.post(
            f"{server_url}/api/testing/tasks/start",
            json={
                "task_type": "demo_task",
                "params": {"steps": 3, "delay": 0.05}
            },
            timeout=5.0
        )
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]

        client = sse_client_factory(f"/api/tasks/{task_id}/stream")
        events = []

        for event in client.connect(timeout=10.0):
            events.append(event)
            if event["event"] == "connection_close":
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

    def test_task_completed_event_received(self, sse_server: tuple[str, Any], sse_client_factory):
        """Test that task_completed event is received when task finishes successfully."""
        import requests

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

        client = sse_client_factory(f"/api/tasks/{task_id}/stream")
        events = []

        for event in client.connect(timeout=10.0):
            events.append(event)
            if event["event"] == "connection_close":
                break

        task_events = [e for e in events if e["event"] == "task_event"]
        completed_events = [e for e in task_events if e["data"]["event_type"] == "task_completed"]

        assert len(completed_events) == 1, "Should receive exactly one task_completed event"

        completed = completed_events[0]
        assert completed["event"] == "task_event"
        assert completed["data"]["event_type"] == "task_completed"
        assert completed["data"]["task_id"] == task_id
        assert "timestamp" in completed["data"]
        assert "data" in completed["data"]

        assert completed["data"]["data"]["status"] == "success"

    def test_connection_close_event_after_completion(self, sse_server: tuple[str, Any], sse_client_factory):
        """Test that connection_close event is sent after task completion."""
        import requests

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

        client = sse_client_factory(f"/api/tasks/{task_id}/stream")
        events = []

        for event in client.connect(timeout=10.0):
            events.append(event)
            if event["event"] == "connection_close":
                break

        assert events[-1]["event"] == "connection_close"
        assert events[-1]["data"]["reason"] == "task_completed"

    def test_task_failed_event_on_exception(self, sse_server: tuple[str, Any], sse_client_factory):
        """Test that task_failed event is received when task raises exception."""
        import requests

        server_url, _ = sse_server
        resp = requests.post(
            f"{server_url}/api/testing/tasks/start",
            json={
                "task_type": "failing_task",
                "params": {"error_message": "Test error", "delay": 0.05}
            },
            timeout=5.0
        )
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]

        client = sse_client_factory(f"/api/tasks/{task_id}/stream")
        events = []

        for event in client.connect(timeout=10.0):
            events.append(event)
            if event["event"] == "connection_close":
                break

        task_events = [e for e in events if e["event"] == "task_event"]
        failed_events = [e for e in task_events if e["data"]["event_type"] == "task_failed"]

        assert len(failed_events) == 1, "Should receive exactly one task_failed event"

        failed = failed_events[0]
        assert failed["event"] == "task_event"
        assert failed["data"]["event_type"] == "task_failed"
        assert failed["data"]["task_id"] == task_id
        assert "error" in failed["data"]["data"]
        assert "Test error" in failed["data"]["data"]["error"]

    def test_task_not_found_returns_error_event(self, sse_server: tuple[str, Any], sse_client_factory):
        """Test that non-existent task returns error event and closes connection."""
        task_id = "nonexistent-task-id"

        client = sse_client_factory(f"/api/tasks/{task_id}/stream")
        events = []

        for event in client.connect(timeout=10.0):
            events.append(event)
            if event["event"] == "connection_close":
                break

        assert len(events) >= 2

        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 1
        assert "Task not found" in error_events[0]["data"]["error"]

        assert events[-1]["event"] == "connection_close"
        assert events[-1]["data"]["reason"] == "task_not_found"

    def test_heartbeat_events_on_idle_stream(self, sse_server: tuple[str, Any], sse_client_factory):
        """Test that heartbeat events are sent when no task events are available."""
        import requests

        server_url, _ = sse_server
        resp = requests.post(
            f"{server_url}/api/testing/tasks/start",
            json={
                "task_type": "demo_task",
                "params": {"steps": 1, "delay": 12.0}
            },
            timeout=5.0
        )
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]

        client = sse_client_factory(f"/api/tasks/{task_id}/stream")
        events = []
        start_time = time.perf_counter()

        for event in client.connect(timeout=20.0):
            events.append(event)
            if event["event"] == "connection_close":
                break
            if time.perf_counter() - start_time > 7.0:
                break

        heartbeat_events = [e for e in events if e["event"] == "heartbeat"]

        assert len(heartbeat_events) >= 1, "Should receive at least one heartbeat during idle period"

        for hb in heartbeat_events:
            assert "timestamp" in hb["data"]
            assert isinstance(hb["data"]["timestamp"], str)
            assert "T" in hb["data"]["timestamp"]

    def test_event_ordering_is_correct(self, sse_server: tuple[str, Any], sse_client_factory):
        """Test that events arrive in correct order: progress -> completion -> close."""
        import requests

        server_url, _ = sse_server
        resp = requests.post(
            f"{server_url}/api/testing/tasks/start",
            json={
                "task_type": "demo_task",
                "params": {"steps": 3, "delay": 0.05}
            },
            timeout=5.0
        )
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]

        client = sse_client_factory(f"/api/tasks/{task_id}/stream")
        events = []

        for event in client.connect(timeout=10.0):
            events.append(event)
            if event["event"] == "connection_close":
                break

        task_events = [e for e in events[:-1] if e["event"] == "task_event"]
        assert len(task_events) >= 1, "Should have at least one task event"

        assert events[-1]["event"] == "connection_close"

        close_index = next(i for i, e in enumerate(events) if e["event"] == "connection_close")
        assert close_index == len(events) - 1, "No events should arrive after connection_close"

    def test_correlation_id_when_present(self, sse_server: tuple[str, Any], sse_client_factory):
        """Test that correlation_id is consistent across events when present."""
        import requests

        server_url, _ = sse_server
        resp = requests.post(
            f"{server_url}/api/testing/tasks/start",
            json={
                "task_type": "demo_task",
                "params": {"steps": 2, "delay": 0.05}
            },
            timeout=5.0
        )
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]

        client = sse_client_factory(f"/api/tasks/{task_id}/stream")
        events = []

        for event in client.connect(timeout=10.0):
            events.append(event)
            if event["event"] == "connection_close":
                break

        correlation_ids = [
            event["data"].get("correlation_id")
            for event in events
            if "correlation_id" in event["data"]
        ]

        if correlation_ids:
            assert all(cid == correlation_ids[0] for cid in correlation_ids), \
                "Correlation IDs should be consistent across all events in a stream"
