"""Spectree configuration for OpenAPI documentation."""

from typing import Any

from flask import Flask, redirect
from spectree import SpecTree

# Global Spectree instance that can be imported by API modules.
# This will be initialized by configure_spectree() before any API imports.
api: SpecTree = None  # type: ignore


def configure_spectree(
    app: Flask,
    title: str,
    version: str = "1.0.0",
    description: str = "",
) -> SpecTree:
    """Configure Spectree with proper Pydantic v2 integration.

    Args:
        app: Flask application instance
        title: API title for OpenAPI docs
        version: API version
        description: API description

    Returns:
        Configured SpecTree instance
    """
    global api

    api = SpecTree(
        backend_name="flask",
        title=title,
        version=version,
        description=description,
        path="api/docs",
        validation_error_status=400,
    )

    api.register(app)

    # Add redirect routes for convenience
    @app.route("/api/docs")
    @app.route("/api/docs/")
    def docs_redirect() -> Any:
        return redirect("/api/docs/swagger/", code=302)

    return api
