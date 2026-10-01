from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from repopilot.api.security import require_auth
from repopilot.application.settings import InvalidSettingsError, SettingsService
from repopilot.domain.settings import ModelSettingsUpdate, PublicModelSettings
from repopilot.persistence.settings import SettingsStorageError


def build_router(service: SettingsService) -> APIRouter:
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

    return router
