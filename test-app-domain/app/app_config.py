"""Application-specific configuration for the test app."""

import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import dump_config_for_logging

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# CAS thumbnails are a local cache: every file can be regenerated from the S3
# original, so the default lives in the OS temp dir and needs no volume.
_DEFAULT_THUMBNAIL_STORAGE_PATH = Path(tempfile.gettempdir()) / "test-app-thumbnails"


class AppEnvironment(BaseSettings):
    """Raw environment variable loading for app-specific settings."""

    model_config = SettingsConfigDict(
        env_file=_PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Local cache directory for CAS thumbnails (CasImageService)
    THUMBNAIL_STORAGE_PATH: Path = _DEFAULT_THUMBNAIL_STORAGE_PATH


class AppSettings(BaseModel):
    """Application-specific settings."""

    model_config = ConfigDict(from_attributes=True)

    # Local cache directory for CAS thumbnails (CasImageService)
    thumbnail_storage_path: Path = _DEFAULT_THUMBNAIL_STORAGE_PATH

    def dump_for_logging(self) -> dict[str, str]:
        """Every setting, for the boot log, with the Sensitive ones as ``***``."""
        return dump_config_for_logging(self)

    @classmethod
    def load(cls, env: "AppEnvironment | None" = None, flask_env: str = "development") -> "AppSettings":
        """Load app settings from environment variables."""
        if env is None:
            env = AppEnvironment()
        return cls(thumbnail_storage_path=env.THUMBNAIL_STORAGE_PATH)
