"""Tests for TaskService."""

import time
from datetime import UTC

import pytest

from app.schemas.task_schema import TaskEventType, TaskStatus
from app.services.sse_connection_manager import (
    BROADCAST_TARGET,
    LOCAL_ADMIN_TARGET,
    Target,
)
from app.services.task_service import TaskProgressHandle, TaskService, task_event_target
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

    def test_task_events_go_to_the_caller(self, task_service, mock_sse_connection_manager):
        """When caller_subject is set, every task event targets that subject."""
        task = DemoTask()
        task_service.start_task(task, caller_subject="alice", steps=1, delay=0.05)
        time.sleep(0.3)

        calls = mock_sse_connection_manager.send_event.call_args_list
        assert len(calls) > 0

        for call in calls:
            assert call.args[0] == Target.subject("alice"), (
                f"Expected Target.subject('alice'), got {call.args[0]!r}"
            )

    def test_task_events_never_broadcast_when_no_subject(
        self, task_service, mock_sse_connection_manager
    ):
        """When caller_subject is None, every task event goes to the local admin only.

        With OIDC on nobody is the local admin, so the events reach nobody.
        """
        task = DemoTask()
        task_service.start_task(task, steps=1, delay=0.05)
        time.sleep(0.3)

        calls = mock_sse_connection_manager.send_event.call_args_list
        assert len(calls) > 0

        for call in calls:
            assert call.args[0] == LOCAL_ADMIN_TARGET, (
                f"Expected LOCAL_ADMIN_TARGET, got {call.args[0]!r}"
            )
            assert call.args[0] != BROADCAST_TARGET

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
        handle = TaskProgressHandle(
            "test-task-id", mock_sse_connection_manager, Target.subject("alice")
        )

        handle.send_progress_text("Text update")
        handle.send_progress_value(0.5)
        handle.send_progress("Combined update", 0.75)

        assert mock_sse_connection_manager.send_event.call_count == 3

        calls = mock_sse_connection_manager.send_event.call_args_list

        text_call = calls[0]
        target = text_call.args[0]
        text_event = text_call.args[1]
        assert target == Target.subject("alice")
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

    def test_progress_handle_passes_its_target(self):
        from unittest.mock import Mock

        mock_sse_connection_manager = Mock()
        handle = TaskProgressHandle(
            "test-task-id", mock_sse_connection_manager, Target.subject("alice")
        )

        handle.send_progress("Step 1", 0.5)

        call = mock_sse_connection_manager.send_event.call_args
        assert call.args[0] == Target.subject("alice")
        assert call.kwargs == {"event_name": "task_event", "service_type": "task"}

    def test_progress_handle_has_no_default_target(self):
        """A progress handle never falls back to a broadcast: the target is required."""
        from unittest.mock import Mock

        with pytest.raises(TypeError):
            TaskProgressHandle("test-task-id", Mock())  # type: ignore[call-arg]


class TestTaskEventTarget:
    """Where a task's events go."""

    def test_caller_subject_targets_the_caller(self):
        assert task_event_target("alice") == Target.subject("alice")

    def test_no_subject_targets_the_local_admin_only(self):
        assert task_event_target(None) == LOCAL_ADMIN_TARGET

    def test_local_user_subject_is_a_subject_target(self):
        """The local admin's own tasks carry its subject, like anyone's."""
        assert task_event_target("local-user") == Target.subject("local-user")
