"""Tests for task schemas."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.schemas.task_schema import (
    TaskEvent,
    TaskEventType,
    TaskInfo,
    TaskProgressUpdate,
    TaskStartResponse,
    TaskStatus,
)


class TestTaskStatus:
    """Test TaskStatus enum."""

    def test_task_status_values(self):
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.RUNNING == "running"
        assert TaskStatus.COMPLETED == "completed"
        assert TaskStatus.FAILED == "failed"
        assert TaskStatus.CANCELLED == "cancelled"


class TestTaskEventType:
    """Test TaskEventType enum."""

    def test_task_event_type_values(self):
        assert TaskEventType.TASK_STARTED == "task_started"
        assert TaskEventType.PROGRESS_UPDATE == "progress_update"
        assert TaskEventType.TASK_COMPLETED == "task_completed"
        assert TaskEventType.TASK_FAILED == "task_failed"


class TestTaskProgressUpdate:
    """Test TaskProgressUpdate schema."""

    def test_progress_update_with_text_and_value(self):
        update = TaskProgressUpdate(text="Processing data...", value=0.5)
        assert update.text == "Processing data..."
        assert update.value == 0.5

    def test_progress_update_with_both(self):
        update = TaskProgressUpdate(text="75% complete", value=0.75)
        assert update.text == "75% complete"
        assert update.value == 0.75

    def test_progress_update_missing_text(self):
        with pytest.raises(ValidationError):
            TaskProgressUpdate(value=0.5)

    def test_progress_update_missing_value(self):
        with pytest.raises(ValidationError):
            TaskProgressUpdate(text="Processing...")

    def test_progress_value_validation(self):
        TaskProgressUpdate(text="Starting", value=0.0)
        TaskProgressUpdate(text="Half done", value=0.5)
        TaskProgressUpdate(text="Complete", value=1.0)

        with pytest.raises(ValidationError):
            TaskProgressUpdate(text="Invalid", value=-0.1)

        with pytest.raises(ValidationError):
            TaskProgressUpdate(text="Invalid", value=1.1)


class TestTaskEvent:
    """Test TaskEvent schema."""

    def test_task_event_basic(self):
        event = TaskEvent(
            event_type=TaskEventType.TASK_STARTED,
            task_id="test-task-123",
        )
        assert event.event_type == TaskEventType.TASK_STARTED
        assert event.task_id == "test-task-123"
        assert isinstance(event.timestamp, datetime)
        assert event.data is None

    def test_task_event_with_data(self):
        data = {"message": "Task completed successfully", "result": 42}
        event = TaskEvent(
            event_type=TaskEventType.TASK_COMPLETED,
            task_id="test-task-123",
            data=data,
        )
        assert event.data == data

    def test_task_event_with_custom_timestamp(self):
        custom_time = datetime(2023, 1, 1, 12, 0, 0)
        event = TaskEvent(
            event_type=TaskEventType.PROGRESS_UPDATE,
            task_id="test-task-123",
            timestamp=custom_time,
        )
        assert event.timestamp == custom_time

    def test_task_event_serialization(self):
        event = TaskEvent(
            event_type=TaskEventType.TASK_FAILED,
            task_id="test-task-123",
            data={"error": "Something went wrong"},
        )
        data = event.model_dump()
        assert data["event_type"] == "task_failed"
        assert data["task_id"] == "test-task-123"
        assert data["data"]["error"] == "Something went wrong"
        assert "timestamp" in data


class TestTaskInfo:
    """Test TaskInfo schema."""

    def test_task_info_minimal(self):
        start_time = datetime(2023, 1, 1, 12, 0, 0)
        info = TaskInfo(
            task_id="test-task-123",
            status=TaskStatus.PENDING,
            start_time=start_time,
        )
        assert info.task_id == "test-task-123"
        assert info.status == TaskStatus.PENDING
        assert info.end_time is None
        assert info.result is None
        assert info.error is None

    def test_task_info_completed(self):
        start_time = datetime(2023, 1, 1, 12, 0, 0)
        end_time = datetime(2023, 1, 1, 12, 1, 0)
        result = {"success": True, "processed": 100}
        info = TaskInfo(
            task_id="test-task-123",
            status=TaskStatus.COMPLETED,
            start_time=start_time,
            end_time=end_time,
            result=result,
        )
        assert info.status == TaskStatus.COMPLETED
        assert info.result == result
        assert info.error is None

    def test_task_info_failed(self):
        start_time = datetime(2023, 1, 1, 12, 0, 0)
        end_time = datetime(2023, 1, 1, 12, 0, 30)
        info = TaskInfo(
            task_id="test-task-123",
            status=TaskStatus.FAILED,
            start_time=start_time,
            end_time=end_time,
            error="Database connection failed",
        )
        assert info.status == TaskStatus.FAILED
        assert info.error == "Database connection failed"

    def test_task_info_serialization(self):
        start_time = datetime(2023, 1, 1, 12, 0, 0)
        info = TaskInfo(
            task_id="test-task-123",
            status=TaskStatus.RUNNING,
            start_time=start_time,
        )
        data = info.model_dump()
        assert data["task_id"] == "test-task-123"
        assert data["status"] == "running"
        assert data["end_time"] is None


class TestTaskStartResponse:
    """Test TaskStartResponse schema."""

    def test_task_start_response(self):
        response = TaskStartResponse(
            task_id="test-task-123",
            status=TaskStatus.PENDING,
        )
        assert response.task_id == "test-task-123"
        assert response.status == TaskStatus.PENDING

    def test_task_start_response_serialization(self):
        response = TaskStartResponse(
            task_id="test-task-123",
            status=TaskStatus.PENDING,
        )
        data = response.model_dump()
        assert data["task_id"] == "test-task-123"
        assert data["status"] == "pending"
