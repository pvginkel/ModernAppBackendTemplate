"""A refused stream reaches the client as an in-stream ``rejected`` event.

The stream answers a refusal (400, 401, 403) with ``200`` and one named event
``rejected``, ``{"status", "message"}``, then ends: an ``EventSource`` does
not reconnect a non-200, and the frontend's SSE client parses the event to
decide between a session refresh and the "connection lost" toast. These tests
pin the event the browser gets, over a real HTTP connection.
"""

import uuid

import pytest
import requests

from tests.integration.sse_client_helper import SSEClient


class TestSSERejection:
    @pytest.mark.parametrize(
        ("status", "message"), [(401, "UNAUTHORIZED"), (403, "FORBIDDEN")]
    )
    def test_refused_connect_is_a_rejected_event(
        self,
        sse_server: tuple[str, object],
        status: int,
        message: str,
    ):
        server_url, _ = sse_server
        armed = requests.post(
            f"{server_url}/api/testing/sse/force-callback-error",
            params={"status": status},
            timeout=5.0,
        )
        assert armed.status_code == 204

        client = SSEClient(
            f"{server_url}/api/sse/stream?request_id={uuid.uuid4()}",
            strict=True,
        )
        # The stream ends after the event.
        events = list(client.connect(timeout=10.0))

        assert events == [
            {"event": "rejected", "data": {"status": status, "message": message}}
        ]

    def test_request_id_with_a_colon_is_a_rejected_event(
        self, sse_server: tuple[str, object]
    ):
        server_url, _ = sse_server
        client = SSEClient(f"{server_url}/api/sse/stream?request_id=a:b", strict=True)

        events = list(client.connect(timeout=10.0))

        assert events == [
            {"event": "rejected", "data": {"status": 400, "message": "BAD REQUEST"}}
        ]
