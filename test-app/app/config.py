"""Configuration management using Pydantic settings.

Two-layer configuration system:
1. Environment: Loads raw values from environment variables (UPPER_CASE)
2. Settings: Clean application settings with lowercase fields and derived values
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_SECRET_KEY = "dev-secret-key-change-in-production"


class Environment(BaseSettings):
    """Raw environment variable loading."""

    model_config = SettingsConfigDict(
        env_file=_PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── Core (always present) ──────────────────────────────────────────

    SECRET_KEY: str = Field(default=_DEFAULT_SECRET_KEY)
    FLASK_ENV: str = Field(default="development")
    DEBUG: bool = Field(default=True)
    CORS_ORIGINS: list[str] = Field(default=["http://localhost:3000"])
    TASK_MAX_WORKERS: int = Field(default=4)
    TASK_TIMEOUT_SECONDS: int = Field(default=300)
    TASK_CLEANUP_INTERVAL_SECONDS: int = Field(default=600)
    METRICS_UPDATE_INTERVAL: int = Field(default=60)
    GRACEFUL_SHUTDOWN_TIMEOUT: int = Field(default=600)
    DRAIN_AUTH_KEY: str = Field(default="")


    # ── use_database ───────────────────────────────────────────────────

    DATABASE_URL: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/test_app",
    )
    DB_POOL_SIZE: int = Field(default=20)
    DB_POOL_MAX_OVERFLOW: int = Field(default=30)
    DB_POOL_TIMEOUT: int = Field(default=10)
    DB_POOL_ECHO: bool | str = Field(default=False)
    DIAGNOSTICS_ENABLED: bool = Field(default=False)
    DIAGNOSTICS_SLOW_QUERY_THRESHOLD_MS: int = Field(default=100)
    DIAGNOSTICS_SLOW_REQUEST_THRESHOLD_MS: int = Field(default=500)
    DIAGNOSTICS_LOG_ALL_QUERIES: bool = Field(default=False)



    # ── use_oidc ───────────────────────────────────────────────────────

    BASEURL: str = Field(default="http://localhost:3000")
    OIDC_ENABLED: bool = Field(default=False)
    OIDC_ISSUER_URL: str | None = Field(default=None)
    OIDC_CLIENT_ID: str | None = Field(default=None)
    OIDC_CLIENT_SECRET: str | None = Field(default=None)
    OIDC_SCOPES: str = Field(default="openid profile email")
    OIDC_AUDIENCE: str | None = Field(default=None)
    OIDC_CLOCK_SKEW_SECONDS: int = Field(default=30)
    OIDC_COOKIE_NAME: str = Field(default="access_token")
    OIDC_COOKIE_SECURE: bool | None = Field(default=None)
    OIDC_COOKIE_SAMESITE: str = Field(default="Lax")
    OIDC_REFRESH_COOKIE_NAME: str = Field(default="refresh_token")



    # ── use_s3 ─────────────────────────────────────────────────────────

    S3_ENDPOINT_URL: str = Field(default="http://localhost:9000")
    S3_ACCESS_KEY_ID: str = Field(default="admin")
    S3_SECRET_ACCESS_KEY: str = Field(default="password")
    S3_BUCKET_NAME: str = Field(default="test-app-attachments")
    S3_REGION: str = Field(default="us-east-1")
    S3_USE_SSL: bool = Field(default=False)
    THUMBNAIL_STORAGE_PATH: str = Field(default="/tmp/test-app_thumbnails")



    # ── use_sse ────────────────────────────────────────────────────────

    FRONTEND_VERSION_URL: str = Field(default="http://localhost:3000/version.json")
    SSE_HEARTBEAT_INTERVAL: int = Field(default=5)
    SSE_GATEWAY_URL: str = Field(default="http://localhost:3001")
    SSE_CALLBACK_SECRET: str = Field(default="")



class Settings(BaseModel):
    """Application settings with lowercase fields and derived values."""

    model_config = ConfigDict(from_attributes=True)

    # ── Core (always present) ──────────────────────────────────────────

    secret_key: str = _DEFAULT_SECRET_KEY
    flask_env: str = "development"
    debug: bool = True
    cors_origins: list[str] = Field(default=["http://localhost:3000"])
    task_max_workers: int = 4
    task_timeout_seconds: int = 300
    task_cleanup_interval_seconds: int = 600
    metrics_update_interval: int = 60
    graceful_shutdown_timeout: int = 600
    drain_auth_key: str = ""


    # ── use_database ───────────────────────────────────────────────────

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/test_app"
    db_pool_size: int = 20
    db_pool_max_overflow: int = 30
    db_pool_timeout: int = 10
    db_pool_echo: bool | str = False
    diagnostics_enabled: bool = False
    diagnostics_slow_query_threshold_ms: int = 100
    diagnostics_slow_request_threshold_ms: int = 500
    diagnostics_log_all_queries: bool = False
    sqlalchemy_engine_options: dict[str, Any] = Field(default_factory=dict)



    # ── use_oidc ───────────────────────────────────────────────────────

    baseurl: str = "http://localhost:3000"
    oidc_enabled: bool = False
    oidc_issuer_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_scopes: str = "openid profile email"
    oidc_audience: str | None = None
    oidc_clock_skew_seconds: int = 30
    oidc_cookie_name: str = "access_token"
    oidc_cookie_secure: bool = False
    oidc_cookie_samesite: str = "Lax"
    oidc_refresh_cookie_name: str = "refresh_token"



    # ── use_s3 ─────────────────────────────────────────────────────────

    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key_id: str = "admin"
    s3_secret_access_key: str = "password"
    s3_bucket_name: str = "test-app-attachments"
    s3_region: str = "us-east-1"
    s3_use_ssl: bool = False
    thumbnail_storage_path: str = "/tmp/test-app_thumbnails"



    # ── use_sse ────────────────────────────────────────────────────────

    frontend_version_url: str = "http://localhost:3000/version.json"
    sse_heartbeat_interval: int = 5
    sse_gateway_url: str = "http://localhost:3001"
    sse_callback_secret: str = ""


    @property
    def is_testing(self) -> bool:
        return self.flask_env == "testing"

    @property
    def is_production(self) -> bool:
        return self.flask_env == "production"

    def to_flask_config(self) -> "FlaskConfig":
        return FlaskConfig(
            SECRET_KEY=self.secret_key,

            SQLALCHEMY_DATABASE_URI=self.database_url,
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SQLALCHEMY_ENGINE_OPTIONS=self.sqlalchemy_engine_options,

        )

    def validate_production_config(self) -> None:
        from app.exceptions import ConfigurationError

        errors: list[str] = []

        if self.is_production and self.secret_key == _DEFAULT_SECRET_KEY:
            errors.append(
                "SECRET_KEY must be set to a secure value in production"
            )


        if self.oidc_enabled:
            if not self.oidc_issuer_url:
                errors.append("OIDC_ISSUER_URL is required when OIDC_ENABLED=True")
            if not self.oidc_client_id:
                errors.append("OIDC_CLIENT_ID is required when OIDC_ENABLED=True")
            if not self.oidc_client_secret:
                errors.append("OIDC_CLIENT_SECRET is required when OIDC_ENABLED=True")


        if errors:
            raise ConfigurationError(
                "Configuration validation failed:\n  - " + "\n  - ".join(errors)
            )

    def set_engine_options_override(self, options: dict[str, Any]) -> None:
        """Override SQLAlchemy engine options (used for testing with SQLite)."""

        self.sqlalchemy_engine_options = options


    @classmethod
    def load(cls, env: "Environment | None" = None) -> "Settings":
        if env is None:
            env = Environment()


        sse_heartbeat_interval = (
            30 if env.FLASK_ENV == "production" else env.SSE_HEARTBEAT_INTERVAL
        )



        oidc_audience = env.OIDC_AUDIENCE or env.OIDC_CLIENT_ID
        if env.OIDC_COOKIE_SECURE is not None:
            oidc_cookie_secure = env.OIDC_COOKIE_SECURE
        else:
            oidc_cookie_secure = env.BASEURL.startswith("https://")



        sqlalchemy_engine_options = {
            "pool_size": env.DB_POOL_SIZE,
            "max_overflow": env.DB_POOL_MAX_OVERFLOW,
            "pool_timeout": env.DB_POOL_TIMEOUT,
            "pool_pre_ping": True,
            "echo_pool": env.DB_POOL_ECHO,
        }


        return cls(
            # Core
            secret_key=env.SECRET_KEY,
            flask_env=env.FLASK_ENV,
            debug=env.DEBUG,
            cors_origins=env.CORS_ORIGINS,
            task_max_workers=env.TASK_MAX_WORKERS,
            task_timeout_seconds=env.TASK_TIMEOUT_SECONDS,
            task_cleanup_interval_seconds=env.TASK_CLEANUP_INTERVAL_SECONDS,
            metrics_update_interval=env.METRICS_UPDATE_INTERVAL,
            graceful_shutdown_timeout=env.GRACEFUL_SHUTDOWN_TIMEOUT,
            drain_auth_key=env.DRAIN_AUTH_KEY,

            # Database
            database_url=env.DATABASE_URL,
            db_pool_size=env.DB_POOL_SIZE,
            db_pool_max_overflow=env.DB_POOL_MAX_OVERFLOW,
            db_pool_timeout=env.DB_POOL_TIMEOUT,
            db_pool_echo=env.DB_POOL_ECHO,
            diagnostics_enabled=env.DIAGNOSTICS_ENABLED,
            diagnostics_slow_query_threshold_ms=env.DIAGNOSTICS_SLOW_QUERY_THRESHOLD_MS,
            diagnostics_slow_request_threshold_ms=env.DIAGNOSTICS_SLOW_REQUEST_THRESHOLD_MS,
            diagnostics_log_all_queries=env.DIAGNOSTICS_LOG_ALL_QUERIES,
            sqlalchemy_engine_options=sqlalchemy_engine_options,


            # OIDC
            baseurl=env.BASEURL,
            oidc_enabled=env.OIDC_ENABLED,
            oidc_issuer_url=env.OIDC_ISSUER_URL,
            oidc_client_id=env.OIDC_CLIENT_ID,
            oidc_client_secret=env.OIDC_CLIENT_SECRET,
            oidc_scopes=env.OIDC_SCOPES,
            oidc_audience=oidc_audience,
            oidc_clock_skew_seconds=env.OIDC_CLOCK_SKEW_SECONDS,
            oidc_cookie_name=env.OIDC_COOKIE_NAME,
            oidc_cookie_secure=oidc_cookie_secure,
            oidc_cookie_samesite=env.OIDC_COOKIE_SAMESITE,
            oidc_refresh_cookie_name=env.OIDC_REFRESH_COOKIE_NAME,


            # S3
            s3_endpoint_url=env.S3_ENDPOINT_URL,
            s3_access_key_id=env.S3_ACCESS_KEY_ID,
            s3_secret_access_key=env.S3_SECRET_ACCESS_KEY,
            s3_bucket_name=env.S3_BUCKET_NAME,
            s3_region=env.S3_REGION,
            s3_use_ssl=env.S3_USE_SSL,
            thumbnail_storage_path=env.THUMBNAIL_STORAGE_PATH,


            # SSE
            frontend_version_url=env.FRONTEND_VERSION_URL,
            sse_heartbeat_interval=sse_heartbeat_interval,
            sse_gateway_url=env.SSE_GATEWAY_URL,
            sse_callback_secret=env.SSE_CALLBACK_SECRET,

        )


class FlaskConfig:
    """Flask-specific configuration for app.config.from_object()."""

    def __init__(
        self,
        SECRET_KEY: str,

        SQLALCHEMY_DATABASE_URI: str,
        SQLALCHEMY_TRACK_MODIFICATIONS: bool,
        SQLALCHEMY_ENGINE_OPTIONS: dict[str, Any],

    ) -> None:
        self.SECRET_KEY = SECRET_KEY

        self.SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI
        self.SQLALCHEMY_TRACK_MODIFICATIONS = SQLALCHEMY_TRACK_MODIFICATIONS
        self.SQLALCHEMY_ENGINE_OPTIONS = SQLALCHEMY_ENGINE_OPTIONS

