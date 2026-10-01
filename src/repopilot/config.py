from __future__ import annotations

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    api_token: SecretStr
    database_url: str = Field(
        default="postgresql+asyncpg://repopilot:repopilot@127.0.0.1:5432/repopilot", repr=False
    )
    redis_url: str = Field(default="redis://127.0.0.1:6379/0", repr=False)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("api_token")
    @classmethod
    def require_token(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("API_TOKEN must not be empty")
        return value


def get_settings() -> AppSettings:
    return AppSettings()
