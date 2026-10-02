from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from repopilot.api.security import require_auth
from repopilot.application.tasks import TaskService
from repopilot.domain.tasks import (
    ApproveGoalRequest, CreateTask, GenerateGoalRequest, TaskDetail, TaskError,
    TaskList, TaskStats,
)


def build_router(service: TaskService) -> APIRouter:
    router = APIRouter(prefix='/tasks', tags=['tasks'], dependencies=[Depends(require_auth)])

    async def operation(awaitable):
        try:
            return await awaitable
        except TaskError as error:
            raise HTTPException(status_code=error.status_code, detail=error.detail) from None

    @router.post('', response_model=TaskDetail, status_code=201)
    async def create_task(payload: CreateTask) -> TaskDetail:
        return await operation(service.create(payload))

    @router.get('', response_model=TaskList)
    async def list_tasks(limit: int = Query(10, ge=1, le=50), cursor: str | None = None) -> TaskList:
        return await operation(service.list(limit, cursor))

    @router.get('/stats', response_model=TaskStats)
    async def task_stats() -> TaskStats:
        return await operation(service.stats())

    @router.get('/{task_id}', response_model=TaskDetail)
    async def task_detail(task_id: UUID) -> TaskDetail:
        return await operation(service.detail(task_id))

    @router.post('/{task_id}/goal', response_model=TaskDetail)
    async def generate_goal(task_id: UUID, payload: GenerateGoalRequest) -> TaskDetail:
        return await operation(service.generate(task_id, payload))

    @router.post('/{task_id}/approve', response_model=TaskDetail)
    async def approve_goal(task_id: UUID, payload: ApproveGoalRequest) -> TaskDetail:
        return await operation(service.approve(task_id, payload))

    return router
