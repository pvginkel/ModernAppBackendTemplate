"""Application configuration extending common settings."""

from functools import lru_cache

from pydantic import Field

from common.core.settings import CommonSettings


class Settings(CommonSettings):
    """Application-specific settings.

    Add your own configuration fields here. They will be loaded from
    environment variables or .env file.

    Example:
        MY_API_KEY: str = Field(default="", description="API key for external service")
    """

    # Add your application-specific settings here
    pass


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
