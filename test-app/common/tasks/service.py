"""Background task service with progress updates."""

import logging
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from common.core.errors import InvalidOperationException
from common.core.shutdown import LifetimeEvent
from common.tasks.base_task import BaseTask, ProgressHandle
from common.tasks.protocols import BroadcasterProtocol
from common.tasks.schemas import (
    TaskEvent,
    TaskEventType,
    TaskInfo,
    TaskProgressUpdate,
    TaskStartResponse,
    TaskStatus,
)

if TYPE_CHECKING:
    from common.core.shutdown import ShutdownCoordinatorProtocol
    from common.metrics.service import MetricsServiceProtocol

logger = logging.getLogger(__name__)


class TaskProgressHandle(ProgressHandle):
    """Implementation of ProgressHandle for sending updates via broadcaster."""

    def __init__(self, task_id: str, broadcaster: BroadcasterProtocol):
        self.task_id = task_id
        self.broadcaster = broadcaster
        self.progress = 0.0
        self.progress_text = ""

    def send_progress_text(self, text: str) -> None:
        """Send a text progress update to connected clients."""
        self.send_progress(text, self.progress)

    def send_progress_value(self, value: float) -> None:
        """Send a progress value update (0.0 to 1.0) to connected clients."""
        self.send_progress(self.progress_text, value)

    def send_progress(self, text: str, value: float) -> None:
        """Send both text and progress value update to connected clients."""
        self.progress_text = text
        if value > self.progress:
            self.progress = value

        event = TaskEvent(
            event_type=TaskEventType.PROGRESS_UPDATE,
            task_id=self.task_id,
            data=TaskProgressUpdate(text=text, value=value).model_dump(),
        )
        self.broadcaster.broadcast_task_event(
            self.task_id,
            event.event_type.value,
            event.model_dump(mode="json"),
        )


