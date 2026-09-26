"""SSE client helper for parsing Server-Sent Events in tests.

This module provides a reusable SSE client for integration tests that need to validate
SSE stream behavior. The client supports both strict and lenient parsing modes.
"""

import json
import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


class SSEClient:
    """Client for parsing Server-Sent Events streams in tests.

    Supports both strict and lenient parsing modes:
    - strict=True: Raises ValueError on malformed events or JSON parse errors
    - strict=False: Logs warnings and continues on parse errors
    """

    def __init__(self, url: str, strict: bool = True):
        self.url = url
        self.strict = strict

    def connect(self, timeout: float = 10.0) -> Any:
        """Connect to SSE endpoint and yield parsed events."""
        response = requests.get(self.url, stream=True, timeout=timeout)
        response.raise_for_status()

        event_name = None
        data_lines: list[str] = []

        for line in response.iter_lines(decode_unicode=True):
            if line == "":
                event = self._build_event(event_name, data_lines)
                if event is not None:
                    yield event

                event_name = None
                data_lines = []

            elif line.startswith("event:"):
                event_name = line[6:].strip()

            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())

            elif line.startswith(":"):
                continue

            elif line.startswith("id:") or line.startswith("retry:"):
                continue

            else:
                if self.strict:
                    raise ValueError(f"Malformed SSE line (no field:value format): {line}")
                else:
                    logger.warning(f"Ignoring malformed SSE line: {line}")

        # Handle stream ending without final blank line
        event = self._build_event(event_name, data_lines)
        if event is not None:
            yield event

    def _build_event(self, event_name: str | None, data_lines: list[str]) -> dict[str, Any] | None:
        """Turn one SSE frame into ``{"event", "data"}``, or None to skip it.

        The SSE Gateway sends application events unnamed, wrapped in a
        ``{"type": <name>, "payload": <data>}`` envelope; those are unwrapped so
        tests see the application's event name. The gateway's own ``ready``
        control signal (a named event with an empty data line) is skipped.
        """
        if event_name == "ready" or not data_lines:
            return None

        data_str = "\n".join(data_lines)
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError as e:
            if self.strict:
                raise ValueError(f"Failed to parse SSE event data as JSON: {data_str}") from e
            logger.warning(f"Failed to parse SSE event data as JSON: {data_str}, error: {e}")
            return {"event": event_name, "data": data_str}

        if event_name is None and isinstance(data, dict) and "type" in data and "payload" in data:
            return {"event": data["type"], "data": data["payload"]}

        return {"event": event_name, "data": data}
