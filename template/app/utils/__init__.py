"""Utility functions and helpers."""

import uuid

from flask import g, has_request_context, request


def get_current_correlation_id() -> str | None:
    """Get the current request's correlation ID."""
    if not has_request_context():
        return None
    return getattr(g, "correlation_id", None)


def _init_request_id(app):
    """Register before_request handler to set correlation ID from X-Request-ID header."""

    @app.before_request
    def set_request_id():
        g.correlation_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())


def ensure_request_id_from_query(request_id: str | None) -> None:
    """Set correlation ID from query parameter for SSE streams.

    SSE connections can't set HTTP headers, so the request_id comes via query param.
    """
    if request_id and has_request_context():
        g.correlation_id = request_id
