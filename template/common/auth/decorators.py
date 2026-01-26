"""Authentication decorators for Flask routes."""

from collections.abc import Callable
from typing import Any


def public(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to mark an endpoint as publicly accessible (no authentication required).

    Usage:
        @some_bp.route("/health")
        @public
        def health_check():
            return {"status": "healthy"}
    """
    func.is_public = True  # type: ignore[attr-defined]
    return func
