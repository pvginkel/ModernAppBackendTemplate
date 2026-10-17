"""Tests for FrontendVersionService: the version on connect, queued versions, shutdown."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from app.config import Settings
from app.services.frontend_version_service import FrontendVersionService
from app.services.sse_connection_manager import (
    BROADCAST_TARGET,
    SSEConnectionManager,
    Target,
)
from app.utils.lifecycle_coordinator import LifecycleEvent
from tests.testing_utils import StubLifecycleCoordinator


def _service(stage: str) -> tuple[FrontendVersionService, MagicMock]:
    manager = MagicMock()
    service = FrontendVersionService(
        settings=Settings(stage=stage, frontend_version_url="http://frontend/version.json"),
        lifecycle_coordinator=StubLifecycleCoordinator(),
        sse_connection_manager=manager,
    )
    return service, manager


class TestVersionOnConnect:
    @pytest.mark.parametrize("stage", ["local-dev", "local-tst"])
    def test_local_stage_sends_nothing_without_a_queued_version(
        self, stage: str, monkeypatch: pytest.MonkeyPatch
    ):
        service, manager = _service(stage)
        fetch = MagicMock()
        monkeypatch.setattr("app.services.frontend_version_service.requests.get", fetch)

        service._on_connect_callback("req-1")

        fetch.assert_not_called()
        manager.send_event.assert_not_called()

    def test_local_stage_still_sends_a_queued_version(self):
        service, manager = _service("local-tst")
        service.queue_version_event("req-1", "v2")
        manager.send_event.reset_mock()

        service._on_connect_callback("req-1")

        manager.send_event.assert_called_once()
        assert manager.send_event.call_args.args[:2] == (
            Target.connection("req-1"),
            {"version": "v2"},
        )

    @pytest.mark.parametrize("stage", ["prd", "tst"])
    def test_deployed_stage_fetches_and_sends_the_version(
        self, stage: str, monkeypatch: pytest.MonkeyPatch
    ):
        service, manager = _service(stage)
        response = MagicMock(text='{"version": "v1"}')
        monkeypatch.setattr(
            "app.services.frontend_version_service.requests.get", MagicMock(return_value=response)
        )

        service._on_connect_callback("req-1")

        manager.send_event.assert_called_once()
        assert manager.send_event.call_args.args[:2] == (
            Target.connection("req-1"),
            {"version": "v1"},
        )


    @pytest.mark.parametrize(
        "failure",
        [
            {"side_effect": requests.ConnectionError("frontend down")},
            {"return_value": MagicMock(text="<html>not json</html>")},
        ],
        ids=["request-error", "not-json"],
    )
    def test_failed_fetch_sends_nothing(
        self, failure: dict[str, object], monkeypatch: pytest.MonkeyPatch
    ):
        """A failed fetch sends no made-up ``"unknown"`` version."""
        service, manager = _service("prd")
        monkeypatch.setattr(
            "app.services.frontend_version_service.requests.get", MagicMock(**failure)
        )

        assert service._fetch_frontend_version() is None
        service._on_connect_callback("req-1")

        manager.send_event.assert_not_called()


class TestQueueVersionEvent:
    def test_queued_version_is_broadcast(self):
        """The version concerns every client: the one broadcast the template sends."""
        service, manager = _service("prd")

        service.queue_version_event("req-1", "v3", changelog="notes")

        manager.send_event.assert_called_once()
        assert manager.send_event.call_args.args[:2] == (
            BROADCAST_TARGET,
            {"version": "v3", "changelog": "notes"},
        )

    def test_queued_version_without_changelog_is_broadcast_in_a_local_stage(self):
        service, manager = _service("local-dev")

        assert service.queue_version_event("req-1", "v3") is True

        manager.send_event.assert_called_once()
        assert manager.send_event.call_args.args[:2] == (BROADCAST_TARGET, {"version": "v3"})

    def test_refused_while_shutting_down(self):
        service, manager = _service("prd")
        service._handle_lifecycle_event(LifecycleEvent.PREPARE_SHUTDOWN)

        assert service.queue_version_event("req-1", "v3") is False

        manager.send_event.assert_not_called()
        assert service._pending_version == {}


class TestPendingVersion:
    def test_dropped_when_the_connection_goes(self):
        """The pending version lives as long as its connection, so the dict stays bounded."""
        service, manager = _service("local-tst")
        manager.register_on_disconnect.assert_called_once_with(service._on_disconnect_callback)
        service.queue_version_event("req-1", "v2")

        service._on_disconnect_callback("req-1")
        manager.send_event.reset_mock()
        service._on_connect_callback("req-1")

        assert service._pending_version == {}
        manager.send_event.assert_not_called()

    def test_disconnect_of_another_connection_keeps_it(self):
        service, _ = _service("local-tst")
        service.queue_version_event("req-1", "v2")

        service._on_disconnect_callback("req-2")

        assert service._pending_version == {"req-1": {"version": "v2"}}

    def test_queue_connect_disconnect_through_the_manager(self):
        """End to end on a real manager: sent on connect, gone after the disconnect."""
        manager = SSEConnectionManager(gateway_url="http://gateway:3000")
        service = FrontendVersionService(
            settings=Settings(stage="local-tst"),
            lifecycle_coordinator=StubLifecycleCoordinator(),
            sse_connection_manager=manager,
        )

        with patch("app.services.sse_connection_manager.requests.post") as post:
            post.return_value = MagicMock(status_code=200)
            service.queue_version_event("req-1", "v2")
            manager.on_connect("req-1", "token-1", "/api/sse/stream?request_id=req-1")

            sent = [call.kwargs["json"] for call in post.call_args_list]
            assert [body["token"] for body in sent] == ["token-1"]
            assert '"v2"' in sent[0]["event"]["data"]

            manager.on_disconnect("token-1")

        assert service._pending_version == {}

    def test_shutdown_clears_pending_versions(self):
        service, _ = _service("local-tst")
        service.queue_version_event("req-1", "v2")

        service._handle_lifecycle_event(LifecycleEvent.PREPARE_SHUTDOWN)
        service._handle_lifecycle_event(LifecycleEvent.SHUTDOWN)

        assert service._is_shutting_down is True
        assert service._pending_version == {}
