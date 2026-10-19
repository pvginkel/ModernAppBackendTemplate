"""The stream under Waitress, the server production runs (``run.py``).

What a stream needs from its server is the server's own, and Flask's dev
server, which the other tests run on, is not the one production runs:

- Each frame reaches the browser when it is written, not when a buffer fills.
- A browser that has gone is noticed on the next write, a heartbeat at the
  latest: Waitress raises ``ClientDisconnected`` into the write, closing the
  body, whose ``finally`` unregisters the connection. That takes
  ``channel_request_lookahead`` (``run.py`` sets 1): by default Waitress
  stops reading a channel while its request runs, so it sees the close only
  when a write fails, which is the third heartbeat after it.
- An open stream outlives ``channel_timeout``. Waitress's channel sweep
  skips a channel with a request in progress, so a stream is never timed
  out; the heartbeats are what notice a gone browser, and what keep nginx's
  ``proxy_read_timeout`` from ending an idle stream.
- ``PREPARE_SHUTDOWN`` ends every stream, so the browsers reconnect to the
  next process.

The client is a raw socket speaking HTTP/1.1, as a browser does: the body
comes chunked, one chunk per frame, and a closed socket is a browser gone.
Every read has a timeout, so a test fails rather than hangs.
"""

import socket
import threading
import time
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask
from paste.translogger import TransLogger  # type: ignore[import-untyped]
from waitress import create_server

from app.services.sse_connection_manager import SSEConnectionManager, Target
from tests.conftest_infrastructure import (
    _build_test_app_settings,
    _create_test_app,
    _dispose_app,
)

READY = "event: ready\ndata:\n\n"
HEARTBEAT = ": heartbeat\n\n"

# The test settings' SSE_HEARTBEAT_INTERVAL
HEARTBEAT_INTERVAL = 1.0

# Well under a heartbeat interval: a frame this fast was not held back
PROMPT = 0.5

# Waitress's channel_timeout, in seconds; production's is 120
CHANNEL_TIMEOUT = 2


class _RawStream:
    """A browser's stream over a raw socket, its chunked body decoded."""

    def __init__(self, port: int, request_id: str) -> None:
        self._sock = socket.create_connection(("127.0.0.1", port), timeout=5.0)
        self._sock.sendall(
            f"GET /api/sse/stream?request_id={request_id} HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "Accept: text/event-stream\r\n"
            "\r\n".encode()
        )
        self._raw = b""
        self._body = b""
        self.ended = False
        self.head = self._read_head()

    def _recv(self, timeout: float) -> None:
        if timeout <= 0:
            raise TimeoutError("no frame in time")
        self._sock.settimeout(timeout)
        try:
            data = self._sock.recv(65536)
        except TimeoutError:
            raise TimeoutError("no frame in time") from None
        if not data:
            raise ConnectionError("the server closed the socket mid-body")
        self._raw += data

    def _read_head(self) -> str:
        deadline = time.monotonic() + 5.0
        while b"\r\n\r\n" not in self._raw:
            self._recv(deadline - time.monotonic())
        head, self._raw = self._raw.split(b"\r\n\r\n", 1)
        return head.decode("latin-1")

    def _decode_chunks(self) -> None:
        while not self.ended:
            line_end = self._raw.find(b"\r\n")
            if line_end < 0:
                return
            size = int(self._raw[:line_end], 16)
            chunk_end = line_end + 2 + size
            if len(self._raw) < chunk_end + 2:
                return
            if size == 0:
                self.ended = True
                return
            self._body += self._raw[line_end + 2 : chunk_end]
            self._raw = self._raw[chunk_end + 2 :]

    def frame(self, timeout: float = 2 * HEARTBEAT_INTERVAL) -> str | None:
        """The next frame, or None when the body has ended.

        Raises:
            TimeoutError: Neither came within the timeout
        """
        deadline = time.monotonic() + timeout
        while True:
            self._decode_chunks()
            separator = self._body.find(b"\n\n")
            if separator >= 0:
                frame = self._body[: separator + 2]
                self._body = self._body[separator + 2 :]
                return frame.decode()
            if self.ended:
                return None
            self._recv(deadline - time.monotonic())

    def close(self) -> None:
        """The browser going: the socket closed under the stream."""
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()


