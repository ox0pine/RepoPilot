from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from repopilot.api.security import require_auth
from repopilot.application.runs import RunService
from repopilot.domain.runs import CreateRunRequest, RunDetail, RunSummary
from repopilot.domain.tasks import TaskError


def build_router(service: RunService, default_image: str) -> APIRouter:
    router = APIRouter(tags=['runs'], dependencies=[Depends(require_auth)])

    async def operation(awaitable):
        try:
            return await awaitable
        except TaskError as error:
            raise HTTPException(status_code=error.status_code, detail=error.detail) from None

    @router.get('/execution')
    async def execution_settings() -> dict[str, str]:
        return {'default_image': default_image}

    @router.post('/tasks/{task_id}/runs', response_model=RunDetail, status_code=202)
    async def create_run(task_id: UUID, payload: CreateRunRequest) -> RunDetail:
        return await operation(service.create(task_id, payload))

    @router.get('/tasks/{task_id}/runs', response_model=list[RunSummary])
    async def list_runs(task_id: UUID) -> list[RunSummary]:
        return await operation(service.list(task_id))

    @router.get('/tasks/{task_id}/runs/{run_id}', response_model=RunDetail)
    async def run_detail(task_id: UUID, run_id: UUID) -> RunDetail:
        return await operation(service.detail(task_id, run_id))

    @router.post('/tasks/{task_id}/runs/{run_id}/cancel', response_model=RunDetail)
    async def cancel_run(task_id: UUID, run_id: UUID) -> RunDetail:
        return await operation(service.cancel(task_id, run_id))

    return router
