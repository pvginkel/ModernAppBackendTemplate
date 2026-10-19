"""Tests for the testing SSE endpoints' answers (app/api/testing_sse.py).

Errors raise and answer with the error envelope: an unknown connection is a
404, a connection that doesn't take the event a 502. The manager is a fresh
one, its connections made with ``on_connect`` as a stream makes them; the
integration tests run these endpoints against real streams.
"""

from collections.abc import Generator
from unittest.mock import Mock

import pytest
from dependency_injector import providers
from flask import Flask
from flask.testing import FlaskClient

from app.schemas.task_schema import TaskStartResponse, TaskStatus
from app.services.sse_connection_manager import SSEConnectionManager
from app.services.task_service import TaskService
from tests.testing_utils import StubLifecycleCoordinator

_TASK_EVENT = {
    "request_id": "req-1",
    "task_id": "task-1",
    "event_type": "progress_update",
    "data": {"text": "Working", "value": 0.5},
}


@pytest.fixture
def manager(app: Flask) -> Generator[SSEConnectionManager]:
    """A fresh connection manager in the app; session_app_isolation undoes the override."""
    manager = SSEConnectionManager(StubLifecycleCoordinator(), queue_max_frames=1)
    app.container.sse_connection_manager.override(providers.Object(manager))
    yield manager


class TestTaskEvent:
    def test_delivered(self, client: FlaskClient, manager: SSEConnectionManager):
        connection = manager.on_connect("req-1", "alice")

        response = client.post("/api/testing/sse/task-event", json=_TASK_EVENT)

        assert response.status_code == 200
        assert response.get_json() == {
            "request_id": "req-1",
            "task_id": "task-1",
            "event_type": "progress_update",
            "delivered": True,
        }
        frame = connection.next_frame(0)
        assert frame is not None
        assert frame.startswith('data: {"type":"task_event","payload":')

    def test_unknown_connection_is_404(
        self, client: FlaskClient, manager: SSEConnectionManager
    ):
        response = client.post("/api/testing/sse/task-event", json=_TASK_EVENT)

        assert response.status_code == 404
        body = response.get_json()
        assert body["code"] == "RECORD_NOT_FOUND"
        assert body["error"] == "SSE connection req-1 was not found"

    def test_a_full_queue_is_502(self, client: FlaskClient, manager: SSEConnectionManager):
        """The queue holds one frame; a second event finds it full and closes the connection."""
        manager.on_connect("req-1", "alice")
        assert client.post("/api/testing/sse/task-event", json=_TASK_EVENT).status_code == 200

        response = client.post("/api/testing/sse/task-event", json=_TASK_EVENT)

        assert response.status_code == 502
        body = response.get_json()
        assert body["code"] == "EXTERNAL_SERVICE_ERROR"
        assert body["error"] == (
            "Cannot send the task event to SSE connection req-1 because external "
            "service failed: the connection did not take it"
        )

    def test_invalid_event_type_is_400(self, client: FlaskClient, manager: SSEConnectionManager):
        response = client.post(
            "/api/testing/sse/task-event", json={**_TASK_EVENT, "event_type": "nope"}
        )

        assert response.status_code == 400


class TestStartTask:
    @pytest.mark.parametrize(
        ("task_type", "task_class"), [("demo_task", "_DemoTask"), ("failing_task", "_FailingTask")]
    )
    def test_starts_each_task_type(
        self, client: FlaskClient, app: Flask, task_type: str, task_class: str
    ):
        task_service = Mock(spec=TaskService)
        task_service.start_task.return_value = TaskStartResponse(
            task_id="task-1", status=TaskStatus.PENDING
        )
        app.container.task_service.override(providers.Object(task_service))

        response = client.post(
            "/api/testing/tasks/start",
            json={"task_type": task_type, "params": {"steps": 1}},
        )

        assert response.status_code == 200
        assert response.get_json() == {"task_id": "task-1", "status": "started"}
        task, = task_service.start_task.call_args.args
        assert type(task).__name__ == task_class
        assert task_service.start_task.call_args.kwargs == {"caller_subject": None, "steps": 1}

    def test_unknown_task_type_is_400(self, client: FlaskClient):
        response = client.post("/api/testing/tasks/start", json={"task_type": "nope"})

        assert response.status_code == 400
