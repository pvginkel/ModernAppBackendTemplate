"""Tests for configuration management."""

import pytest

from app.config import Environment, Settings
from app.exceptions import ConfigurationError


def test_environment_defaults(monkeypatch):
    """Test Environment loads default values."""
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("DEBUG", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)

    env = Environment()

    assert env.FLASK_ENV == "development"
    assert env.DEBUG is True
    assert env.SECRET_KEY == "dev-secret-key-change-in-production"


def test_environment_from_env_vars(monkeypatch):
    """Test Environment loads from environment variables."""
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("SECRET_KEY", "prod-secret")

    env = Environment()

    assert env.FLASK_ENV == "production"
    assert env.DEBUG is False
    assert env.SECRET_KEY == "prod-secret"


def test_settings_load_default_values(monkeypatch):
    """Test Settings.load() with default environment."""
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("DEBUG", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)

    settings = Settings.load()

    assert settings.flask_env == "development"
    assert settings.debug is True
    assert settings.secret_key == "dev-secret-key-change-in-production"


def test_settings_load_production_heartbeat():
    """Test Settings.load() sets heartbeat to 30 in production."""
    env = Environment(FLASK_ENV="production")
    settings = Settings.load(env)

    assert settings.sse_heartbeat_interval == 30


def test_settings_load_engine_options():
    """Test Settings.load() builds engine options from pool settings."""
    env = Environment(DB_POOL_SIZE=10, DB_POOL_MAX_OVERFLOW=20, DB_POOL_TIMEOUT=15)
    settings = Settings.load(env)

    assert settings.sqlalchemy_engine_options["pool_size"] == 10
    assert settings.sqlalchemy_engine_options["max_overflow"] == 20
    assert settings.sqlalchemy_engine_options["pool_timeout"] == 15
    assert settings.sqlalchemy_engine_options["pool_pre_ping"] is True


def test_settings_direct_construction():
    """Test constructing Settings directly (for tests)."""
    settings = Settings(
        database_url="sqlite://",
        secret_key="test-key",
        flask_env="testing",
    )

    assert settings.database_url == "sqlite://"
    assert settings.secret_key == "test-key"


def test_to_flask_config():
    """Test Settings.to_flask_config() creates FlaskConfig."""
    settings = Settings.load()
    flask_config = settings.to_flask_config()

    assert flask_config.SECRET_KEY == settings.secret_key
    assert flask_config.SQLALCHEMY_DATABASE_URI == settings.database_url
    assert flask_config.SQLALCHEMY_TRACK_MODIFICATIONS is False


def test_settings_extra_env_ignored(monkeypatch):
    """Extra environment variables should be ignored."""
    monkeypatch.setenv("SOME_UNRELATED_SETTING", "42")
    env = Environment()
    assert not hasattr(env, "SOME_UNRELATED_SETTING")


def test_settings_is_testing_property():
    """Test is_testing property."""
    settings = Settings.load(Environment(FLASK_ENV="testing"))
    assert settings.is_testing is True

    settings = Settings.load(Environment(FLASK_ENV="development"))
    assert settings.is_testing is False


def test_settings_is_production_property():
    """Test is_production property."""
    settings = Settings(flask_env="production")
    assert settings.is_production is True

    settings = Settings(flask_env="development")
    assert settings.is_production is False


def test_settings_model_copy_update():
    """Test Settings.model_copy with update dict (test fixture pattern)."""
    base_settings = Settings.load()
    updated = base_settings.model_copy(update={
        "database_url": "sqlite://",
        "sqlalchemy_engine_options": {"poolclass": "StaticPool"},
    })

    assert updated.database_url == "sqlite://"
    assert updated.sqlalchemy_engine_options["poolclass"] == "StaticPool"


class TestValidateProductionConfig:
    """Tests for production configuration validation."""

    def test_development_defaults_pass(self):
        """Development defaults should pass validation."""
        settings = Settings()
        settings.validate_production_config()

    def test_production_default_secret_key_fails(self):
        """Production with default SECRET_KEY should fail."""
        settings = Settings(flask_env="production")
        with pytest.raises(ConfigurationError, match="SECRET_KEY"):
            settings.validate_production_config()

    def test_production_custom_secret_key_passes(self):
        """Production with a custom SECRET_KEY passes that check."""
        settings = Settings(
            flask_env="production",
            secret_key="my-secure-production-key",
        )
        settings.validate_production_config()

    def test_oidc_enabled_missing_issuer_url_fails(self):
        """OIDC enabled without OIDC_ISSUER_URL should fail."""
        settings = Settings(
            oidc_enabled=True,
            oidc_issuer_url=None,
            oidc_client_id="my-client",
            oidc_client_secret="my-secret",
        )
        with pytest.raises(ConfigurationError, match="OIDC_ISSUER_URL"):
            settings.validate_production_config()

    def test_oidc_enabled_all_settings_present_passes(self):
        """OIDC enabled with all settings present should pass."""
        settings = Settings(
            oidc_enabled=True,
            oidc_issuer_url="https://auth.example.com/realms/test",
            oidc_client_id="my-client",
            oidc_client_secret="my-secret",
        )
        settings.validate_production_config()

    def test_oidc_disabled_missing_settings_passes(self):
        """OIDC disabled should not require OIDC settings."""
        settings = Settings(oidc_enabled=False)
        settings.validate_production_config()
