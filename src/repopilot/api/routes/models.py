from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from repopilot.api.security import require_auth
from repopilot.application.settings import InvalidSettingsError, SettingsService
from repopilot.domain.settings import ModelRefreshRequest
from repopilot.integration.models import ModelProviderError
from repopilot.persistence.settings import SettingsStorageError


def build_router(service: SettingsService) -> APIRouter:
    router = APIRouter(prefix="/models", tags=["models"], dependencies=[Depends(require_auth)])

    @router.post("/refresh")
    async def refresh_models(payload: ModelRefreshRequest) -> dict[str, list[str]]:
        try:
            models = await service.refresh_models(base_url=payload.base_url, api_key=payload.api_key)
        except InvalidSettingsError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except ModelProviderError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        except SettingsStorageError:
            raise HTTPException(status_code=500, detail="Unable to load model settings") from None
        return {"models": models}

    return router
