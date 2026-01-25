"""Tests for the task service."""

import time
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel


class TestTaskService:
    """Tests for TaskService functionality."""

    def test_start_task_returns_task_id(self, app_context: Any, container: Any) -> None:
        """Test starting a task returns a task ID."""
        from common.tasks.base_task import BaseTask, ProgressHandle
        from common.tasks.schemas import TaskStatus

        class SimpleTask(BaseTask):
            def execute(self, progress: ProgressHandle, **kwargs: Any) -> BaseModel | None:
                return None

        task_service = container.task_service()
        result = task_service.start_task(SimpleTask())

        assert result.task_id is not None
        assert result.status == TaskStatus.PENDING

    def test_task_executes_and_completes(self, app_context: Any, container: Any) -> None:
        """Test that a started task executes and completes."""
        from common.tasks.base_task import BaseTask, ProgressHandle
        from common.tasks.schemas import TaskStatus

        class CompletingTask(BaseTask):
            def execute(self, progress: ProgressHandle, **kwargs: Any) -> BaseModel | None:
                progress.send_progress("Working...", 0.5)
                return None

        task_service = container.task_service()
        result = task_service.start_task(CompletingTask())

        # Wait for task to complete
        for _ in range(10):
            status = task_service.get_task_status(result.task_id)
            if status and status.status == TaskStatus.COMPLETED:
                break
            time.sleep(0.1)

        status = task_service.get_task_status(result.task_id)
        assert status is not None
        assert status.status == TaskStatus.COMPLETED

    def test_task_failure_is_recorded(self, app_context: Any, container: Any) -> None:
        """Test that task failures are recorded."""
        from common.tasks.base_task import BaseTask, ProgressHandle
        from common.tasks.schemas import TaskStatus

        class FailingTask(BaseTask):
            def execute(self, progress: ProgressHandle, **kwargs: Any) -> BaseModel | None:
                raise ValueError("Task failed intentionally")

        task_service = container.task_service()
        result = task_service.start_task(FailingTask())

        # Wait for task to fail
        for _ in range(10):
            status = task_service.get_task_status(result.task_id)
            if status and status.status == TaskStatus.FAILED:
                break
            time.sleep(0.1)

        status = task_service.get_task_status(result.task_id)
        assert status is not None
        assert status.status == TaskStatus.FAILED
        assert "Task failed intentionally" in (status.error or "")

    def test_cancel_task(self, app_context: Any, container: Any) -> None:
        """Test cancelling a task."""
        from common.tasks.base_task import BaseTask, ProgressHandle
        from common.tasks.schemas import TaskStatus

        class LongRunningTask(BaseTask):
            def execute(self, progress: ProgressHandle, **kwargs: Any) -> BaseModel | None:
                for i in range(100):
                    if self._cancelled:
                        return None
                    time.sleep(0.1)
                return None

        task_service = container.task_service()
        result = task_service.start_task(LongRunningTask())

        # Wait for task to actually start running
        for _ in range(20):
            status = task_service.get_task_status(result.task_id)
            if status and status.status == TaskStatus.RUNNING:
                break
            time.sleep(0.05)

        # Cancel the task
        cancelled = task_service.cancel_task(result.task_id)
        assert cancelled is True

        # Verify status
        status = task_service.get_task_status(result.task_id)
        assert status is not None
        assert status.status == TaskStatus.CANCELLED

    def test_get_nonexistent_task_returns_none(
        self, app_context: Any, container: Any
    ) -> None:
        """Test getting status of nonexistent task returns None."""
        task_service = container.task_service()

        status = task_service.get_task_status("nonexistent-task-id")

        assert status is None

    def test_task_with_result(self, app_context: Any, container: Any) -> None:
        """Test task that returns a result."""
        from common.tasks.base_task import BaseTask, ProgressHandle
        from common.tasks.schemas import TaskStatus

        class ResultModel(BaseModel):
            message: str
            count: int

        class TaskWithResult(BaseTask):
            def execute(self, progress: ProgressHandle, **kwargs: Any) -> ResultModel:
                return ResultModel(message="Done", count=42)

        task_service = container.task_service()
        result = task_service.start_task(TaskWithResult())

        # Wait for completion
        for _ in range(10):
            status = task_service.get_task_status(result.task_id)
            if status and status.status == TaskStatus.COMPLETED:
                break
            time.sleep(0.1)

        status = task_service.get_task_status(result.task_id)
        assert status is not None
        assert status.status == TaskStatus.COMPLETED
        assert status.result is not None
        assert status.result["message"] == "Done"
        assert status.result["count"] == 42


class TestNullBroadcaster:
    """Tests for NullBroadcaster (no-op implementation)."""

    def test_null_broadcaster_accepts_events(self) -> None:
        """Test NullBroadcaster silently accepts broadcast calls."""
        from common.sse.null_broadcaster import NullBroadcaster

        broadcaster = NullBroadcaster()

        # These should not raise
        broadcaster.broadcast_task_event("task-123", "progress", {"value": 0.5})
        broadcaster.broadcast_task_event("task-456", "completed", {"result": "ok"})

    def test_tasks_work_with_null_broadcaster(
        self, app_context: Any, container: Any
    ) -> None:
        """Test that tasks work correctly with NullBroadcaster."""
        # This test verifies the decoupling - tasks should work even
        # when SSE is not available (using NullBroadcaster)
        from common.tasks.base_task import BaseTask, ProgressHandle
        from common.tasks.schemas import TaskStatus

        class TaskWithProgress(BaseTask):
            def execute(self, progress: ProgressHandle, **kwargs: Any) -> BaseModel | None:
                progress.send_progress("Step 1", 0.25)
                progress.send_progress("Step 2", 0.50)
                progress.send_progress("Step 3", 0.75)
                progress.send_progress("Done", 1.0)
                return None

        task_service = container.task_service()
        result = task_service.start_task(TaskWithProgress())

        # Wait for completion
        for _ in range(10):
            status = task_service.get_task_status(result.task_id)
            if status and status.status == TaskStatus.COMPLETED:
                break
            time.sleep(0.1)

        status = task_service.get_task_status(result.task_id)
        assert status is not None
        assert status.status == TaskStatus.COMPLETED