@pytest.fixture
def waitress(app: Flask) -> Generator[tuple[int, Flask, SSEConnectionManager]]:
    """The backend under Waitress, as ``run.py`` serves it.

    ``run.py``'s ``TransLogger`` wrapping, proxy settings and
    ``channel_request_lookahead``; four threads, and a ``channel_timeout`` of
    ``CHANNEL_TIMEOUT`` swept every second (the default sweeps every 30 s).
    The app is the test's own, so it may shut it down, on the session app's
    database (a stream never touches it); building it wires app.api to it
    for the test.

    Yields:
        ``(port, app, the app's connection manager)``
    """
    settings = app.container.config()
    waitress_app = _create_test_app(settings, _build_test_app_settings())
    # No version fetch from a frontend that is not there
    version_mock = patch.object(
        waitress_app.container.frontend_version_service(),
        "_fetch_frontend_version",
        return_value=None,
    )
    version_mock.start()

    socket_map: dict[int, Any] = {}
    server = create_server(
        TransLogger(waitress_app, setup_console_handler=False),
        map=socket_map,
        host="127.0.0.1",
        port=0,
        threads=4,
        channel_request_lookahead=1,
        channel_timeout=CHANNEL_TIMEOUT,
        cleanup_interval=1,
        trusted_proxy="*",
        trusted_proxy_count=1,
        trusted_proxy_headers="x-forwarded-for x-forwarded-proto x-forwarded-host",
    )
    stopping = threading.Event()

    def serve() -> None:
        try:
            server.run()
        except Exception:
            # Closing the sockets under the loop at teardown can raise in it
            if not stopping.is_set():
                raise

    server_thread = threading.Thread(target=serve, name="waitress", daemon=True)
    server_thread.start()

    try:
        yield (
            server.effective_port,  # type: ignore[union-attr]
            waitress_app,
            waitress_app.container.sse_connection_manager(),
        )
    finally:
        stopping.set()
        # Ends every stream still open, so the task threads come free
        _dispose_app(waitress_app)
        for dispatcher in list(socket_map.values()):
            dispatcher.handle_close()
        server.task_dispatcher.shutdown(timeout=5)
        server_thread.join(timeout=5)
        version_mock.stop()


def _open(port: int, request_id: str) -> _RawStream:
    stream = _RawStream(port, request_id)
    assert stream.head.startswith("HTTP/1.1 200"), stream.head
    assert "transfer-encoding: chunked" in stream.head.lower(), stream.head
    return stream


class TestSSEUnderWaitress:
    def test_frames_are_written_through(
        self, waitress: tuple[int, Flask, SSEConnectionManager]
    ) -> None:
        """``ready`` and an event each arrive at once, not with a later heartbeat."""
        port, _, manager = waitress

        started = time.monotonic()
        stream = _open(port, "req-flush")
        try:
            assert stream.frame(timeout=PROMPT) == READY
            assert time.monotonic() - started < PROMPT

            sent = time.monotonic()
            assert manager.send_event(
                Target.connection("req-flush"),
                {"n": 1},
                event_name="probe",
                service_type="test",
            )
            frame = stream.frame(timeout=PROMPT)
            elapsed = time.monotonic() - sent
        finally:
            stream.close()

        assert frame is not None and '"type":"probe"' in frame.replace(" ", "")
        assert elapsed < PROMPT, f"the event took {elapsed:.2f}s"

    def test_a_gone_client_is_unregistered_within_a_heartbeat(
        self, waitress: tuple[int, Flask, SSEConnectionManager]
    ) -> None:
        """The next write after the socket closes fails, and the observers hear of it."""
        port, _, manager = waitress
        disconnected = threading.Event()
        manager.register_on_disconnect(
            lambda request_id: request_id == "req-gone" and disconnected.set()
        )

        stream = _open(port, "req-gone")
        assert stream.frame() == READY
        assert manager.has_connection("req-gone")

        closed = time.monotonic()
        stream.close()

        assert disconnected.wait(timeout=4 * HEARTBEAT_INTERVAL), (
            "the disconnect observers did not fire"
        )
        elapsed = time.monotonic() - closed
        assert not manager.has_connection("req-gone")
        assert elapsed < HEARTBEAT_INTERVAL + PROMPT, f"noticed after {elapsed:.2f}s"

    def test_an_idle_stream_outlives_channel_timeout(
        self, waitress: tuple[int, Flask, SSEConnectionManager]
    ) -> None:
        """Past ``channel_timeout`` and a channel sweep, still open and delivering."""
        port, _, manager = waitress

        stream = _open(port, "req-idle")
        try:
            assert stream.frame() == READY
            # The sweep runs every second (the fixture's cleanup_interval)
            idle_until = time.monotonic() + CHANNEL_TIMEOUT + 1.5
            while time.monotonic() < idle_until:
                assert stream.frame() == HEARTBEAT

            assert manager.send_event(
                Target.connection("req-idle"),
                {"n": 2},
                event_name="probe",
                service_type="test",
            )
            frame = stream.frame()
            while frame == HEARTBEAT:
                frame = stream.frame()
        finally:
            stream.close()

        assert frame is not None and '"type":"probe"' in frame.replace(" ", "")

    def test_prepare_shutdown_ends_an_open_stream(
        self, waitress: tuple[int, Flask, SSEConnectionManager]
    ) -> None:
        """The app's own lifecycle coordinator shuts down: the body ends at once."""
        port, app, manager = waitress

        stream = _open(port, "req-shutdown")
        try:
            assert stream.frame() == READY

            shutdown = time.monotonic()
            app.container.lifecycle_coordinator().shutdown()

            frame = stream.frame(timeout=PROMPT)
            elapsed = time.monotonic() - shutdown
        finally:
            stream.close()

        assert frame is None, f"expected the end of the body, got {frame!r}"
        assert stream.ended
        assert elapsed < PROMPT, f"the stream ended after {elapsed:.2f}s"
        assert not manager.has_connection("req-shutdown")
