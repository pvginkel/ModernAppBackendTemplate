"""Tests for task API endpoints."""

import time
from unittest.mock import MagicMock, Mock

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
            data = response.get_json()
            assert data["code"] == "RECORD_NOT_FOUND"
            assert data["error"] == "Task nonexistent was not found"

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
            data = response.get_json()
            assert data["code"] == "RECORD_NOT_FOUND"
            assert data["error"] == "Cancellable task nonexistent was not found"

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
            data = response.get_json()
            assert data["code"] == "RECORD_NOT_FOUND"
            assert data["error"] == "Finished task nonexistent was not found"


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


class TestTaskOwnership:
    """Only the subject that started a task can read, cancel or remove it.

    With OIDC disabled the local admin owns every task. With OIDC enabled a
    task without a subject belongs to nobody. A caller who doesn't own a task
    gets the same 404 as for an unknown id.
    """

    @pytest.fixture
    def owned_tasks(self):
        """A mock TaskService holding a task per owner, and one without."""
        tasks = {
            "alice-task": TaskInfo(
                task_id="alice-task",
                subject="alice",
                status=TaskStatus.COMPLETED,
                start_time="2023-01-01T00:00:00",
            ),
            "tester-task": TaskInfo(
                task_id="tester-task",
                subject="tester",
                status=TaskStatus.COMPLETED,
                start_time="2023-01-01T00:00:00",
            ),
            "system-task": TaskInfo(
                task_id="system-task",
                subject=None,
                status=TaskStatus.COMPLETED,
                start_time="2023-01-01T00:00:00",
            ),
        }
        service = Mock(spec=TaskService)
        service.get_task_status.side_effect = tasks.get
        service.cancel_task.return_value = True
        service.remove_completed_task.return_value = True
        return service

    @staticmethod
    def _call_all(client, task_id):
        return (
            client.get(f"/api/tasks/{task_id}/status"),
            client.post(f"/api/tasks/{task_id}/cancel"),
            client.delete(f"/api/tasks/{task_id}"),
        )

    @staticmethod
    def _error_of(response):
        """The status and error body, less the request's own correlation id."""
        body = response.get_json()
        body.pop("correlationId", None)
        return response.status_code, body

    def test_owner_reads_cancels_and_removes_own_task(
        self, oidc_app, oidc_client, generate_test_jwt, owned_tasks
    ):
        oidc_app.container.task_service.override(owned_tasks)
        oidc_client.set_cookie("access_token", generate_test_jwt(subject="alice"))

        status, cancel, remove = self._call_all(oidc_client, "alice-task")

        assert status.status_code == 200
        assert status.get_json()["subject"] == "alice"
        assert cancel.status_code == 200
        assert remove.status_code == 200
        owned_tasks.cancel_task.assert_called_once_with("alice-task")
        owned_tasks.remove_completed_task.assert_called_once_with("alice-task")

    def test_other_subject_gets_404_and_task_is_untouched(
        self, oidc_app, oidc_client, generate_test_jwt, owned_tasks
    ):
        oidc_app.container.task_service.override(owned_tasks)
        oidc_client.set_cookie("access_token", generate_test_jwt(subject="bob"))

        responses = self._call_all(oidc_client, "alice-task")

        assert [r.status_code for r in responses] == [404, 404, 404]
        owned_tasks.cancel_task.assert_not_called()
        owned_tasks.remove_completed_task.assert_not_called()

    def test_refusal_reads_like_an_unknown_task(
        self, oidc_app, oidc_client, generate_test_jwt, owned_tasks
    ):
        oidc_app.container.task_service.override(owned_tasks)
        oidc_client.set_cookie("access_token", generate_test_jwt(subject="bob"))

        refused = self._call_all(oidc_client, "alice-task")
        owned_tasks.get_task_status.side_effect = lambda task_id: None
        unknown = self._call_all(oidc_client, "alice-task")

        assert [self._error_of(r) for r in refused] == [self._error_of(r) for r in unknown]
        assert [r.status_code for r in refused] == [404, 404, 404]

    def test_task_without_subject_is_nobodys_with_oidc_enabled(
        self, oidc_app, oidc_client, generate_test_jwt, owned_tasks
    ):
        """An admin doesn't own a system task either."""
        oidc_app.container.task_service.override(owned_tasks)
        oidc_client.set_cookie(
            "access_token", generate_test_jwt(subject="alice", roles=["admin"])
        )

        responses = self._call_all(oidc_client, "system-task")

        assert [r.status_code for r in responses] == [404, 404, 404]
        owned_tasks.cancel_task.assert_not_called()
        owned_tasks.remove_completed_task.assert_not_called()

    def test_jwt_subject_local_user_owns_nothing_with_oidc_enabled(
        self, oidc_app, oidc_client, generate_test_jwt, owned_tasks
    ):
        """The local admin exemption needs OIDC off, not just the subject."""
        oidc_app.container.task_service.override(owned_tasks)
        oidc_client.set_cookie("access_token", generate_test_jwt(subject="local-user"))

        assert oidc_client.get("/api/tasks/alice-task/status").status_code == 404
        assert oidc_client.get("/api/tasks/system-task/status").status_code == 404

    @pytest.mark.parametrize("task_id", ["alice-task", "system-task"])
    def test_local_admin_owns_every_task_with_oidc_disabled(
        self, app, client, owned_tasks, task_id
    ):
        app.container.task_service.override(owned_tasks)

        responses = self._call_all(client, task_id)

        assert [r.status_code for r in responses] == [200, 200, 200]

    def test_test_session_with_oidc_disabled_owns_its_own_tasks_only(
        self, app, client, owned_tasks
    ):
        """A test session is a caller of its own, not the local admin."""
        app.container.task_service.override(owned_tasks)
        token = app.container.testing_service().create_session(subject="tester")
        client.set_cookie("access_token", token)

        assert client.get("/api/tasks/tester-task/status").status_code == 200
        assert client.get("/api/tasks/alice-task/status").status_code == 404
        assert client.get("/api/tasks/system-task/status").status_code == 404

    def test_real_task_service_lifecycle_for_owner_and_stranger(
        self, oidc_app, oidc_client, generate_test_jwt
    ):
        service = TaskService(
            lifecycle_coordinator=StubLifecycleCoordinator(),
            sse_connection_manager=MagicMock(),
            max_workers=1,
            task_timeout=10,
        )
        try:
            oidc_app.container.task_service.override(service)
            task_id = service.start_task(
                DemoTask(), caller_subject="alice", steps=1, delay=0.01
            ).task_id
            deadline = time.monotonic() + 5
            while service.get_task_status(task_id).status != TaskStatus.COMPLETED:
                assert time.monotonic() < deadline, "the task did not complete"
                time.sleep(0.01)

            oidc_client.set_cookie("access_token", generate_test_jwt(subject="bob"))
            assert oidc_client.delete(f"/api/tasks/{task_id}").status_code == 404
            assert service.get_task_status(task_id) is not None

            oidc_client.set_cookie("access_token", generate_test_jwt(subject="alice"))
            status = oidc_client.get(f"/api/tasks/{task_id}/status")
            assert status.status_code == 200
            assert status.get_json()["status"] == TaskStatus.COMPLETED.value
            assert oidc_client.delete(f"/api/tasks/{task_id}").status_code == 200
            assert service.get_task_status(task_id) is None
        finally:
            service.shutdown()
