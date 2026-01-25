"""Custom Flask application class with container reference."""

from typing import TYPE_CHECKING

from flask import Flask

if TYPE_CHECKING:
    from common.core.container import CommonContainer


class App(Flask):
    """Custom Flask application with typed container attribute.

    This class extends Flask to provide type-safe access to the
    dependency injection container.
    """

    container: "CommonContainer"
