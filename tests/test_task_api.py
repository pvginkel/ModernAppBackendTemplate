"""Tests for task API endpoints."""

import time
from unittest.mock import Mock

import pytest

from app.schemas.task_schema import TaskInfo, TaskStatus
from app.services.task_service import TaskService
from tests.test_tasks.test_task import DemoTask, LongRunningTask
from tests.testing_utils import StubLifecycleCoordinator


class TestTaskAPI:
    """Test task API endpoints."""

    @pytest.fixture
    def task_service_mock(self):
        return Mock(spec=TaskService)

    def test_get_task_status_success(self, client, app, task_service_mock):
        task_id = "test-task-id"
        task_info = TaskInfo(
            task_id=task_id,
            status=TaskStatus.COMPLETED,
            start_time="2023-01-01T00:00:00",
            end_time="2023-01-01T00:01:00",
            result={"success": True},
        )
        task_service_mock.get_task_status.return_value = task_info

        with app.app_context():
            app.container.task_service.override(task_service_mock)
            response = client.get(f"/api/tasks/{task_id}/status")
            assert response.status_code == 200
            data = response.get_json()
            assert data["task_id"] == task_id
            assert data["status"] == TaskStatus.COMPLETED.value

    def test_get_task_status_not_found(self, client, app, task_service_mock):
        task_service_mock.get_task_status.return_value = None
        with app.app_context():
            app.container.task_service.override(task_service_mock)
            response = client.get("/api/tasks/nonexistent/status")
            assert response.status_code == 404

    def test_cancel_task_success(self, client, app, task_service_mock):
        task_service_mock.cancel_task.return_value = True
        with app.app_context():
            app.container.task_service.override(task_service_mock)
            response = client.post("/api/tasks/test-task-id/cancel")
            assert response.status_code == 200
            data = response.get_json()
            assert data["success"] is True

    def test_cancel_task_not_found(self, client, app, task_service_mock):
        task_service_mock.cancel_task.return_value = False
        with app.app_context():
            app.container.task_service.override(task_service_mock)
            response = client.post("/api/tasks/nonexistent/cancel")
            assert response.status_code == 404

    def test_remove_task_success(self, client, app, task_service_mock):
        task_service_mock.remove_completed_task.return_value = True
        with app.app_context():
            app.container.task_service.override(task_service_mock)
            response = client.delete("/api/tasks/test-task-id")
            assert response.status_code == 200
            data = response.get_json()
            assert data["success"] is True

    def test_remove_task_not_found(self, client, app, task_service_mock):
        task_service_mock.remove_completed_task.return_value = False
        with app.app_context():
            app.container.task_service.override(task_service_mock)
            response = client.delete("/api/tasks/nonexistent")
            assert response.status_code == 404


class TestTaskAPIIntegration:
    """Integration tests for task API with real TaskService."""

    @pytest.fixture
    def real_task_service(self):
        from unittest.mock import MagicMock

        service = TaskService(
            lifecycle_coordinator=StubLifecycleCoordinator(),
            sse_connection_manager=MagicMock(),
            max_workers=1,
            task_timeout=10,
        )
        yield service
        service.shutdown()

    def test_full_task_lifecycle_via_api(self, client, app, real_task_service):
        with app.app_context():
            app.container.task_service.override(real_task_service)

            task = DemoTask()
            response = real_task_service.start_task(
                task, message="API integration test", steps=2, delay=0.01
            )
            task_id = response.task_id

            status_response = client.get(f"/api/tasks/{task_id}/status")
            assert status_response.status_code == 200

            time.sleep(0.2)

            status_response = client.get(f"/api/tasks/{task_id}/status")
            assert status_response.status_code == 200
            assert status_response.get_json()["status"] == TaskStatus.COMPLETED.value

            remove_response = client.delete(f"/api/tasks/{task_id}")
            assert remove_response.status_code == 200

            status_response = client.get(f"/api/tasks/{task_id}/status")
            assert status_response.status_code == 404

    def test_task_cancellation_via_api(self, client, app, real_task_service):
        with app.app_context():
            app.container.task_service.override(real_task_service)

            task = LongRunningTask()
            response = real_task_service.start_task(task, total_time=2.0, check_interval=0.05)
            task_id = response.task_id

            time.sleep(0.1)
            cancel_response = client.post(f"/api/tasks/{task_id}/cancel")
            assert cancel_response.status_code == 200

            time.sleep(0.2)
            status_response = client.get(f"/api/tasks/{task_id}/status")
            assert status_response.get_json()["status"] == TaskStatus.CANCELLED.value
