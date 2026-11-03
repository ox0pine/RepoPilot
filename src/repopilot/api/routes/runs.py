from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from repopilot.api.errors import domain_call
from repopilot.api.security import require_auth
from repopilot.domain.runs import CreateDeliveryRequest, CreateRunRequest, RunDelivery, RunDetail, RunSummary
from repopilot.integration.delivery import DeliveryService
from repopilot.persistence.runs import RunRepository


def build_router(repository: RunRepository, delivery_service: DeliveryService, default_image: str) -> APIRouter:
    router = APIRouter(tags=['runs'], dependencies=[Depends(require_auth)])

    @router.get('/execution')
    async def execution_settings() -> dict[str, str]:
        return {'default_image': default_image}

    @router.post('/tasks/{task_id}/runs', response_model=RunDetail, status_code=202)
    async def create_run(task_id: UUID, payload: CreateRunRequest) -> RunDetail:
        return await domain_call(repository.create(task_id, payload))

    @router.get('/tasks/{task_id}/runs', response_model=list[RunSummary])
    async def list_runs(task_id: UUID) -> list[RunSummary]:
        return await domain_call(repository.list(task_id))

    @router.get('/tasks/{task_id}/runs/{run_id}', response_model=RunDetail)
    async def run_detail(task_id: UUID, run_id: UUID) -> RunDetail:
        return await domain_call(repository.detail(task_id, run_id))

    @router.post('/tasks/{task_id}/runs/{run_id}/cancel', response_model=RunDetail)
    async def cancel_run(task_id: UUID, run_id: UUID) -> RunDetail:
        return await domain_call(repository.cancel(task_id, run_id))

    @router.post('/tasks/{task_id}/runs/{run_id}/delivery', response_model=RunDelivery, status_code=201)
    async def create_delivery(
        task_id: UUID, run_id: UUID, payload: CreateDeliveryRequest,
    ) -> RunDelivery:
        return await domain_call(delivery_service.create(task_id, run_id))

    return router
