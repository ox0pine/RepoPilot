from __future__ import annotations

from urllib.parse import urlsplit
from typing import Literal

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


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


class PublicGitHubSettings(BaseModel):
    public_key: str
    private_key_configured: bool
    api_token_configured: bool


class GitHubSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    api_token: SecretStr | None = Field(default=None, max_length=4096)


class GitHubPublicKeyResult(BaseModel):
    status: Literal['created', 'already_exists']
    key_id: int
    public_key: str


class GitHubAuthorizationResult(BaseModel):
    settings: PublicGitHubSettings
    registration: GitHubPublicKeyResult


class InvalidGitHubSettingsError(ValueError):
    pass


def normalize_ssh_public_key(value: str) -> str:
    value = value.strip()
    if not value:
        return ''
    try:
        if len(value.splitlines()) != 1:
            raise ValueError
        key = serialization.load_ssh_public_key(value.encode('utf-8'))
        if not isinstance(key, ed25519.Ed25519PublicKey):
            raise ValueError
        return key.public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise InvalidGitHubSettingsError('Invalid SSH public key') from None


def _load_ssh_private_key(private_key: str):
    try:
        key = serialization.load_ssh_private_key(private_key.encode('utf-8'), password=None)
        if not isinstance(key, ed25519.Ed25519PrivateKey):
            raise ValueError
    except TypeError:
        raise InvalidGitHubSettingsError('Passphrase-protected SSH private keys are not supported') from None
    except (ValueError, UnsupportedAlgorithm):
        raise InvalidGitHubSettingsError('Invalid or unsupported SSH private key') from None
    return key


def validate_ssh_pair(public_key: str, private_key: str) -> tuple[str, str]:
    public_key = normalize_ssh_public_key(public_key)
    private_key = private_key.replace('\r\n', '\n').strip()
    if not public_key and not private_key:
        return '', ''
    if not private_key:
        raise InvalidGitHubSettingsError('SSH private key is required when a public key is configured')
    private_key += '\n'
    key = _load_ssh_private_key(private_key)
    if not public_key:
        raise InvalidGitHubSettingsError('SSH public key is required when a private key is configured')
    derived = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
    if derived != public_key:
        raise InvalidGitHubSettingsError('SSH public and private keys do not match')
    return public_key, private_key


def ensure_ssh_pair(public_key: str, private_key: str) -> tuple[str, str]:
    if not public_key and not private_key:
        key = ed25519.Ed25519PrivateKey.generate()
        return (
            key.public_key().public_bytes(
                serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH,
            ).decode(),
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                serialization.NoEncryption(),
            ).decode(),
        )
    validate_ssh_pair(public_key, private_key)
    return public_key, private_key


def normalize_github_api_token(value: str) -> str:
    value = value.strip()
    if any(not ('!' <= char <= '~') for char in value):
        raise InvalidGitHubSettingsError('Invalid GitHub API token')
    return value
