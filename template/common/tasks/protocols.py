"""Protocols for task service dependencies."""

from abc import ABC, abstractmethod
from typing import Any


class BroadcasterProtocol(ABC):
    """Protocol for broadcasting task events.

    Implementations:
    - ConnectionManager: Broadcasts via SSE Gateway
    - NullBroadcaster: No-op for apps without SSE
    """

    @abstractmethod
    def broadcast_task_event(
        self, task_id: str, event_type: str, data: dict[str, Any]
    ) -> bool:
        """Broadcast a task event to connected clients.

        Args:
            task_id: Unique task identifier
            event_type: Event type (started, progress, completed, failed)
            data: Event payload

        Returns:
            True if broadcast succeeded, False otherwise
        """
        pass
