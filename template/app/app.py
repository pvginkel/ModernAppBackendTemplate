"""Custom Flask application class with typed container attribute."""

from flask import Flask

from app.services.container import ServiceContainer


class App(Flask):
    container: ServiceContainer
