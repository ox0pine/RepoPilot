from __future__ import annotations

from fastapi import APIRouter, Depends

from repopilot.api.security import require_auth


def build_router() -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(require_auth)])

    @router.post("")
    async def authenticate() -> dict[str, bool]:
        return {"authenticated": True}

    return router