class TaskService:
    """Service for managing background tasks with progress updates."""

    def __init__(
        self,
        metrics_service: "MetricsServiceProtocol",
        shutdown_coordinator: "ShutdownCoordinatorProtocol",
        broadcaster: BroadcasterProtocol,
        max_workers: int = 4,
        task_timeout: int = 300,
        cleanup_interval: int = 600,
    ):
        """Initialize TaskService with configurable parameters.

        Args:
            metrics_service: Instance of MetricsService for recording metrics
            shutdown_coordinator: Coordinator for graceful shutdown
            broadcaster: Broadcaster for task events (SSE or null)
            max_workers: Maximum number of concurrent tasks
            task_timeout: Task execution timeout in seconds
            cleanup_interval: How often to clean up completed tasks in seconds
        """
        self.max_workers = max_workers
        self.task_timeout = task_timeout
        self.cleanup_interval = cleanup_interval
        self.metrics_service = metrics_service
        self.shutdown_coordinator = shutdown_coordinator
        self.broadcaster = broadcaster
        self._tasks: dict[str, TaskInfo] = {}
        self._task_instances: dict[str, BaseTask] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._lock = threading.RLock()
        self._shutdown_event = threading.Event()
        self._shutting_down = False
        self._tasks_complete_event = threading.Event()

        # Register with shutdown coordinator
        self.shutdown_coordinator.register_lifetime_notification(
            self._on_lifetime_event
        )
        self.shutdown_coordinator.register_shutdown_waiter(
            "TaskService", self._wait_for_tasks_completion
        )

        # Start cleanup thread
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_worker, daemon=True
        )
        self._cleanup_thread.start()

        logger.info(
            f"TaskService initialized: max_workers={max_workers}, "
            f"timeout={task_timeout}s, cleanup_interval={cleanup_interval}s"
        )

    def start_task(self, task: BaseTask, **kwargs: Any) -> TaskStartResponse:
        """Start a background task and return task info.

        Args:
            task: Instance of BaseTask to execute
            **kwargs: Task-specific parameters

        Returns:
            TaskStartResponse with task ID and status

        Raises:
            InvalidOperationException: If service is shutting down
        """
        if self._shutting_down:
            raise InvalidOperationException(
                "Cannot start task: service is shutting down"
            )

        task_id = str(uuid.uuid4())

        with self._lock:
            task_info = TaskInfo(
                task_id=task_id,
                status=TaskStatus.PENDING,
                start_time=datetime.now(UTC),
                end_time=None,
                result=None,
                error=None,
            )

            self._tasks[task_id] = task_info
            self._task_instances[task_id] = task
            self._executor.submit(self._execute_task, task_id, task, kwargs)

        logger.info(f"Started task {task_id} of type {type(task).__name__}")

        return TaskStartResponse(task_id=task_id, status=TaskStatus.PENDING)

    def get_task_status(self, task_id: str) -> TaskInfo | None:
        """Get current status of a task."""
        with self._lock:
            return self._tasks.get(task_id)

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task."""
        with self._lock:
            task_instance = self._task_instances.get(task_id)
            task_info = self._tasks.get(task_id)

            if not task_instance or not task_info:
                return False

            if task_info.status in [
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ]:
                return False

            task_instance.cancel()
            task_info.status = TaskStatus.CANCELLED
            task_info.end_time = datetime.now(UTC)

            logger.info(f"Cancelled task {task_id}")
            return True

    def _execute_task(
        self, task_id: str, task: BaseTask, kwargs: dict[str, Any]
    ) -> None:
        """Execute a task in a background thread."""
        start_time = time.perf_counter()

        try:
            with self._lock:
                task_info = self._tasks.get(task_id)
                if task_info:
                    task_info.status = TaskStatus.RUNNING

            # Broadcast task started
            self._broadcast_event(
                TaskEvent(
                    event_type=TaskEventType.TASK_STARTED,
                    task_id=task_id,
                    data=None,
                )
            )

            # Create progress handle
            progress_handle = TaskProgressHandle(task_id, self.broadcaster)

            # Execute the task
            result = task.execute(progress_handle, **kwargs)
            duration = time.perf_counter() - start_time

            with self._lock:
                task_info = self._tasks.get(task_id)
                if task_info and task_info.status != TaskStatus.CANCELLED:
                    task_info.status = TaskStatus.COMPLETED
                    task_info.end_time = datetime.now(UTC)
                    task_info.result = result.model_dump() if result else None

                    self.metrics_service.record_task_execution(
                        type(task).__name__, duration, "success"
                    )

                    self._broadcast_event(
                        TaskEvent(
                            event_type=TaskEventType.TASK_COMPLETED,
                            task_id=task_id,
                            data=result.model_dump() if result else None,
                        )
                    )

                    logger.info(f"Task {task_id} completed successfully")
                    self._check_tasks_complete()

        except Exception as e:
            error_msg = str(e)
            error_trace = traceback.format_exc()
            duration = time.perf_counter() - start_time

            logger.error(f"Task {task_id} failed: {error_msg}")

            with self._lock:
                task_info = self._tasks.get(task_id)
                if task_info:
                    task_info.status = TaskStatus.FAILED
                    task_info.end_time = datetime.now(UTC)
                    task_info.error = error_msg

                    self.metrics_service.record_task_execution(
                        type(task).__name__, duration, "error"
                    )

            self._broadcast_event(
                TaskEvent(
                    event_type=TaskEventType.TASK_FAILED,
                    task_id=task_id,
                    data={"error": error_msg, "traceback": error_trace},
                )
            )

            self._check_tasks_complete()

    def _broadcast_event(self, event: TaskEvent) -> None:
        """Broadcast a task event."""
        self.broadcaster.broadcast_task_event(
            event.task_id,
            event.event_type.value,
            event.model_dump(mode="json"),
        )

    def _cleanup_worker(self) -> None:
        """Background worker that periodically cleans up completed tasks."""
        while not self._shutdown_event.is_set():
            try:
                if self._shutdown_event.wait(timeout=self.cleanup_interval):
                    break
                self._cleanup_completed_tasks()
            except Exception as e:
                logger.error(f"Error during task cleanup: {e}", exc_info=True)

    def _cleanup_completed_tasks(self) -> None:
        """Remove completed tasks older than cleanup_interval."""
        current_time = datetime.now(UTC)
        tasks_to_remove = []

        with self._lock:
            for task_id, task_info in self._tasks.items():
                if task_info.status in [
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                ]:
                    if task_info.end_time:
                        time_since = (current_time - task_info.end_time).total_seconds()
                        if time_since >= self.cleanup_interval:
                            tasks_to_remove.append(task_id)

        for task_id in tasks_to_remove:
            with self._lock:
                self._tasks.pop(task_id, None)
                self._task_instances.pop(task_id, None)

        if tasks_to_remove:
            logger.debug(f"Cleaned up {len(tasks_to_remove)} completed tasks")

    def _on_lifetime_event(self, event: LifetimeEvent) -> None:
        """Callback for shutdown lifecycle events."""
        match event:
            case LifetimeEvent.PREPARE_SHUTDOWN:
                with self._lock:
                    self._shutting_down = True
                    logger.info(
                        f"TaskService shutdown initiated with "
                        f"{self._get_active_task_count()} active tasks"
                    )
            case LifetimeEvent.SHUTDOWN:
                self.shutdown()

    def _wait_for_tasks_completion(self, timeout: float) -> bool:
        """Wait for all tasks to complete within timeout."""
        with self._lock:
            active_count = self._get_active_task_count()
            if active_count == 0:
                logger.info("No active tasks to wait for")
                return True

            logger.info(
                f"Waiting for {active_count} active tasks "
                f"(timeout: {timeout:.1f}s)"
            )

        completed = self._tasks_complete_event.wait(timeout=timeout)

        if completed:
            logger.info("All tasks completed gracefully")
        else:
            with self._lock:
                remaining = self._get_active_task_count()
                logger.warning(
                    f"Timeout waiting for tasks, {remaining} still active"
                )

        return completed

    def _get_active_task_count(self) -> int:
        """Get count of active (pending or running) tasks."""
        return sum(
            1
            for task in self._tasks.values()
            if task.status in [TaskStatus.PENDING, TaskStatus.RUNNING]
        )

    def _check_tasks_complete(self) -> None:
        """Check if all tasks are complete during shutdown."""
        if self._shutting_down:
            with self._lock:
                if self._get_active_task_count() == 0:
                    logger.info("All tasks completed during shutdown")
                    self._tasks_complete_event.set()

    def shutdown(self) -> None:
        """Shutdown the task service and cleanup resources."""
        logger.info("Shutting down TaskService...")

        self._shutdown_event.set()
        if self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=5.0)

        self._executor.shutdown(wait=True)

        with self._lock:
            active_tasks = self._get_active_task_count()
            if active_tasks > 0:
                logger.warning(f"Shutting down with {active_tasks} active tasks")

            self._tasks.clear()
            self._task_instances.clear()

        logger.info("TaskService shutdown complete")
