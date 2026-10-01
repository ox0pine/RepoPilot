from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


def normalize_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base_url must be an HTTP or HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("base_url must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("base_url must not contain a query or fragment")
    return normalized


class ModelSettings(BaseModel):
    base_url: str = "https://api.openai.com/v1"
    model: str = ""

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return normalize_base_url(value)

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        return value.strip()


class PublicModelSettings(BaseModel):
    base_url: str
    model: str
    api_key_configured: bool


class ModelSettingsUpdate(BaseModel):
    base_url: str = Field(min_length=1)
    api_key: str | None = None
    model: str = ""


class ModelRefreshRequest(BaseModel):
    base_url: str = Field(min_length=1)
    api_key: str | None = None
