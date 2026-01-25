"""Tests for configuration management."""

from typing import Any


class TestSettings:
    """Test Settings configuration."""

    def test_settings_defaults(self) -> None:
        """Test default configuration values."""
        from app.config import Settings

        settings = Settings(_env_file=None)  # type: ignore[call-arg]

        assert settings.FLASK_ENV == "development"
        assert settings.DEBUG is True
        assert settings.SECRET_KEY == "dev-secret-key-change-in-production"

    def test_settings_from_env(self, monkeypatch: Any) -> None:
        """Test configuration from environment variables."""
        from app.config import Settings

        monkeypatch.setenv("FLASK_ENV", "production")
        monkeypatch.setenv("DEBUG", "false")
        monkeypatch.setenv("SECRET_KEY", "prod-secret")

        settings = Settings(_env_file=None)  # type: ignore[call-arg]

        assert settings.FLASK_ENV == "production"
        assert settings.DEBUG is False
        assert settings.SECRET_KEY == "prod-secret"

    def test_database_url_property(self) -> None:
        """Test SQLALCHEMY_DATABASE_URI property."""
        from app.config import Settings

        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            DATABASE_URL="postgresql://test:test@localhost/test"
        )

        assert settings.SQLALCHEMY_DATABASE_URI == "postgresql://test:test@localhost/test"

    def test_settings_extra_env_ignored(self, monkeypatch: Any) -> None:
        """Extra environment variables should be ignored."""
        from app.config import Settings

        monkeypatch.setenv("SOME_UNRELATED_SETTING", "42")

        settings = Settings(_env_file=None)  # type: ignore[call-arg]

        assert not hasattr(settings, "SOME_UNRELATED_SETTING")

    def test_is_testing_property(self) -> None:
        """Test is_testing property."""
        from app.config import Settings

        # Development mode
        settings = Settings(_env_file=None, FLASK_ENV="development")  # type: ignore[call-arg]
        assert settings.is_testing is False

        # Testing mode
        settings = Settings(_env_file=None, FLASK_ENV="testing")  # type: ignore[call-arg]
        assert settings.is_testing is True

    def test_is_production_property(self) -> None:
        """Test is_production property."""
        from app.config import Settings

        # Development mode with debug
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            FLASK_ENV="development",
            DEBUG=True
        )
        assert settings.is_production is False

        # Production mode
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            FLASK_ENV="production",
            DEBUG=False
        )
        assert settings.is_production is True

    def test_sqlalchemy_engine_options(self) -> None:
        """Test SQLALCHEMY_ENGINE_OPTIONS property."""
        from app.config import Settings

        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            DB_POOL_SIZE=10,
            DB_POOL_MAX_OVERFLOW=20,
            DB_POOL_TIMEOUT=60
        )

        options = settings.SQLALCHEMY_ENGINE_OPTIONS
        assert options["pool_size"] == 10
        assert options["max_overflow"] == 20
        assert options["pool_timeout"] == 60
        assert options["pool_pre_ping"] is True

    def test_set_engine_options_override(self) -> None:
        """Test set_engine_options_override for SQLite testing."""
        from app.config import Settings

        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            DATABASE_URL="sqlite:///:memory:",
            DB_POOL_SIZE=5
        )

        # Override for SQLite (no pool options)
        settings.set_engine_options_override({})

        options = settings.SQLALCHEMY_ENGINE_OPTIONS
        assert options == {}

    def test_cors_origins_default(self) -> None:
        """Test CORS_ORIGINS default value."""
        from app.config import Settings

        settings = Settings(_env_file=None)  # type: ignore[call-arg]

        assert isinstance(settings.CORS_ORIGINS, list)
        assert "http://localhost:3000" in settings.CORS_ORIGINS

    def test_s3_settings(self) -> None:
        """Test S3 configuration settings."""
        from app.config import Settings

        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            S3_ENDPOINT_URL="http://minio:9000",
            S3_ACCESS_KEY_ID="mykey",
            S3_SECRET_ACCESS_KEY="mysecret",
            S3_BUCKET_NAME="mybucket",
            S3_REGION="eu-west-1",
            S3_USE_SSL=True
        )

        assert settings.S3_ENDPOINT_URL == "http://minio:9000"
        assert settings.S3_ACCESS_KEY_ID == "mykey"
        assert settings.S3_SECRET_ACCESS_KEY == "mysecret"
        assert settings.S3_BUCKET_NAME == "mybucket"
        assert settings.S3_REGION == "eu-west-1"
        assert settings.S3_USE_SSL is True

    def test_oidc_settings(self) -> None:
        """Test OIDC configuration settings."""
        from app.config import Settings

        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            OIDC_ENABLED=True,
            OIDC_ISSUER_URL="https://auth.example.com/realms/app",
            OIDC_CLIENT_ID="my-client",
            OIDC_CLIENT_SECRET="secret123",
            OIDC_SCOPES="openid profile email",
            OIDC_AUDIENCE="my-client",
            OIDC_CLOCK_SKEW_SECONDS=60
        )

        assert settings.OIDC_ENABLED is True
        assert settings.OIDC_ISSUER_URL == "https://auth.example.com/realms/app"
        assert settings.OIDC_CLIENT_ID == "my-client"
        assert settings.OIDC_CLIENT_SECRET == "secret123"
        assert settings.OIDC_SCOPES == "openid profile email"
        assert settings.OIDC_AUDIENCE == "my-client"
        assert settings.OIDC_CLOCK_SKEW_SECONDS == 60

    def test_task_settings(self) -> None:
        """Test task configuration settings."""
        from app.config import Settings

        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            TASK_MAX_WORKERS=8,
            TASK_TIMEOUT_SECONDS=600,
            TASK_CLEANUP_INTERVAL_SECONDS=300
        )

        assert settings.TASK_MAX_WORKERS == 8
        assert settings.TASK_TIMEOUT_SECONDS == 600
        assert settings.TASK_CLEANUP_INTERVAL_SECONDS == 300
