"""A refused connect reaches the client as the gateway's ``rejected`` event.

The connect callback refuses with 401 or 403, and the SSE Gateway turns the
refusal into an in-stream ``rejected`` event, ``{"status", "message"}``, which
the frontend's SSE client parses to decide between a session refresh and the
"connection lost" toast. The gateway builds that event from the callback's
status code and reason phrase alone, never from its body, so the body can be
the error envelope every other error answers with. These tests pin the event
the browser gets, through a real gateway.
"""

import uuid

import pytest
import requests

from tests.integration.sse_client_helper import SSEClient


class TestSSEGatewayRejection:
    @pytest.mark.parametrize(
        ("status", "message"), [(401, "UNAUTHORIZED"), (403, "FORBIDDEN")]
    )
    def test_refused_connect_is_a_rejected_event(
        self,
        sse_server: tuple[str, object],
        sse_gateway_server: str,
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
            f"{sse_gateway_server}/api/sse/stream?request_id={uuid.uuid4()}",
            strict=True,
        )
        # The gateway ends the stream after the event.
        events = list(client.connect(timeout=10.0))

        assert events == [
            {"event": "rejected", "data": {"status": status, "message": message}}
        ]
