"""Tests for TaskService."""

import time
from datetime import UTC

import pytest

from app.schemas.task_schema import TaskEventType, TaskStatus
from app.services.task_service import TaskProgressHandle, TaskService
from tests.test_tasks.test_task import DemoTask, FailingTask, LongRunningTask
from tests.testing_utils import StubLifecycleCoordinator


class TestTaskService:
    """Test TaskService functionality."""

    @pytest.fixture
    def mock_lifecycle_coordinator(self):
        return StubLifecycleCoordinator()

    @pytest.fixture
    def mock_sse_connection_manager(self):
        from unittest.mock import Mock

        return Mock()

    @pytest.fixture
    def task_service(self, mock_lifecycle_coordinator, mock_sse_connection_manager):
        service = TaskService(
            mock_lifecycle_coordinator,
            mock_sse_connection_manager,
            max_workers=2,
            task_timeout=10,
        )
        try:
            yield service
        finally:
            service.shutdown()

    def test_start_task(self, task_service):
        task = DemoTask()
        response = task_service.start_task(task, message="test task", steps=2, delay=0.01)
        assert response.task_id is not None
        assert response.status == TaskStatus.PENDING

        time.sleep(0.2)
        task_info = task_service.get_task_status(response.task_id)
        assert task_info is not None
        assert task_info.status == TaskStatus.COMPLETED
        assert task_info.result["status"] == "success"

    def test_get_task_status_nonexistent(self, task_service):
        assert task_service.get_task_status("nonexistent-task-id") is None

    def test_task_execution_with_failure(self, task_service):
        task = FailingTask()
        response = task_service.start_task(task, error_message="Test failure", delay=0.01)
        time.sleep(0.2)
        task_info = task_service.get_task_status(response.task_id)
        assert task_info is not None
        assert task_info.status == TaskStatus.FAILED
        assert "Test failure" in task_info.error

    def test_cancel_task(self, task_service):
        task = LongRunningTask()
        response = task_service.start_task(task, total_time=5.0, check_interval=0.02)
        time.sleep(0.2)
        assert task_service.cancel_task(response.task_id) is True
        time.sleep(0.5)
        task_info = task_service.get_task_status(response.task_id)
        assert task_info is not None
        assert task_info.status == TaskStatus.CANCELLED

    def test_cancel_nonexistent_task(self, task_service):
        assert task_service.cancel_task("nonexistent-task-id") is False

    def test_cancel_completed_task(self, task_service):
        task = DemoTask()
        response = task_service.start_task(task, steps=1, delay=0.01)
        time.sleep(0.2)
        assert task_service.cancel_task(response.task_id) is False

    def test_remove_completed_task(self, task_service):
        task = DemoTask()
        response = task_service.start_task(task, steps=1, delay=0.01)
        time.sleep(0.2)
        assert task_service.get_task_status(response.task_id).status == TaskStatus.COMPLETED
        assert task_service.remove_completed_task(response.task_id) is True
        assert task_service.get_task_status(response.task_id) is None

    def test_remove_running_task(self, task_service):
        task = LongRunningTask()
        response = task_service.start_task(task, total_time=1.0, check_interval=0.05)
        assert task_service.remove_completed_task(response.task_id) is False
        task_service.cancel_task(response.task_id)

    def test_get_task_events(self, task_service, mock_sse_connection_manager):
        task = DemoTask()
        _ = task_service.start_task(task, message="event test", steps=2, delay=0.05)
        time.sleep(0.3)
        assert mock_sse_connection_manager.send_event.called
        calls = mock_sse_connection_manager.send_event.call_args_list
        assert len(calls) > 0
        event_types = []
        for call in calls:
            event_data = call[0][1]
            if "event_type" in event_data:
                event_types.append(event_data["event_type"])
        assert TaskEventType.TASK_STARTED in event_types
        assert TaskEventType.PROGRESS_UPDATE in event_types
        assert TaskEventType.TASK_COMPLETED in event_types

    def test_concurrent_tasks(self, task_service):
        responses = []
        for i in range(3):
            task = DemoTask()
            response = task_service.start_task(task, message=f"task {i}", steps=2, delay=0.02)
            responses.append(response)
        time.sleep(0.3)
        for response in responses:
            task_info = task_service.get_task_status(response.task_id)
            assert task_info is not None
            assert task_info.status == TaskStatus.COMPLETED

    def test_task_service_shutdown(self, mock_lifecycle_coordinator, mock_sse_connection_manager):
        service = TaskService(mock_lifecycle_coordinator, mock_sse_connection_manager, max_workers=1)
        task = DemoTask()
        service.start_task(task, steps=1, delay=0.01)
        service.shutdown()
        assert len(service._tasks) == 0
        assert len(service._task_instances) == 0

    def test_cleanup_only_removes_old_completed_tasks(self, task_service):
        from datetime import datetime, timedelta

        task1 = DemoTask()
        task2 = DemoTask()
        response1 = task_service.start_task(task1, steps=1, delay=0.01)
        time.sleep(0.2)
        response2 = task_service.start_task(task2, steps=1, delay=0.01)
        time.sleep(0.2)
        assert task_service.get_task_status(response1.task_id).status == TaskStatus.COMPLETED
        assert task_service.get_task_status(response2.task_id).status == TaskStatus.COMPLETED

        with task_service._lock:
            old_time = datetime.now(UTC) - timedelta(seconds=task_service.cleanup_interval + 1)
            task_service._tasks[response1.task_id].end_time = old_time

        task_service._cleanup_completed_tasks()
        assert task_service.get_task_status(response1.task_id) is None
        assert task_service.get_task_status(response2.task_id) is not None


class TestTaskProgressHandle:
    """Test TaskProgressHandle implementation."""

    def test_progress_handle_creation(self):
        from unittest.mock import Mock

        mock_sse_connection_manager = Mock()
        handle = TaskProgressHandle("test-task-id", mock_sse_connection_manager)

        handle.send_progress_text("Text update")
        handle.send_progress_value(0.5)
        handle.send_progress("Combined update", 0.75)

        assert mock_sse_connection_manager.send_event.call_count == 3

        calls = mock_sse_connection_manager.send_event.call_args_list

        text_call = calls[0]
        request_id = text_call.args[0]
        text_event = text_call.args[1]
        assert request_id is None
        assert text_event["event_type"] == TaskEventType.PROGRESS_UPDATE
        assert text_event["task_id"] == "test-task-id"
        assert text_event["data"]["text"] == "Text update"

        value_call = calls[1]
        value_event = value_call.args[1]
        assert value_event["data"]["value"] == 0.5

        combined_call = calls[2]
        combined_event = combined_call.args[1]
        assert combined_event["data"]["text"] == "Combined update"
        assert combined_event["data"]["value"] == 0.75
