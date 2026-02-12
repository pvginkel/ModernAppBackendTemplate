"""Tests for configuration system."""

from app.config import Settings


def test_settings_load_defaults():
    """Settings can be created with default values."""
    settings = Settings(
        database_url="sqlite:///:memory:",
        secret_key="test",
        flask_env="testing",
    )
    assert settings.debug is True  # default is True
    assert settings.flask_env == "testing"


def test_flask_config_from_settings():
    """to_flask_config() produces a FlaskConfig with the correct secret key."""
    settings = Settings(
        database_url="sqlite:///:memory:",
        secret_key="test-key",
        debug=True,
        flask_env="testing",
    )
    flask_config = settings.to_flask_config()
    assert flask_config.SECRET_KEY == "test-key"


def test_testing_environment_detection():
    """flask_env='testing' enables is_testing property."""
    settings = Settings(
        database_url="sqlite:///:memory:",
        secret_key="test",
        flask_env="testing",
    )
    assert settings.is_testing is True

    settings2 = Settings(
        database_url="sqlite:///:memory:",
        secret_key="test",
        flask_env="development",
    )
    assert settings2.is_testing is False
