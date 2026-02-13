"""
Spectree configuration with Pydantic v2 compatibility.
"""
from typing import Any

from flask import Flask
from spectree import SpecTree

from app.consts import API_DESCRIPTION, API_TITLE

# Global Spectree instance that can be imported by API modules.
# This will be initialized by configure_spectree() before any imports of the API modules.
# The type is being ignored to not over complicate the code and
# make the type checker happy.
api: SpecTree = None  # type: ignore


def configure_spectree(app: Flask) -> SpecTree:
    """
    Configure Spectree with proper Pydantic v2 integration and custom settings.

    Returns:
        SpecTree: Configured Spectree instance
    """
    global api

    # Create Spectree instance with Flask backend
    api = SpecTree(
        backend_name="flask",
        title=API_TITLE,
        version="1.0.0",
        description=API_DESCRIPTION,
        path="api/docs",  # OpenAPI docs available at /api/docs
        validation_error_status=400,
    )

    # Register the SpecTree with the Flask app to create documentation routes
    api.register(app)

    # Add redirect routes for convenience
    from flask import redirect

    @app.route("/api/docs")
    @app.route("/api/docs/")
    def docs_redirect() -> Any:
        return redirect("/api/docs/swagger/", code=302)

    return api

