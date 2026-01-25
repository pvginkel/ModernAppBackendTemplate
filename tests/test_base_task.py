"""Tests for BaseTask abstract class."""

from typing import Any
from unittest.mock import Mock

import pytest
from pydantic import BaseModel


class TestBaseTask:
    """Test BaseTask abstract class functionality."""

    def test_concrete_task_can_be_instantiated(self) -> None:
        """Test that concrete task implementation can be instantiated."""
        from common.tasks.base_task import BaseTask, ProgressHandle

        class SimpleTask(BaseTask):
            def execute(self, progress: ProgressHandle, **kwargs: Any) -> BaseModel | None:
                return None

        task = SimpleTask()
        assert task is not None
        assert not task.is_cancelled

    def test_task_cancellation(self) -> None:
        """Test task cancellation functionality."""
        from common.tasks.base_task import BaseTask, ProgressHandle

        class SimpleTask(BaseTask):
            def execute(self, progress: ProgressHandle, **kwargs: Any) -> BaseModel | None:
                return None

        task = SimpleTask()
        assert not task.is_cancelled

        task.cancel()
        assert task.is_cancelled

    def test_task_cancellation_is_idempotent(self) -> None:
        """Test that cancel can be called multiple times safely."""
        from common.tasks.base_task import BaseTask, ProgressHandle

        class SimpleTask(BaseTask):
            def execute(self, progress: ProgressHandle, **kwargs: Any) -> BaseModel | None:
                return None

        task = SimpleTask()
        task.cancel()
        task.cancel()
        task.cancel()

        assert task.is_cancelled

    def test_task_execute_with_progress_handle(self) -> None:
        """Test that task execute method receives and uses progress handle."""
        from common.tasks.base_task import BaseTask, ProgressHandle

        class ResultModel(BaseModel):
            status: str
            steps_completed: int

        class DemoTask(BaseTask):
            def execute(self, progress: ProgressHandle, **kwargs: Any) -> ResultModel:
                steps = kwargs.get("steps", 3)
                for i in range(steps):
                    if self.is_cancelled:
                        return ResultModel(status="cancelled", steps_completed=i)
                    progress.send_progress(f"Step {i+1}", (i + 1) / steps)
                return ResultModel(status="success", steps_completed=steps)

        task = DemoTask()
        progress_handle = Mock(spec=ProgressHandle)

        result = task.execute(progress_handle, steps=2)

        assert isinstance(result, ResultModel)
        assert result.status == "success"
        assert result.steps_completed == 2

        # Verify progress updates were called
        assert progress_handle.send_progress.call_count == 2

    def test_task_execute_respects_cancellation(self) -> None:
        """Test task execution respects cancellation flag."""
        from common.tasks.base_task import BaseTask, ProgressHandle

        class ResultModel(BaseModel):
            status: str
            completed_steps: int

        class CancellableTask(BaseTask):
            def execute(self, progress: ProgressHandle, **kwargs: Any) -> ResultModel:
                for i in range(10):
                    if self.is_cancelled:
                        return ResultModel(status="cancelled", completed_steps=i)
                    progress.send_progress(f"Step {i+1}", (i + 1) / 10)
                return ResultModel(status="success", completed_steps=10)

        task = CancellableTask()
        progress_handle = Mock(spec=ProgressHandle)

        # Cancel task before execution
        task.cancel()

        result = task.execute(progress_handle)

        assert result.status == "cancelled"
        assert result.completed_steps == 0

    def test_task_execute_with_exception(self) -> None:
        """Test that task exceptions are properly raised."""
        from common.tasks.base_task import BaseTask, ProgressHandle

        class FailingTask(BaseTask):
            def execute(self, progress: ProgressHandle, **kwargs: Any) -> BaseModel | None:
                error_message = kwargs.get("error_message", "Task failed")
                progress.send_progress("Starting...", 0.1)
                raise ValueError(error_message)

        task = FailingTask()
        progress_handle = Mock(spec=ProgressHandle)

        with pytest.raises(ValueError, match="Test error"):
            task.execute(progress_handle, error_message="Test error")

        # Should still have sent progress update before failing
        progress_handle.send_progress.assert_called()


class TestProgressHandle:
    """Test ProgressHandle protocol compliance."""

    def test_progress_handle_protocol_methods(self) -> None:
        """Test that ProgressHandle protocol has required methods."""
        from common.tasks.base_task import ProgressHandle

        # Create a mock that implements the protocol
        progress_handle = Mock(spec=ProgressHandle)

        # Test all required methods exist and can be called
        progress_handle.send_progress("test", 0.5)

        # Verify method was called
        progress_handle.send_progress.assert_called_with("test", 0.5)

    def test_progress_handle_with_various_values(self) -> None:
        """Test ProgressHandle with various progress values."""
        from common.tasks.base_task import ProgressHandle

        progress_handle = Mock(spec=ProgressHandle)

        # Test boundary values
        progress_handle.send_progress("Starting", 0.0)
        progress_handle.send_progress("Half done", 0.5)
        progress_handle.send_progress("Complete", 1.0)

        assert progress_handle.send_progress.call_count == 3
