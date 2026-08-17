from functools import lru_cache
from typing import Literal
from urllib.parse import quote_plus

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated environment-driven application settings."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "YouTube Shorts Automation API"
    app_env: str = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "shorts"
    postgres_user: str = "shorts"
    postgres_password: str = Field(default="change-me", repr=False)
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    celery_broker_url: str | None = None
    celery_result_backend: str | None = None
    storage_root: str = "storage/videos"
    max_upload_size_mb: int = 1024
    whisper_model: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    openai_api_key: str | None = Field(default=None, repr=False)
    openai_base_url: str = "https://api.openai.com/v1"
    pexels_api_key: str | None = Field(default=None, repr=False)

    publishing_provider: Literal["local", "youtube"] = "local"
    youtube_oauth_client_id: str | None = None
    youtube_oauth_client_secret: SecretStr | None = None
    youtube_oauth_redirect_uri: str | None = None
    oauth_state_ttl_seconds: int = Field(default=600, gt=0)
    credential_encryption_key: SecretStr | None = None

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{quote_plus(self.postgres_user)}:{quote_plus(self.postgres_password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def resolved_celery_broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def resolved_celery_result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
