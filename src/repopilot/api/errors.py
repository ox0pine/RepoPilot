"""Transport conversion for domain errors; routers never restate status mapping."""

from __future__ import annotations

from fastapi import HTTPException

from repopilot.domain.tasks import TaskError


async def domain_call(awaitable):
    try:
        return await awaitable
    except TaskError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from None
