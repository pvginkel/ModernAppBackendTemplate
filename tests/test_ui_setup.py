"""Tests for GET /api/ui-setup."""

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.config import Settings


def _override_settings(app: Flask, **update: object) -> None:
    """Swap the session app's settings; session_app_isolation restores them."""
    settings: Settings = app.container.config()
    app.container.config.override(settings.model_copy(update=update))


class TestUiSetup:
    def test_reports_stage_and_internals_fact(self, client: FlaskClient):
        response = client.get("/api/ui-setup")

        assert response.status_code == 200
        assert response.json == {"stage": "local-tst", "expose_internals": True}

    @pytest.mark.parametrize(
        ("homelab_internal", "expected"), [(False, False), (True, True)]
    )
    def test_on_prd_internals_follow_homelab_internal(
        self, app: Flask, client: FlaskClient, homelab_internal: bool, expected: bool
    ):
        _override_settings(app, stage=Settings(stage="prd").stage, homelab_internal=homelab_internal)

        response = client.get("/api/ui-setup")

        assert response.json == {"stage": "prd", "expose_internals": expected}
