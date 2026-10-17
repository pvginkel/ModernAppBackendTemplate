"""Task status, cancel, and remove endpoints.

Every app using the task system needs these. They only talk to TaskService
(pure infrastructure) and are template-owned.

A task belongs to the subject that started it (``TaskInfo.subject``), and
only that caller can read, cancel or remove it. With OIDC disabled, the local
admin owns every task. With OIDC enabled, a task started without a subject (a
system task) belongs to nobody. To any other caller the task does not exist:
the endpoints answer 404, as for an unknown id.
"""

import logging
from typing import Any

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, jsonify

from app.config import Settings
from app.schemas.task_schema import TaskInfo
from app.services.container import ServiceContainer
from app.services.task_service import TaskService
from app.utils.auth import LOCAL_USER_SUBJECT, get_auth_context

logger = logging.getLogger(__name__)

tasks_bp = Blueprint("tasks", __name__, url_prefix="/tasks")


def _caller_owns(task_info: TaskInfo, settings: Settings) -> bool:
    """Whether the caller may read, cancel or remove the task."""
    auth_context = get_auth_context()
    if auth_context is None:
        return False
    if not settings.oidc_enabled and auth_context.subject == LOCAL_USER_SUBJECT:
        return True
    return task_info.subject is not None and task_info.subject == auth_context.subject


def _find_caller_task(task_service: TaskService, task_id: str, settings: Settings) -> TaskInfo | None:
    """Return the task, or None when it doesn't exist or the caller doesn't own it."""
    task_info = task_service.get_task_status(task_id)
    if task_info is None:
        return None
    if not _caller_owns(task_info, settings):
        logger.info("Task %s refused: the caller does not own it", task_id)
        return None
    return task_info


@tasks_bp.route("/<task_id>/status", methods=["GET"])
@inject
def get_task_status(
    task_id: str,
    task_service: TaskService = Provide[ServiceContainer.task_service],
    settings: Settings = Provide[ServiceContainer.config],
) -> Any:
    """Get current status of a task."""
    task_info = _find_caller_task(task_service, task_id, settings)
    if not task_info:
        return jsonify({"error": "Task not found"}), 404

    return jsonify(task_info.model_dump())


@tasks_bp.route("/<task_id>/cancel", methods=["POST"])
@inject
def cancel_task(
    task_id: str,
    task_service: TaskService = Provide[ServiceContainer.task_service],
    settings: Settings = Provide[ServiceContainer.config],
) -> Any:
    """Cancel a running task."""
    if _find_caller_task(task_service, task_id, settings) is None:
        return jsonify({"error": "Task not found or cannot be cancelled"}), 404

    success = task_service.cancel_task(task_id)
    if not success:
        return jsonify({"error": "Task not found or cannot be cancelled"}), 404

    return jsonify({"success": True, "message": "Task cancellation requested"})


@tasks_bp.route("/<task_id>", methods=["DELETE"])
@inject
def remove_task(
    task_id: str,
    task_service: TaskService = Provide[ServiceContainer.task_service],
    settings: Settings = Provide[ServiceContainer.config],
) -> Any:
    """Remove a completed task from registry."""
    if _find_caller_task(task_service, task_id, settings) is None:
        return jsonify({"error": "Task not found or not completed"}), 404

    success = task_service.remove_completed_task(task_id)
    if not success:
        return jsonify({"error": "Task not found or not completed"}), 404

    return jsonify({"success": True, "message": "Task removed from registry"})
