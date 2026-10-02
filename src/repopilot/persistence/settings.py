from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from repopilot.domain.settings import (
    GitHubSettingsUpdate, InvalidGitHubSettingsError, ModelSettings, ensure_ssh_pair,
    normalize_github_api_token,
)
from repopilot.persistence.database import Database, GitHubSettingsRow, ModelSettingsRow


class StoredModelSettings(ModelSettings):
    api_key: str = Field(default="", repr=False)


class SettingsStorageError(Exception):
    pass


class SettingsRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def load(self) -> StoredModelSettings:
        try:
            async with self.database.session() as session:
                row = await session.scalar(select(ModelSettingsRow).where(ModelSettingsRow.id == 1))
        except (SQLAlchemyError, OSError):
            raise SettingsStorageError("Unable to read saved model settings") from None
        if row is None:
            raise SettingsStorageError("Saved model settings are unavailable")
        try:
            return StoredModelSettings(base_url=row.base_url, model=row.model, api_key=row.api_key)
        except ValueError:
            raise SettingsStorageError("Saved model settings are invalid") from None

    async def save(self, settings: ModelSettings, api_key: str | None) -> StoredModelSettings:
        try:
            async with self.database.session() as session:
                async with session.begin():
                    row = await session.scalar(
                        select(ModelSettingsRow).where(ModelSettingsRow.id == 1).with_for_update()
                    )
                    if row is None:
                        raise SettingsStorageError("Saved model settings are unavailable")
                    if api_key is not None:
                        token = api_key.strip()
                    else:
                        token = row.api_key if settings.base_url == row.base_url else ""
                    row.base_url = settings.base_url
                    row.model = settings.model
                    row.api_key = token
                    saved = StoredModelSettings(**settings.model_dump(), api_key=token)
                return saved
        except (SQLAlchemyError, OSError):
            raise SettingsStorageError("Unable to save model settings") from None


class StoredGitHubSettings(BaseModel):
    public_key: str
    private_key: SecretStr
    api_token: SecretStr


class GitHubEncryptionUnavailableError(Exception):
    def __init__(self) -> None:
        super().__init__('Configure a valid GITHUB_CREDENTIALS_KEY to use GitHub settings')


class GitHubSettingsRepository:
    def __init__(self, database: Database, encryption_key: SecretStr | None) -> None:
        self.database = database
        self.encryption_key = encryption_key
        self._fernet: Fernet | None = None

    def _cipher(self) -> Fernet:
        if self._fernet is None:
            try:
                key = self.encryption_key.get_secret_value() if self.encryption_key else ''
                if not key.strip():
                    raise ValueError
                self._fernet = Fernet(key.encode('ascii'))
            except (ValueError, TypeError):
                raise GitHubEncryptionUnavailableError() from None
        return self._fernet

    def _stored(self, row: GitHubSettingsRow | None, cipher: Fernet) -> StoredGitHubSettings:
        if row is None:
            raise SettingsStorageError('Saved GitHub settings are unavailable')
        try:
            private = cipher.decrypt(row.private_key_ciphertext.encode()).decode() if row.private_key_ciphertext else ''
            token = cipher.decrypt(row.api_token_ciphertext.encode()).decode() if row.api_token_ciphertext else ''
        except (InvalidToken, ValueError, TypeError):
            raise SettingsStorageError('Unable to decrypt saved GitHub credentials; check GITHUB_CREDENTIALS_KEY') from None
        return StoredGitHubSettings(public_key=row.public_key, private_key=SecretStr(private), api_token=SecretStr(token))

    async def load(self) -> StoredGitHubSettings:
        cipher = self._cipher()
        try:
            async with self.database.session() as session:
                row = await session.scalar(select(GitHubSettingsRow).where(GitHubSettingsRow.id == 1))
                return self._stored(row, cipher)
        except (SQLAlchemyError, OSError):
            raise SettingsStorageError('Unable to load GitHub settings') from None

    async def save(self, payload: GitHubSettingsUpdate) -> StoredGitHubSettings:
        cipher = self._cipher()
        try:
            async with self.database.session() as session:
                async with session.begin():
                    row = await session.scalar(select(GitHubSettingsRow).where(GitHubSettingsRow.id == 1).with_for_update())
                    current = self._stored(row, cipher)
                    old_private = current.private_key.get_secret_value()
                    old_token = current.api_token.get_secret_value()
                    token = old_token if payload.api_token is None else payload.api_token.get_secret_value()
                    token = normalize_github_api_token(token)
                    if not token:
                        raise InvalidGitHubSettingsError('GitHub API token is required')
                    public, private = ensure_ssh_pair(current.public_key, old_private)
                    assert row is not None
                    row.public_key = public
                    try:
                        if private != old_private:
                            row.private_key_ciphertext = cipher.encrypt(private.encode()).decode() if private else ''
                        if token != old_token:
                            row.api_token_ciphertext = cipher.encrypt(token.encode()).decode() if token else ''
                    except (ValueError, TypeError):
                        raise SettingsStorageError('Unable to save GitHub settings') from None
                    saved = StoredGitHubSettings(public_key=public, private_key=SecretStr(private), api_token=SecretStr(token))
                return saved
        except (SQLAlchemyError, OSError):
            raise SettingsStorageError('Unable to save GitHub settings') from None
