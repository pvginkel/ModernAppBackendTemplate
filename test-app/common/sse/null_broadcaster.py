"""Null broadcaster implementation for apps without SSE."""

from typing import Any

from common.tasks.protocols import BroadcasterProtocol


class NullBroadcaster(BroadcasterProtocol):
    """No-op broadcaster for when SSE is disabled.

    Task events are silently discarded. Tasks still run and complete,
    but no real-time updates are sent to clients.
    """

    def broadcast_task_event(
        self, task_id: str, event_type: str, data: dict[str, Any]
    ) -> bool:
        """Silently discard the event.

        Returns:
            Always returns False (no active connections)
        """
        return False
