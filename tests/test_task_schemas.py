"""Tests for task schemas."""

from datetime import datetime, UTC

import pytest
from pydantic import ValidationError


class TestTaskStatus:
    """Test TaskStatus enum."""

    def test_task_status_values(self) -> None:
        """Test TaskStatus enum values."""
        from common.tasks.schemas import TaskStatus

        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.RUNNING == "running"
        assert TaskStatus.COMPLETED == "completed"
        assert TaskStatus.FAILED == "failed"
        assert TaskStatus.CANCELLED == "cancelled"

    def test_task_status_all_values_distinct(self) -> None:
        """Test all TaskStatus values are distinct."""
        from common.tasks.schemas import TaskStatus

        values = [status.value for status in TaskStatus]
        assert len(values) == len(set(values))


class TestTaskInfo:
    """Test TaskInfo schema."""

    def test_task_info_minimal(self) -> None:
        """Test TaskInfo with minimal required fields."""
        from common.tasks.schemas import TaskInfo, TaskStatus

        start_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)

        info = TaskInfo(
            task_id="test-task-123",
            status=TaskStatus.PENDING,
            start_time=start_time
        )

        assert info.task_id == "test-task-123"
        assert info.status == TaskStatus.PENDING
        assert info.start_time == start_time
        assert info.end_time is None
        assert info.result is None
        assert info.error is None

    def test_task_info_completed(self) -> None:
        """Test TaskInfo for completed task."""
        from common.tasks.schemas import TaskInfo, TaskStatus

        start_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
        end_time = datetime(2023, 1, 1, 12, 1, 0, tzinfo=UTC)
        result = {"success": True, "processed": 100}

        info = TaskInfo(
            task_id="test-task-123",
            status=TaskStatus.COMPLETED,
            start_time=start_time,
            end_time=end_time,
            result=result
        )

        assert info.status == TaskStatus.COMPLETED
        assert info.end_time == end_time
        assert info.result == result
        assert info.error is None

    def test_task_info_failed(self) -> None:
        """Test TaskInfo for failed task."""
        from common.tasks.schemas import TaskInfo, TaskStatus

        start_time = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
        end_time = datetime(2023, 1, 1, 12, 0, 30, tzinfo=UTC)

        info = TaskInfo(
            task_id="test-task-123",
            status=TaskStatus.FAILED,
            start_time=start_time,
            end_time=end_time,
            error="Database connection failed"
        )

        assert info.status == TaskStatus.FAILED
        assert info.error == "Database connection failed"
        assert info.result is None


class TestTaskStartResponse:
    """Test TaskStartResponse schema."""

    def test_task_start_response(self) -> None:
        """Test TaskStartResponse creation and validation."""
        from common.tasks.schemas import TaskStartResponse, TaskStatus

        response = TaskStartResponse(
            task_id="test-task-123",
            status=TaskStatus.PENDING
        )

        assert response.task_id == "test-task-123"
        assert response.status == TaskStatus.PENDING

    def test_task_start_response_serialization(self) -> None:
        """Test TaskStartResponse serialization."""
        from common.tasks.schemas import TaskStartResponse, TaskStatus

        response = TaskStartResponse(
            task_id="test-task-123",
            status=TaskStatus.PENDING
        )

        data = response.model_dump()

        assert data["task_id"] == "test-task-123"
        assert data["status"] == "pending"
