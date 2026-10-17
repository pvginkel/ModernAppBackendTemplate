"""Tests for reject_if_not_testing, the guard on every testing blueprint."""

from flask import Flask

from app.config import Settings


def _guard_with(app: Flask, **update: object) -> object:
    # Imported once the app exists: importing app.api before it does fails.
    from app.api.testing_guard import reject_if_not_testing

    settings: Settings = app.container.config()
    app.container.config.override(settings.model_copy(update=update))
    with app.test_request_context():
        return reject_if_not_testing()


class TestTestingGuard:
    def test_allows_testing_mode(self, app: Flask):
        assert _guard_with(app) is None

    def test_rejects_outside_testing_mode(self, app: Flask):
        result = _guard_with(app, flask_env="production")

        assert isinstance(result, tuple)
        assert result[1] == 400

    def test_rejects_testing_mode_on_prd(self, app: Flask):
        result = _guard_with(app, stage=Settings(stage="prd").stage)

        assert isinstance(result, tuple)
        assert result[1] == 400
        assert "STAGE" in result[0].get_json()["details"]["message"]
