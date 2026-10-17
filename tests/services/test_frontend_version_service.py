"""Tests for FrontendVersionService's version event on connect."""

from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.services.frontend_version_service import FrontendVersionService
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
        assert manager.send_event.call_args.args[1] == {"version": "v2"}

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
        assert manager.send_event.call_args.args[:2] == ("req-1", {"version": "v1"})
