"""Test task implementations for testing the task system."""

import time

from pydantic import BaseModel, Field

from app.services.base_task import BaseTask, ProgressHandle


class DemoTaskResult(BaseModel):
    """Result schema for DemoTask."""

    message: str = Field(description="Task message")
    steps_completed: int = Field(description="Number of steps completed")
    status: str = Field(description="Task completion status")


class CancelledTaskResult(BaseModel):
    """Result schema for cancelled task."""

    status: str = Field(description="Task status")
    completed_steps: int = Field(description="Steps completed before cancellation")


class LongRunningTaskResult(BaseModel):
    """Result schema for LongRunningTask."""

    status: str = Field(description="Task completion status")
    elapsed_time: float = Field(description="Time elapsed during execution")
    completed: bool = Field(description="Whether task completed successfully")


class DemoTask(BaseTask):
    """Simple test task for testing task execution."""

    def execute(self, progress_handle: ProgressHandle, **kwargs) -> BaseModel:
        message = kwargs.get('message', 'default message')
        delay = kwargs.get('delay', 0.1)
        steps = kwargs.get('steps', 3)

        progress_handle.send_progress("Starting test task", 0.0)

        for i in range(steps):
            if self.is_cancelled:
                return CancelledTaskResult(
                    status="cancelled",
                    completed_steps=i,
                )

            time.sleep(delay)
            progress = (i + 1) / steps
            progress_handle.send_progress(f"Step {i+1}/{steps}", progress)

        return DemoTaskResult(
            message=message,
            steps_completed=steps,
            status="success",
        )


class FailingTask(BaseTask):
    """Task that always fails for testing error handling."""

    def execute(self, progress_handle: ProgressHandle, **kwargs) -> BaseModel:
        error_message = kwargs.get('error_message', 'Test error')
        delay = kwargs.get('delay', 0.1)

        progress_handle.send_progress("Starting failing task", 0.0)
        time.sleep(delay)

        raise ValueError(error_message)


class LongRunningTask(BaseTask):
    """Long running task for testing cancellation."""

    def execute(self, progress_handle: ProgressHandle, **kwargs) -> BaseModel:
        total_time = kwargs.get('total_time', 5.0)
        check_interval = kwargs.get('check_interval', 0.1)

        start_time = time.time()
        progress_handle.send_progress("Starting long task", 0.0)

        while time.time() - start_time < total_time:
            if self.is_cancelled:
                elapsed = time.time() - start_time
                return LongRunningTaskResult(
                    status="cancelled",
                    elapsed_time=elapsed,
                    completed=False,
                )

            elapsed = time.time() - start_time
            progress = min(elapsed / total_time, 1.0)
            progress_handle.send_progress(f"Running... {elapsed:.1f}s", progress)

            time.sleep(check_interval)

        return LongRunningTaskResult(
            status="completed",
            elapsed_time=total_time,
            completed=True,
        )
