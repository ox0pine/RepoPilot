from __future__ import annotations

from uuid import UUID

from repopilot.domain.runs import CreateRunRequest, RunDetail, RunSummary
from repopilot.persistence.runs import RunRepository


class RunService:
    def __init__(self, repository: RunRepository) -> None:
        self.repository = repository

    async def create(self, task_id: UUID, payload: CreateRunRequest) -> RunDetail:
        return await self.repository.create(task_id, payload)

    async def list(self, task_id: UUID) -> list[RunSummary]:
        return await self.repository.list(task_id)

    async def detail(self, task_id: UUID, run_id: UUID) -> RunDetail:
        return await self.repository.detail(task_id, run_id)

    async def cancel(self, task_id: UUID, run_id: UUID) -> RunDetail:
        return await self.repository.cancel(task_id, run_id)
