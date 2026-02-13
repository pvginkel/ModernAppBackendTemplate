"""Tests for BaseTask abstract class."""

from unittest.mock import Mock

import pytest

from app.services.base_task import ProgressHandle
from tests.test_tasks.test_task import (
    CancelledTaskResult,
    DemoTask,
    DemoTaskResult,
    FailingTask,
)


class TestBaseTask:
    """Test BaseTask abstract class functionality."""

    def test_concrete_task_can_be_instantiated(self):
        task = DemoTask()
        assert task is not None
        assert not task.is_cancelled

    def test_task_cancellation(self):
        task = DemoTask()
        assert not task.is_cancelled
        task.cancel()
        assert task.is_cancelled

    def test_task_execute_with_progress_handle(self):
        task = DemoTask()
        progress_handle = Mock(spec=ProgressHandle)
        result = task.execute(
            progress_handle,
            message="test message",
            steps=2,
            delay=0.01,
        )
        assert isinstance(result, DemoTaskResult)
        assert result.status == "success"
        assert result.message == "test message"
        assert result.steps_completed == 2
        progress_handle.send_progress.assert_called()

    def test_task_execute_with_cancellation(self):
        task = DemoTask()
        progress_handle = Mock(spec=ProgressHandle)
        task.cancel()
        result = task.execute(
            progress_handle,
            message="cancelled task",
            steps=5,
            delay=0.01,
        )
        assert isinstance(result, CancelledTaskResult)
        assert result.status == "cancelled"
        assert result.completed_steps == 0

    def test_task_execute_with_exception(self):
        task = FailingTask()
        progress_handle = Mock(spec=ProgressHandle)
        with pytest.raises(ValueError, match="Test error"):
            task.execute(
                progress_handle,
                error_message="Test error",
                delay=0.01,
            )
        progress_handle.send_progress.assert_called()


class TestProgressHandle:
    """Test ProgressHandle protocol compliance."""

    def test_progress_handle_protocol(self):
        progress_handle = Mock(spec=ProgressHandle)
        progress_handle.send_progress_text("test")
        progress_handle.send_progress_value(0.5)
        progress_handle.send_progress("test", 0.5)
        progress_handle.send_progress_text.assert_called_with("test")
        progress_handle.send_progress_value.assert_called_with(0.5)
        progress_handle.send_progress.assert_called_with("test", 0.5)
