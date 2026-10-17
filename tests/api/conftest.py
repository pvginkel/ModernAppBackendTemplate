"""Fixtures for the API tests."""

from collections.abc import Generator

import pytest
from dependency_injector import providers
from flask import Flask
from flask.testing import FlaskClient

from app.services.auth_service import AuthService


@pytest.fixture
def roles_oidc_app(oidc_app: Flask) -> Generator[Flask]:
    """The OIDC app with roles configured as most apps do: no read_role.

    ``write_role="editor"`` gates writes, ``pipeline`` is an additional role,
    and GETs have no role gate. A caller holding neither role gets 403
    everywhere.
    """
    container = oidc_app.container
    auth_service = AuthService(
        config=container.config(),
        write_role="editor",
        additional_roles=["pipeline"],
    )
    container.auth_service.override(providers.Object(auth_service))
    try:
        yield oidc_app
    finally:
        container.auth_service.reset_override()


@pytest.fixture
def roles_oidc_client(roles_oidc_app: Flask) -> FlaskClient:
    """Test client for the role-configured OIDC app."""
    return roles_oidc_app.test_client()
