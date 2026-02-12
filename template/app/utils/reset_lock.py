"""Reset lock utility for concurrency control during database reset operations."""

from __future__ import annotations

import threading


class ResetLock:
    """Thread-safe lock for controlling database reset operations."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reset_in_progress = False

    def acquire_reset(self) -> bool:
        """
        Try to acquire reset lock.

        Returns:
            True if lock was acquired, False if reset already in progress
        """
        with self._lock:
            if self._reset_in_progress:
                return False
            self._reset_in_progress = True
            return True

    def release_reset(self) -> None:
        """Release the reset lock."""
        with self._lock:
            self._reset_in_progress = False

    def is_resetting(self) -> bool:
        """
        Check if reset is in progress.

        Returns:
            True if reset operation is currently in progress
        """
        with self._lock:
            return self._reset_in_progress

    def __enter__(self) -> bool:
        """Context manager entry - try to acquire lock."""
        return self.acquire_reset()

    def __exit__(
        self,
        exc_type: type | None,
        exc_val: Exception | None,
        exc_tb: object | None,
    ) -> None:
        """Context manager exit - always release lock."""
        self.release_reset()
