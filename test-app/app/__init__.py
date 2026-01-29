"""test-app Flask application."""

from flask import Flask

from common.core.spectree import configure_spectree


def configure_app_spectree(app: Flask) -> None:
    """Configure Spectree with app-specific settings."""
    configure_spectree(
        app,
        title="test-app API",
        version="1.0.0",
        description="Test application",
    )


def get_wire_modules() -> list[str]:
    """Return list of modules to wire for dependency injection."""
    return [
        "app.api",
        "common.health.routes",
        "common.metrics.routes",

        "common.sse.routes",

        # Add your API modules here as you create them
        # "app.api.your_module",
    ]
