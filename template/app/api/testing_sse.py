"""Testing endpoints for SSE integration tests.

Provides endpoints to start demo tasks and trigger version events,
enabling integration tests to exercise the SSE Gateway pipeline
without requiring real domain logic.

All endpoints are guarded by reject_if_not_testing() so they are
only available when FLASK_ENV=testing.
"""

import time
from typing import Any

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, jsonify, request
from pydantic import BaseModel

from app.api.testing_guard import reject_if_not_testing
from app.services.base_task import BaseTask, ProgressHandle
from app.services.container import ServiceContainer
from app.services.frontend_version_service import FrontendVersionService
from app.services.task_service import TaskService

testing_sse_bp = Blueprint("testing_sse", __name__, url_prefix="/api/testing")


@testing_sse_bp.before_request
def _guard():
    return reject_if_not_testing()


# ---------------------------------------------------------------------------
# Demo tasks for integration testing
# ---------------------------------------------------------------------------


class _DemoTaskResult(BaseModel):
    status: str = "success"


class _DemoTask(BaseTask):
    """Simple task that sends progress updates and completes."""

    def execute(self, progress_handle: ProgressHandle, **kwargs: Any) -> BaseModel:
        steps: int = kwargs.get("steps", 3)
        delay: float = kwargs.get("delay", 0.1)

        for i in range(steps):
            if self.is_cancelled:
                return _DemoTaskResult(status="cancelled")
            progress_handle.send_progress(f"Step {i + 1}/{steps}", (i + 1) / steps)
            time.sleep(delay)

        return _DemoTaskResult(status="success")


class _FailingTask(BaseTask):
    """Task that raises an exception after a delay."""

    def execute(self, progress_handle: ProgressHandle, **kwargs: Any) -> BaseModel:
        error_message: str = kwargs.get("error_message", "Task failed")
        delay: float = kwargs.get("delay", 0.1)

        time.sleep(delay)
        raise RuntimeError(error_message)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@testing_sse_bp.route("/tasks/start", methods=["POST"])
@inject
def start_test_task(
    task_service: TaskService = Provide[ServiceContainer.task_service],
) -> tuple[Any, int]:
    """Start a demo or failing task for integration testing.

    Request body:
        task_type: "demo_task" or "failing_task"
        params: dict of task parameters (steps, delay, error_message)
    """
    data = request.get_json(silent=True) or {}
    task_type = data.get("task_type", "demo_task")
    params = data.get("params", {})

    if task_type == "demo_task":
        task = _DemoTask()
    elif task_type == "failing_task":
        task = _FailingTask()
    else:
        return jsonify({"error": f"Unknown task_type: {task_type}"}), 400

    result = task_service.start_task(task, **params)
    return jsonify({"task_id": result.task_id}), 200


@testing_sse_bp.route("/deployments/version", methods=["POST"])
@inject
def trigger_version_event(
    frontend_version_service: FrontendVersionService = Provide[
        ServiceContainer.frontend_version_service
    ],
) -> tuple[Any, int]:
    """Trigger a version event for integration testing.

    Request body:
        request_id: client request ID for pending version storage
        version: version string to broadcast
        changelog: optional changelog text
    """
    data = request.get_json(silent=True) or {}
    request_id = data.get("request_id", "")
    version = data.get("version", "test-1.0.0")
    changelog = data.get("changelog")

    if not request_id:
        return jsonify({"error": "request_id is required"}), 400

    frontend_version_service.queue_version_event(
        request_id=request_id,
        version=version,
        changelog=changelog,
    )

    return jsonify({"status": "accepted"}), 202
