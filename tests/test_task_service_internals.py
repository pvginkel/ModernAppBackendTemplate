"""Unit tests for TaskService internals: progress, execution, cleanup, shutdown.

test_task_service.py drives tasks through the thread pool; these call the
internals synchronously, with a mock SSE connection manager, so each state
transition is checked deterministically.
"""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from app.exceptions import InvalidOperationException
from app.schemas.task_schema import TaskEvent, TaskEventType, TaskInfo, TaskStatus
from app.services.base_task import BaseTask, ProgressHandle
from app.services.task_service import (
    ACTIVE_TASKS_AT_SHUTDOWN,
    TaskProgressHandle,
    TaskService,
)
from app.utils.lifecycle_coordinator import LifecycleEvent
from tests.testing_utils import TestLifecycleCoordinator


class _Result(BaseModel):
    value: str = "ok"


class _SuccessTask(BaseTask):
    def execute(self, progress_handle: ProgressHandle, **kwargs: Any) -> BaseModel:
        progress_handle.send_progress("working", 0.5)
        return _Result()


class _FailingTask(BaseTask):
    def execute(self, progress_handle: ProgressHandle, **kwargs: Any) -> BaseModel:
        raise RuntimeError("intentional failure")


def _fire(coordinator: TestLifecycleCoordinator, event: LifecycleEvent) -> None:
    for callback in list(coordinator._notifications):
        callback(event)


def _task_info(task_id: str, status: TaskStatus, ended: datetime | None = None) -> TaskInfo:
    terminal = status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
    now = datetime.now(UTC)
    return TaskInfo(
        task_id=task_id,
        subject="user-sub",
        status=status,
        start_time=ended or now,
        end_time=(ended or now) if terminal else None,
    )


@pytest.fixture
def coordinator() -> TestLifecycleCoordinator:
    return TestLifecycleCoordinator()


@pytest.fixture
def sse() -> MagicMock:
    return MagicMock()


@pytest.fixture
def service(coordinator: TestLifecycleCoordinator, sse: MagicMock) -> Generator[TaskService]:
    svc = TaskService(coordinator, sse, max_workers=2, task_timeout=10, cleanup_interval=60)
    try:
        yield svc
    finally:
        svc.shutdown()


class TestTaskProgressHandle:
    def test_progress_never_decreases(self, sse: MagicMock):
        handle = TaskProgressHandle("task-1", sse, target_subject="u")

        handle.send_progress("step 1", 0.8)
        handle.send_progress("step 2", 0.5)

        assert handle.progress == 0.8

    def test_text_update_keeps_the_value(self, sse: MagicMock):
        handle = TaskProgressHandle("task-1", sse, target_subject="u")

        handle.send_progress("first", 0.3)
        handle.send_progress_text("second")

        assert handle.progress_text == "second"
        assert handle.progress == 0.3

    def test_value_update_keeps_the_text(self, sse: MagicMock):
        handle = TaskProgressHandle("task-1", sse, target_subject="u")

        handle.send_progress("initial", 0.1)
        handle.send_progress_value(0.9)

        assert handle.progress_text == "initial"
        assert handle.progress == 0.9

    def test_send_failure_is_swallowed(self, sse: MagicMock):
        sse.send_event.side_effect = RuntimeError("connection lost")
        handle = TaskProgressHandle("task-1", sse, target_subject="u")

        handle.send_progress("step", 0.5)

        assert handle.progress == 0.5


class TestStartTask:
    def test_start_during_shutdown_raises(self, service: TaskService):
        service._shutting_down = True

        with pytest.raises(InvalidOperationException, match="shutting down"):
            service.start_task(_SuccessTask())

    def test_start_returns_pending_and_registers(self, service: TaskService):
        response = service.start_task(_SuccessTask(), caller_subject="user-sub")

        assert response.status == TaskStatus.PENDING
        assert len(response.task_id) == 36
        assert response.task_id in service._tasks


class TestExecuteTask:
    def test_failure_sends_task_failed_last(self, service: TaskService, sse: MagicMock):
        service._tasks["t-1"] = _task_info("t-1", TaskStatus.PENDING)

        service._execute_task("t-1", _FailingTask(), {}, caller_subject="user-sub")

        assert service._tasks["t-1"].status == TaskStatus.FAILED
        assert "intentional failure" in (service._tasks["t-1"].error or "")
        assert sse.send_event.call_args_list[-1].args[1]["event_type"] == "task_failed"

    def test_cancelled_task_stays_cancelled(self, service: TaskService):
        class _SelfCancellingTask(BaseTask):
            def execute(self, progress_handle: ProgressHandle, **kwargs: Any) -> BaseModel:
                service.cancel_task("t-1")
                return _Result()

        task = _SelfCancellingTask()
        service._tasks["t-1"] = _task_info("t-1", TaskStatus.PENDING)
        service._task_instances["t-1"] = task

        service._execute_task("t-1", task, {}, caller_subject="user-sub")

        assert service._tasks["t-1"].status == TaskStatus.CANCELLED


