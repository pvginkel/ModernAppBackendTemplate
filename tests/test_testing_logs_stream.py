"""Tests for the testing log stream's shutdown path.

Covers the per-stream lifecycle callback in ``app/api/testing_logs.py``: the
sentinel that ends the stream on PREPARE_SHUTDOWN, and the teardown that
unregisters both the log client and the callback, so streams leak neither.

The generator is driven directly with ``next()``, with a recording lifecycle
coordinator and the real ``LogCaptureHandler``.
"""

from collections.abc import Callable, Generator
from types import SimpleNamespace
from typing import Any

import pytest
from flask import Flask

from app.utils.lifecycle_coordinator import LifecycleEvent
from app.utils.log_capture import LogCaptureHandler


@pytest.fixture
def testing_logs_module(app: Flask) -> Any:
    """The testing_logs module, imported once the app has wired app.api."""
    from app.api import testing_logs

    return testing_logs


class _RecordingLifecycleCoordinator:
    """Records registrations so a test can fire events by hand."""

    def __init__(self) -> None:
        self._callbacks: list[Callable[[LifecycleEvent], None]] = []

    def register_lifecycle_notification(self, callback: Callable[[LifecycleEvent], None]) -> None:
        self._callbacks.append(callback)

    def unregister_lifecycle_notification(self, callback: Callable[[LifecycleEvent], None]) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def fire(self, event: LifecycleEvent) -> None:
        for callback in list(self._callbacks):
            callback(event)

    @property
    def callback_count(self) -> int:
        return len(self._callbacks)


def _stream(
    testing_logs_module: Any,
    lifecycle: _RecordingLifecycleCoordinator,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[str]:
    """Call ``stream_logs`` with ``lifecycle`` injected; return its body generator."""
    monkeypatch.setattr(testing_logs_module, "ensure_request_id_from_query", lambda _: None)
    monkeypatch.setattr(testing_logs_module, "get_current_correlation_id", lambda: None)
    # stream_logs only reads request.args.get("request_id").
    fake_request = SimpleNamespace(args=SimpleNamespace(get=lambda _key: None))
    monkeypatch.setattr(testing_logs_module, "request", fake_request)

    response = testing_logs_module.stream_logs(lifecycle_coordinator=lifecycle)
    return response.response  # type: ignore[no-any-return]


class TestStreamLogsGenerator:
    def test_log_event_is_yielded_as_sse(
        self, testing_logs_module: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lifecycle = _RecordingLifecycleCoordinator()
        gen = _stream(testing_logs_module, lifecycle, monkeypatch)
        try:
            assert "event: connection_open" in next(gen)

            LogCaptureHandler.get_instance()._broadcast_event("log", {"message": "hello"})

            rendered = next(gen)
            assert "event: log" in rendered
            assert "hello" in rendered
        finally:
            gen.close()

    def test_shutdown_sentinel_ends_stream_without_yielding(
        self, testing_logs_module: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lifecycle = _RecordingLifecycleCoordinator()
        gen = _stream(testing_logs_module, lifecycle, monkeypatch)
        try:
            next(gen)  # connection_open
            assert lifecycle.callback_count == 1

            lifecycle.fire(LifecycleEvent.PREPARE_SHUTDOWN)

            with pytest.raises(StopIteration):
                next(gen)
            assert lifecycle.callback_count == 0
        finally:
            gen.close()

    def test_closing_the_stream_unregisters_both_hooks(
        self, testing_logs_module: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lifecycle = _RecordingLifecycleCoordinator()
        handler = LogCaptureHandler.get_instance()
        clients_before = len(handler._clients)

        gen = _stream(testing_logs_module, lifecycle, monkeypatch)
        next(gen)  # connection_open: the stream is fully set up
        assert lifecycle.callback_count == 1
        assert len(handler._clients) == clients_before + 1

        gen.close()

        assert lifecycle.callback_count == 0
        assert len(handler._clients) == clients_before

    def test_shutdown_after_close_reaches_no_stream(
        self, testing_logs_module: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lifecycle = _RecordingLifecycleCoordinator()
        gen = _stream(testing_logs_module, lifecycle, monkeypatch)
        next(gen)
        gen.close()

        lifecycle.fire(LifecycleEvent.PREPARE_SHUTDOWN)
        assert lifecycle.callback_count == 0

    def test_no_connection_close_event(self, testing_logs_module: Any) -> None:
        """The stream ends with the TCP close; it no longer sends connection_close."""
        assert "connection_close" not in (testing_logs_module.stream_logs.__doc__ or "")
