import logging
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from prometheus_client import Gauge

from app.exceptions import InvalidOperationException
from app.schemas.task_schema import (
    TaskEvent,
    TaskEventType,
    TaskInfo,
    TaskProgressUpdate,
    TaskStartResponse,
    TaskStatus,
)
from app.services.base_task import BaseTask
from app.services.connection_manager import ConnectionManager
from app.utils.lifecycle_coordinator import LifecycleCoordinatorProtocol, LifecycleEvent

ACTIVE_TASKS_AT_SHUTDOWN = Gauge(
    "active_tasks_at_shutdown",
    "Number of active tasks when shutdown initiated",
)

logger = logging.getLogger(__name__)


class TaskProgressHandle:
    """Implementation of ProgressHandle for sending updates via SSE."""

    def __init__(self, task_id: str, connection_manager: ConnectionManager):
        self.task_id = task_id
        self.connection_manager = connection_manager
        self.progress = 0.0
        self.progress_text = ""

    def send_progress_text(self, text: str) -> None:
        self.send_progress(text, self.progress)

    def send_progress_value(self, value: float) -> None:
        self.send_progress(self.progress_text, value)

    def send_progress(self, text: str, value: float) -> None:
        self.progress_text = text
        if value > self.progress:
            self.progress = value
        self._send_progress_event(TaskProgressUpdate(text=text, value=value))

    def _send_progress_event(self, progress: TaskProgressUpdate) -> None:
        event = TaskEvent(
            event_type=TaskEventType.PROGRESS_UPDATE,
            task_id=self.task_id,
            data=progress.model_dump()
        )
        try:
            self.connection_manager.send_event(
                None,
                event.model_dump(mode='json'),
                event_name="task_event",
                service_type="task"
            )
        except Exception as e:
            logger.warning(f"Failed to broadcast progress event for task {self.task_id}: {e}")