class TestBroadcastTaskEvent:
    def test_event_is_serialised_as_json(self, service: TaskService, sse: MagicMock):
        event = TaskEvent(event_type=TaskEventType.TASK_STARTED, task_id="t-1", data=None)

        service._broadcast_task_event(event, target_subject="user-sub")

        sse.send_event.assert_called_once()
        call = sse.send_event.call_args
        assert call.kwargs["event_name"] == "task_event"
        # mode="json": the timestamp is a string on the wire.
        assert isinstance(call.args[1]["timestamp"], str)

    def test_no_connection_is_not_an_error(self, service: TaskService, sse: MagicMock):
        sse.send_event.return_value = False
        event = TaskEvent(event_type=TaskEventType.TASK_COMPLETED, task_id="t-2", data=None)

        service._broadcast_task_event(event, target_subject="user-sub")


class TestRemoveTask:
    @pytest.mark.parametrize("status", [TaskStatus.FAILED, TaskStatus.CANCELLED])
    def test_every_terminal_task_is_removable(self, service: TaskService, status: TaskStatus):
        service._tasks["t-1"] = _task_info("t-1", status)
        service._task_instances["t-1"] = MagicMock(spec=BaseTask)

        assert service.remove_completed_task("t-1") is True
        assert "t-1" not in service._tasks
        assert "t-1" not in service._task_instances


class TestCleanupCompletedTasks:
    def test_recent_completed_tasks_are_kept(self, service: TaskService):
        service.cleanup_interval = 600
        service._tasks["recent"] = _task_info(
            "recent", TaskStatus.COMPLETED, ended=datetime.now(UTC) - timedelta(seconds=10)
        )

        service._cleanup_completed_tasks()

        assert "recent" in service._tasks

    def test_running_tasks_are_never_removed(self, service: TaskService):
        service.cleanup_interval = 0
        service._tasks["running"] = _task_info(
            "running", TaskStatus.RUNNING, ended=datetime.now(UTC) - timedelta(seconds=9999)
        )

        service._cleanup_completed_tasks()

        assert "running" in service._tasks


class TestLifecycleAndShutdown:
    def test_prepare_shutdown_sets_the_flag(
        self, service: TaskService, coordinator: TestLifecycleCoordinator
    ):
        _fire(coordinator, LifecycleEvent.PREPARE_SHUTDOWN)

        assert service._shutting_down is True

    def test_prepare_shutdown_records_active_tasks(
        self, service: TaskService, coordinator: TestLifecycleCoordinator
    ):
        service._tasks["active"] = _task_info("active", TaskStatus.RUNNING)

        _fire(coordinator, LifecycleEvent.PREPARE_SHUTDOWN)

        assert ACTIVE_TASKS_AT_SHUTDOWN._value.get() == 1.0

    def test_wait_without_active_tasks_returns_at_once(self, service: TaskService):
        assert service._wait_for_tasks_completion(timeout=1.0) is True

    def test_wait_on_a_stuck_task_times_out(self, service: TaskService):
        service._tasks["stuck"] = _task_info("stuck", TaskStatus.RUNNING)

        assert service._wait_for_tasks_completion(timeout=0.1) is False

    def test_wait_returns_once_tasks_signal_completion(self, service: TaskService):
        service._tasks["done"] = _task_info("done", TaskStatus.RUNNING)
        service._tasks_complete_event.set()

        assert service._wait_for_tasks_completion(timeout=1.0) is True

    def test_shutdown_event_clears_state(
        self, service: TaskService, coordinator: TestLifecycleCoordinator
    ):
        service._tasks["t-1"] = _task_info("t-1", TaskStatus.COMPLETED)
        service._task_instances["t-1"] = MagicMock()

        _fire(coordinator, LifecycleEvent.SHUTDOWN)

        assert service._tasks == {}
        assert service._task_instances == {}

    def test_completion_signalled_when_shutting_down_and_idle(self, service: TaskService):
        service._shutting_down = True
        service._tasks["done"] = _task_info("done", TaskStatus.COMPLETED)

        service._check_tasks_complete()

        assert service._tasks_complete_event.is_set()

    def test_completion_not_signalled_outside_shutdown(self, service: TaskService):
        service._check_tasks_complete()

        assert not service._tasks_complete_event.is_set()
