"""Fixtures for the SSE integration tests."""

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _wire_sse_server(sse_server: tuple[str, Any], session_app_isolation: None) -> None:
    """Point the API's injections at the SSE server's app during each test.

    Wiring is per module, not per app, so one container serves the requests
    of every app in the process. The SSE server serves these tests; the
    session app gets the wiring back at the start of the next test
    (``session_app_isolation``, which this runs after).
    """
    _, sse_app = sse_server
    sse_app.container.wire(packages=["app.api"])
