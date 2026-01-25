"""Connection manager for SSE Gateway integration.

This service manages the bidirectional mapping between request IDs
and SSE Gateway tokens. It handles connection lifecycle events and provides
an interface for sending events (targeted or broadcast) via HTTP to the SSE Gateway.
"""

import json
import logging
import threading
from collections.abc import Callable
from time import perf_counter
from typing import TYPE_CHECKING, Any

import requests

from common.tasks.protocols import BroadcasterProtocol

if TYPE_CHECKING:
    from common.metrics.service import MetricsServiceProtocol

logger = logging.getLogger(__name__)


class ConnectionManager(BroadcasterProtocol):
    """Manages SSE Gateway token mappings and event delivery."""

    def __init__(
        self,
        gateway_url: str,
        metrics_service: "MetricsServiceProtocol",
        http_timeout: float = 5.0,
    ):
        """Initialize ConnectionManager.

        Args:
            gateway_url: Base URL for SSE Gateway (e.g., "http://localhost:3001")
            metrics_service: Metrics service for observability
            http_timeout: Timeout for HTTP requests to SSE Gateway in seconds
        """
        self.gateway_url = gateway_url.rstrip("/")
        self.metrics_service = metrics_service
        self.http_timeout = http_timeout

        # Bidirectional mappings
        self._connections: dict[str, dict[str, str]] = {}
        self._token_to_request_id: dict[str, str] = {}

        # Observer callbacks for connection events
        self._on_connect_callbacks: list[Callable[[str], None]] = []

        # Thread safety
        self._lock = threading.RLock()

    def register_on_connect(self, callback: Callable[[str], None]) -> None:
        """Register a callback to be notified when connections are established."""
        with self._lock:
            self._on_connect_callbacks.append(callback)

    def on_connect(self, request_id: str, token: str, url: str) -> None:
        """Register a new connection from SSE Gateway.

        Args:
            request_id: Plain request ID
            token: Gateway-generated connection token
            url: Original client request URL
        """
        old_token_to_close: str | None = None

        with self._lock:
            existing = self._connections.get(request_id)
            if existing:
                old_token_to_close = existing["token"]
                self._token_to_request_id.pop(old_token_to_close, None)

            self._connections[request_id] = {"token": token, "url": url}
            self._token_to_request_id[token] = request_id

            logger.info(
                f"Registered SSE Gateway connection: request_id={request_id}"
            )

        if old_token_to_close:
            self._close_connection_internal(old_token_to_close, request_id)

        # Notify observers
        with self._lock:
            callbacks_to_notify = list(self._on_connect_callbacks)

        for callback in callbacks_to_notify:
            try:
                callback(request_id)
            except Exception as e:
                logger.warning(f"Observer callback failed: {e}")

    def on_disconnect(self, token: str) -> None:
        """Handle disconnect callback from SSE Gateway."""
        with self._lock:
            request_id = self._token_to_request_id.get(token)
            if not request_id:
                return

            current_conn = self._connections.get(request_id)
            if not current_conn or current_conn["token"] != token:
                self._token_to_request_id.pop(token, None)
                return

            del self._connections[request_id]
            del self._token_to_request_id[token]

            logger.info(
                f"Unregistered SSE Gateway connection: request_id={request_id}"
            )

    def has_connection(self, request_id: str) -> bool:
        """Check if a connection exists for the given request_id."""
        with self._lock:
            return request_id in self._connections

    def broadcast_task_event(
        self, task_id: str, event_type: str, data: dict[str, Any]
    ) -> bool:
        """Broadcast a task event to all connections (BroadcasterProtocol)."""
        return self.send_event(None, data, "task_event", "task")

    def send_event(
        self,
        request_id: str | None,
        event_data: dict[str, Any],
        event_name: str,
        service_type: str,
    ) -> bool:
        """Send an event to the SSE Gateway for delivery to client(s).

        Args:
            request_id: Request identifier for targeted send, or None for broadcast
            event_data: Event payload (will be JSON-serialized)
            event_name: SSE event name
            service_type: Service type for metrics

        Returns:
            True if event sent successfully to at least one connection
        """
        if request_id is None:
            # Broadcast mode
            with self._lock:
                tokens_to_send = [
                    (req_id, conn["token"])
                    for req_id, conn in self._connections.items()
                ]

            if not tokens_to_send:
                return False

            success_count = 0
            for req_id, token in tokens_to_send:
                if self._send_event_to_token(
                    token, event_data, event_name, service_type, req_id
                ):
                    success_count += 1

            return success_count > 0

        # Targeted mode
        with self._lock:
            conn_info = self._connections.get(request_id)
            if not conn_info:
                return False
            token = conn_info["token"]

        return self._send_event_to_token(
            token, event_data, event_name, service_type, request_id
        )

    def _send_event_to_token(
        self,
        token: str,
        event_data: dict[str, Any],
        event_name: str,
        service_type: str,
        request_id: str | None = None,
    ) -> bool:
        """Send an event to a specific token via SSE Gateway."""
        start_time = perf_counter()

        try:
            send_request = {
                "token": token,
                "event": {"name": event_name, "data": json.dumps(event_data)},
                "close": False,
            }

            url = f"{self.gateway_url}/internal/send"
            response = requests.post(
                url,
                json=send_request,
                timeout=self.http_timeout,
                headers={"Content-Type": "application/json"},
            )

            if response.status_code == 404:
                if request_id:
                    with self._lock:
                        self._connections.pop(request_id, None)
                        self._token_to_request_id.pop(token, None)
                return False

            return response.status_code == 200

        except requests.RequestException as e:
            logger.error(f"Failed to send event to SSE Gateway: {e}")
            return False

        finally:
            duration = perf_counter() - start_time
            logger.debug(f"SSE Gateway send took {duration:.3f}s")

    def _close_connection_internal(self, token: str, request_id: str) -> None:
        """Close a connection via SSE Gateway."""
        try:
            send_request = {"token": token, "event": None, "close": True}
            url = f"{self.gateway_url}/internal/send"
            requests.post(
                url,
                json=send_request,
                timeout=self.http_timeout,
                headers={"Content-Type": "application/json"},
            )
        except requests.RequestException:
            pass  # Best effort
