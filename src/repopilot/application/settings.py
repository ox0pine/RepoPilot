from __future__ import annotations

from repopilot.domain.settings import ModelSettings, PublicModelSettings
from repopilot.integration.cache import ModelCache
from repopilot.integration.models import fetch_models
from repopilot.persistence.settings import SettingsRepository, StoredModelSettings
from repopilot.domain.settings import GitHubAuthorizationResult, GitHubSettingsUpdate, PublicGitHubSettings, InvalidGitHubSettingsError
from repopilot.integration.github import GitHubClient
from repopilot.persistence.settings import GitHubSettingsRepository, StoredGitHubSettings


class InvalidSettingsError(Exception):
    pass


class SettingsService:
    def __init__(self, repository: SettingsRepository, cache: ModelCache) -> None:
        self.repository = repository
        self.cache = cache

    @staticmethod
    def _public(settings: StoredModelSettings) -> PublicModelSettings:
        return PublicModelSettings(
            base_url=settings.base_url,
            model=settings.model,
            api_key_configured=bool(settings.api_key),
        )

    async def public(self) -> PublicModelSettings:
        return self._public(await self.repository.load())

    def _validate(self, base_url: str, model: str = "") -> ModelSettings:
        try:
            return ModelSettings(base_url=base_url, model=model)
        except ValueError:
            raise InvalidSettingsError("Invalid model settings: use an HTTP or HTTPS URL without credentials, query, or fragment") from None

    async def update(self, *, base_url: str, api_key: str | None, model: str) -> PublicModelSettings:
        validated = self._validate(base_url, model)
        saved = await self.repository.save(validated, api_key)
        return self._public(saved)

    async def refresh_models(self, *, base_url: str, api_key: str | None) -> list[str]:
        normalized_url = self._validate(base_url).base_url
        if api_key is None:
            settings = await self.repository.load()
            token = settings.api_key if normalized_url == settings.base_url else ""
        else:
            token = api_key.strip()
        cached = await self.cache.get(normalized_url, token)
        if cached is not None:
            return cached
        models = await fetch_models(normalized_url, token)
        await self.cache.set(normalized_url, token, models)
        return models


class GitHubSettingsService:
    def __init__(self, repository: GitHubSettingsRepository, client: GitHubClient) -> None:
        self.repository = repository
        self.client = client

    @staticmethod
    def _public(settings: StoredGitHubSettings) -> PublicGitHubSettings:
        return PublicGitHubSettings(
            public_key=settings.public_key,
            private_key_configured=bool(settings.private_key.get_secret_value()),
            api_token_configured=bool(settings.api_token.get_secret_value()),
        )

    async def public(self) -> PublicGitHubSettings:
        return self._public(await self.repository.load())

    async def update(self, payload: GitHubSettingsUpdate) -> PublicGitHubSettings:
        return self._public(await self.repository.save(payload))

    async def authorize(self) -> GitHubAuthorizationResult:
        settings = await self.repository.load()
        token = settings.api_token.get_secret_value()
        if not token:
            raise InvalidGitHubSettingsError('Save a GitHub API token in settings before authorizing the SSH key')
        if not settings.public_key or not settings.private_key.get_secret_value():
            raise InvalidGitHubSettingsError('Save GitHub settings to create an SSH key pair before authorizing it')
        registration = await self.client.register_public_key(
            public_key=settings.public_key, api_token=token,
        )
        return GitHubAuthorizationResult(settings=self._public(settings), registration=registration)
