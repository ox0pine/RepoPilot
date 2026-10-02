from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from repopilot.api.security import require_auth
from repopilot.application.settings import InvalidSettingsError, SettingsService
from repopilot.domain.settings import ModelSettingsUpdate, PublicModelSettings
from repopilot.persistence.settings import SettingsStorageError
from repopilot.application.settings import GitHubSettingsService
from repopilot.domain.settings import GitHubAuthorizationResult, GitHubSettingsUpdate, PublicGitHubSettings, InvalidGitHubSettingsError
from repopilot.integration.github import GitHubAPIError
from repopilot.persistence.settings import GitHubEncryptionUnavailableError


def build_router(service: SettingsService, github_service: GitHubSettingsService) -> APIRouter:
    router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(require_auth)])

    @router.get("", response_model=PublicModelSettings)
    async def get_settings() -> PublicModelSettings:
        try:
            return await service.public()
        except SettingsStorageError:
            raise HTTPException(status_code=500, detail="Unable to load model settings") from None

    @router.put("/model", response_model=PublicModelSettings)
    async def update_model_settings(payload: ModelSettingsUpdate) -> PublicModelSettings:
        try:
            return await service.update(base_url=payload.base_url, api_key=payload.api_key, model=payload.model)
        except InvalidSettingsError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except SettingsStorageError:
            raise HTTPException(status_code=500, detail="Unable to save model settings") from None

    async def github_operation(operation):
        try:
            return await operation
        except InvalidGitHubSettingsError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        except GitHubEncryptionUnavailableError as error:
            raise HTTPException(status_code=503, detail=str(error)) from None
        except SettingsStorageError as error:
            raise HTTPException(status_code=500, detail=str(error)) from None
        except GitHubAPIError as error:
            raise HTTPException(status_code=error.status_code, detail=error.detail) from None

    @router.get('/github', response_model=PublicGitHubSettings)
    async def get_github_settings() -> PublicGitHubSettings:
        return await github_operation(github_service.public())

    @router.put('/github', response_model=PublicGitHubSettings)
    async def update_github_settings(payload: GitHubSettingsUpdate) -> PublicGitHubSettings:
        return await github_operation(github_service.update(payload))

    @router.post('/github/authorize', response_model=GitHubAuthorizationResult)
    async def authorize_github_key() -> GitHubAuthorizationResult:
        return await github_operation(github_service.authorize())


    return router