class TaskService:
    """Service for managing background tasks with SSE progress updates."""

    def __init__(
        self,
        lifecycle_coordinator: LifecycleCoordinatorProtocol,
        connection_manager: ConnectionManager,
        max_workers: int = 4,
        task_timeout: int = 300,
        cleanup_interval: int = 600
    ):
        self.max_workers = max_workers
        self.task_timeout = task_timeout
        self.cleanup_interval = cleanup_interval
        self.lifecycle_coordinator = lifecycle_coordinator
        self.connection_manager = connection_manager
        self._tasks: dict[str, TaskInfo] = {}
        self._task_instances: dict[str, BaseTask] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._lock = threading.RLock()
        self._shutdown_event = threading.Event()
        self._shutting_down = False
        self._tasks_complete_event = threading.Event()

        self.lifecycle_coordinator.register_lifecycle_notification(self._on_lifecycle_event)
        self.lifecycle_coordinator.register_shutdown_waiter("TaskService", self._wait_for_tasks_completion)

        self._cleanup_thread = threading.Thread(target=self._cleanup_worker, daemon=True)
        self._cleanup_thread.start()

        logger.info(f"TaskService initialized: max_workers={max_workers}, timeout={task_timeout}s, cleanup_interval={cleanup_interval}s")

    def start_task(self, task: BaseTask, **kwargs: Any) -> TaskStartResponse:
        if self._shutting_down:
            raise InvalidOperationException("start task", "service is shutting down")

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

    def _broadcast_task_event(self, event: TaskEvent) -> None:
        success = self.connection_manager.send_event(
            None,
            event.model_dump(mode='json'),
            event_name="task_event",
            service_type="task"
        )
        if not success:
            logger.debug(f"No active connections for broadcast: {event.event_type}")

    def get_task_status(self, task_id: str) -> TaskInfo | None:
        with self._lock:
            return self._tasks.get(task_id)

    def cancel_task(self, task_id: str) -> bool:
        with self._lock:
            task_instance = self._task_instances.get(task_id)
            task_info = self._tasks.get(task_id)

            if not task_instance or not task_info:
                return False
            if task_info.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
                return False

            task_instance.cancel()
            task_info.status = TaskStatus.CANCELLED
            task_info.end_time = datetime.now(UTC)
            logger.info(f"Cancelled task {task_id}")
            return True

    def remove_completed_task(self, task_id: str) -> bool:
        with self._lock:
            task_info = self._tasks.get(task_id)
            if not task_info or task_info.status not in [
                TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED
            ]:
                return False
            self._tasks.pop(task_id, None)
            self._task_instances.pop(task_id, None)
            logger.debug(f"Removed completed task {task_id}")
            return True

    def _execute_task(self, task_id: str, task: BaseTask, kwargs: dict[str, Any]) -> None:
        start_time = time.perf_counter()
        try:
            with self._lock:
                task_info = self._tasks.get(task_id)
                if task_info:
                    task_info.status = TaskStatus.RUNNING

            start_event = TaskEvent(
                event_type=TaskEventType.TASK_STARTED,
                task_id=task_id,
                data=None,
            )
            self._broadcast_task_event(start_event)

            progress_handle = TaskProgressHandle(task_id, self.connection_manager)
            result = task.execute(progress_handle, **kwargs)

            with self._lock:
                task_info = self._tasks.get(task_id)
                if task_info and task_info.status != TaskStatus.CANCELLED:
                    task_info.status = TaskStatus.COMPLETED
                    task_info.end_time = datetime.now(UTC)
                    task_info.result = result.model_dump() if result else None

                    completion_event = TaskEvent(
                        event_type=TaskEventType.TASK_COMPLETED,
                        task_id=task_id,
                        data=result.model_dump() if result else None
                    )
                    self._broadcast_task_event(completion_event)
                    logger.info(f"Task {task_id} completed successfully")
                    self._check_tasks_complete()

        except Exception as e:
            error_msg = str(e)
            error_trace = traceback.format_exc()
            logger.error(f"Task {task_id} failed: {error_msg}")

            with self._lock:
                task_info = self._tasks.get(task_id)
                if task_info:
                    task_info.status = TaskStatus.FAILED
                    task_info.end_time = datetime.now(UTC)
                    task_info.error = error_msg

            failure_event = TaskEvent(
                event_type=TaskEventType.TASK_FAILED,
                task_id=task_id,
                data={"error": error_msg, "traceback": error_trace}
            )
            self._broadcast_task_event(failure_event)
            self._check_tasks_complete()

    def _cleanup_worker(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                if self._shutdown_event.wait(timeout=self.cleanup_interval):
                    break
                self._cleanup_completed_tasks()
            except Exception as e:
                logger.error(f"Error during task cleanup: {e}", exc_info=True)

    def _cleanup_completed_tasks(self) -> None:
        current_time = datetime.now(UTC)
        tasks_to_remove = []
        with self._lock:
            for task_id, task_info in self._tasks.items():
                if task_info.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
                    if task_info.end_time:
                        time_since_completion = (current_time - task_info.end_time).total_seconds()
                        if time_since_completion >= self.cleanup_interval:
                            tasks_to_remove.append(task_id)

        for task_id in tasks_to_remove:
            self.remove_completed_task(task_id)

    def shutdown(self) -> None:
        logger.info("Shutting down TaskService...")
        self._shutdown_event.set()
        if self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=5.0)
        self._executor.shutdown(wait=True)
        with self._lock:
            self._tasks.clear()
            self._task_instances.clear()
        logger.info("TaskService shutdown complete")

    def _on_lifecycle_event(self, event: LifecycleEvent) -> None:
        match event:
            case LifecycleEvent.PREPARE_SHUTDOWN:
                with self._lock:
                    self._shutting_down = True
                    active_count = self._get_active_task_count()
                    ACTIVE_TASKS_AT_SHUTDOWN.set(active_count)
            case LifecycleEvent.SHUTDOWN:
                self.shutdown()

    def _wait_for_tasks_completion(self, timeout: float) -> bool:
        with self._lock:
            active_count = self._get_active_task_count()
            if active_count == 0:
                return True
        return self._tasks_complete_event.wait(timeout=timeout)

    def _get_active_task_count(self) -> int:
        return sum(
            1 for task in self._tasks.values()
            if task.status in [TaskStatus.PENDING, TaskStatus.RUNNING]
        )

    def _check_tasks_complete(self) -> None:
        if self._shutting_down:
            with self._lock:
                if self._get_active_task_count() == 0:
                    self._tasks_complete_event.set()
