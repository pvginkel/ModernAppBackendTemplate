"""Tests for configuration management."""

import logging
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

import run
from app.app_config import AppSettings
from app.config import (
    TEST_DB_POOL_MAX_OVERFLOW,
    TEST_DB_POOL_SIZE,
    Environment,
    Sensitive,
    Settings,
    dump_config_for_logging,
    is_sensitive,
)
from app.exceptions import ConfigurationError
from tests.conftest_infrastructure import _build_test_settings


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
        database_url="postgresql+psycopg://app:secret@db.example:5432/app",
        secret_key="test-key",
        flask_env="testing",
    )

    assert settings.database_url == "postgresql+psycopg://app:secret@db.example:5432/app"
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
        "database_url": "postgresql+psycopg://app:secret@db.example:5432/app",
        "sqlalchemy_engine_options": {"pool_size": 2},
    })

    assert updated.database_url == "postgresql+psycopg://app:secret@db.example:5432/app"
    assert updated.sqlalchemy_engine_options["pool_size"] == 2


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


class TestTestModePool:
    """FLASK_ENV=testing takes the small test pool unless the pool is set."""

    @pytest.fixture(autouse=True)
    def _no_pool_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DB_POOL_SIZE", raising=False)
        monkeypatch.delenv("DB_POOL_MAX_OVERFLOW", raising=False)

    @staticmethod
    def _pool(settings: Settings) -> tuple[int, int, int, int]:
        options = settings.sqlalchemy_engine_options
        return (
            settings.db_pool_size,
            settings.db_pool_max_overflow,
            options["pool_size"],
            options["max_overflow"],
        )

    def test_testing_takes_the_test_pool(self):
        settings = Settings.load(Environment(FLASK_ENV="testing"))

        assert self._pool(settings) == (
            TEST_DB_POOL_SIZE,
            TEST_DB_POOL_MAX_OVERFLOW,
            TEST_DB_POOL_SIZE,
            TEST_DB_POOL_MAX_OVERFLOW,
        )

    @pytest.mark.parametrize("flask_env", ["development", "production"])
    def test_other_environments_keep_the_default_pool(self, flask_env: str):
        settings = Settings.load(Environment(FLASK_ENV=flask_env))

        assert self._pool(settings) == (20, 30, 20, 30)

    def test_explicit_pool_size_wins_on_its_own(self):
        settings = Settings.load(Environment(FLASK_ENV="testing", DB_POOL_SIZE=7))

        assert self._pool(settings) == (7, TEST_DB_POOL_MAX_OVERFLOW, 7, TEST_DB_POOL_MAX_OVERFLOW)

    def test_explicit_max_overflow_wins_on_its_own(self):
        settings = Settings.load(Environment(FLASK_ENV="testing", DB_POOL_MAX_OVERFLOW=9))

        assert self._pool(settings) == (TEST_DB_POOL_SIZE, 9, TEST_DB_POOL_SIZE, 9)

    def test_pool_from_the_environment_counts_as_explicit(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("FLASK_ENV", "testing")
        monkeypatch.setenv("DB_POOL_SIZE", "4")

        settings = Settings.load()

        assert self._pool(settings) == (4, TEST_DB_POOL_MAX_OVERFLOW, 4, TEST_DB_POOL_MAX_OVERFLOW)

    def test_explicit_default_value_still_wins(self):
        """Setting the pool to the production default is a choice, not a default."""
        settings = Settings.load(Environment(FLASK_ENV="testing", DB_POOL_SIZE=20))

        assert self._pool(settings)[0] == 20

    def test_pytest_settings_carry_the_whole_engine_options(self):
        """A partial options dict would leave the engine on SQLAlchemy's own pool."""
        options = _build_test_settings().sqlalchemy_engine_options

        assert options == {
            "pool_size": TEST_DB_POOL_SIZE,
            "max_overflow": TEST_DB_POOL_MAX_OVERFLOW,
            "pool_timeout": 10,
            "pool_pre_ping": True,
            "echo_pool": False,
        }


class TestSensitiveMarks:
    """Every secret on Settings carries the Sensitive mark.

    A new secret field that is not in this list and not marked shows up in
    the boot log in clear text; add it here and mark it.
    """

    SETTINGS_SECRETS = {
        "secret_key",
        "drain_auth_key",
        "database_url",
        "oidc_client_secret",
        "s3_access_key_id",
        "s3_secret_access_key",
        "sse_callback_secret",
    }

    def test_every_secret_is_marked(self):
        fields = Settings.model_fields
        unmarked = {name for name in self.SETTINGS_SECRETS if not is_sensitive(fields[name])}
        assert unmarked == set()

    def test_nothing_else_is_marked(self):
        marked = {name for name, info in Settings.model_fields.items() if is_sensitive(info)}
        assert marked == self.SETTINGS_SECRETS

    def test_marker_instance_counts_too(self):
        from typing import Annotated

        class Model(BaseModel):
            token: Annotated[str, Sensitive()] = "t"

        assert is_sensitive(Model.model_fields["token"])


class TestDumpForLogging:
    def test_settings_dump_covers_every_field(self):
        assert set(Settings().dump_for_logging()) == set(Settings.model_fields)

    def test_secrets_are_redacted(self):
        settings = Settings(
            secret_key="super-secret-key",
            drain_auth_key="drain-key",
            oidc_client_secret="client-secret",
            s3_secret_access_key="s3-secret",
            sse_callback_secret="sse-secret",
        )
        dump = settings.dump_for_logging()

        for name in ("secret_key", "drain_auth_key", "oidc_client_secret",
                     "s3_secret_access_key", "sse_callback_secret"):
            assert dump[name] == "***"
        for secret in ("super-secret-key", "drain-key", "client-secret", "s3-secret", "sse-secret"):
            assert not any(secret in value for value in dump.values())

    def test_database_url_shows_everything_but_the_password(self):
        settings = Settings(database_url="postgresql+psycopg://app:pa55word@db:5432/app")

        dump = settings.dump_for_logging()

        assert "pa55word" not in dump["database_url"]
        assert dump["database_url"] == repr("postgresql+psycopg://app:***@db:5432/app")

    def test_other_fields_show_their_repr(self):
        settings = Settings(flask_env="testing", task_max_workers=7)
        dump = settings.dump_for_logging()

        assert dump["flask_env"] == "'testing'"
        assert dump["task_max_workers"] == "7"
        assert dump["log_level"] == "'INFO'"

    def test_app_settings_dump_covers_every_field(self):
        assert set(AppSettings().dump_for_logging()) == set(AppSettings.model_fields)

    def test_dump_config_for_logging_on_any_model(self):
        from typing import Annotated

        class Model(BaseModel):
            name: str = "x"
            token: Annotated[str, Sensitive] = "t0ken"

        assert dump_config_for_logging(Model()) == {"name": "'x'", "token": "***"}


class TestBootLogging:
    def test_app_settings_with_a_dump_are_logged(self, caplog: pytest.LogCaptureFixture):
        app_config = SimpleNamespace(dump_for_logging=lambda: {"api_url": "'http://x'"})
        app = SimpleNamespace(container=SimpleNamespace(app_config=lambda: app_config))

        with caplog.at_level(logging.INFO, logger="run"):
            run._log_app_config(app)  # type: ignore[arg-type]

        assert "api_url" in caplog.text

    def test_app_settings_without_a_dump_are_skipped(self, caplog: pytest.LogCaptureFixture):
        """An app-owned AppSettings from before the Sensitive marks is not dumped."""
        app_config = SimpleNamespace(api_key="must-not-appear")
        app = SimpleNamespace(container=SimpleNamespace(app_config=lambda: app_config))

        with caplog.at_level(logging.INFO, logger="run"):
            run._log_app_config(app)  # type: ignore[arg-type]

        assert "must-not-appear" not in caplog.text
        assert "app_config" not in caplog.text


class TestLogLevel:
    def test_default_is_info(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        assert Settings.load(Environment(_env_file=None)).log_level == "INFO"  # type: ignore[call-arg]

    def test_log_level_from_the_environment(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        assert Settings.load().log_level == "DEBUG"

    @pytest.mark.parametrize(
        ("name", "level"),
        [
            ("DEBUG", logging.DEBUG),
            ("info", logging.INFO),
            ("Warning", logging.WARNING),
            ("ERROR", logging.ERROR),
            ("critical", logging.CRITICAL),
        ],
    )
    def test_level_names_resolve_case_insensitively(self, name: str, level: int):
        from app.utils.log_formatter import resolve_log_level

        assert resolve_log_level(name) == level

    @pytest.mark.parametrize("name", ["verbose", "", "basicConfig"])
    def test_unknown_level_falls_back_to_info(self, name: str):
        from app.utils.log_formatter import resolve_log_level

        assert resolve_log_level(name) == logging.INFO


class TestExtraFieldFormatter:
    def _record(self, **extra: object) -> logging.LogRecord:
        record = logging.LogRecord("app.x", logging.INFO, __file__, 1, "Imported %s", ("item",), None)
        for key, value in extra.items():
            setattr(record, key, value)
        return record

    def test_extra_fields_follow_the_message_sorted(self):
        from app.utils.log_formatter import ExtraFieldFormatter

        formatter = ExtraFieldFormatter("%(name)s: %(message)s")

        line = formatter.format(self._record(source="csv", item_id=42))

        assert line == "app.x: Imported item | item_id=42 source=csv"

    def test_no_extra_fields_leaves_the_line_alone(self):
        from app.utils.log_formatter import ExtraFieldFormatter

        formatter = ExtraFieldFormatter("%(name)s: %(message)s")

        assert formatter.format(self._record()) == "app.x: Imported item"
