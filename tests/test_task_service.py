"""Tests for task service."""


def test_task_service_exists(app):
    """Container provides a task service."""
    task_service = app.container.task_service()
    assert task_service is not None


def test_task_service_has_executor(app):
    """Task service initializes with a thread pool executor."""
    task_service = app.container.task_service()
    assert task_service._executor is not None
