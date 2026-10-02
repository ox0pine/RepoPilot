from __future__ import annotations

import asyncio
from uuid import UUID

from repopilot.domain.tasks import (
    ApproveGoalRequest, CreateTask, GenerateGoalRequest, TaskDetail, TaskError,
    TaskList, TaskStats,
)
from repopilot.integration.goals import GoalClient
from repopilot.integration.task_sources import GitHubSourceClient
from repopilot.persistence.settings import (
    GitHubEncryptionUnavailableError, GitHubSettingsRepository, SettingsRepository,
    SettingsStorageError,
)
from repopilot.persistence.tasks import GenerationAttempt, INTERRUPTED_ERROR, TaskRepository


class TaskService:
    def __init__(
        self, repository: TaskRepository, settings_repository: SettingsRepository,
        github_settings_repository: GitHubSettingsRepository,
        source_client: GitHubSourceClient, goal_client: GoalClient,
    ) -> None:
        self.repository = repository
        self.settings_repository = settings_repository
        self.github_settings_repository = github_settings_repository
        self.source_client = source_client
        self.goal_client = goal_client

    async def create(self, payload: CreateTask) -> TaskDetail:
        try:
            settings = await self.github_settings_repository.load()
        except GitHubEncryptionUnavailableError:
            raise TaskError(503, "GitHub 凭据无法解密，请先在设置中配置 GitHub") from None
        except SettingsStorageError:
            raise TaskError(503, "GitHub 配置暂时不可用，请检查设置后重试") from None
        api_token = settings.api_token.get_secret_value()
        if not api_token:
            raise TaskError(503, "请先在设置中配置 GitHub API Token")
        source = await self.source_client.fetch(payload, api_token)
        return await self.repository.create(source)

    async def detail(self, task_id: UUID) -> TaskDetail:
        return await self.repository.detail(task_id)

    async def list(self, limit: int = 10, cursor: str | None = None) -> TaskList:
        return await self.repository.list(limit, cursor)

    async def stats(self) -> TaskStats:
        return await self.repository.stats()

    async def _cancel_attempt(self, attempt: GenerationAttempt) -> None:
        # Shield the short cleanup transaction from the cancelled HTTP request.
        # If persistence is unavailable, the database lease remains the fallback.
        cleanup = asyncio.create_task(self.repository.fail_generation(
            attempt.task_id, attempt.generation_id, INTERRUPTED_ERROR,
        ))
        try:
            await asyncio.shield(cleanup)
        except (Exception, asyncio.CancelledError):
            # Retrieve a detached cleanup exception without logging credentials.
            cleanup.add_done_callback(lambda result: result.exception() if not result.cancelled() else None)

    async def generate(self, task_id: UUID, payload: GenerateGoalRequest) -> TaskDetail:
        # Resolve the short commit even when cancellation arrives during startup,
        # so a committed generating row can still be retired immediately.
        start = asyncio.create_task(self.repository.start_generation(
            task_id, payload.expected_revision, payload.action, payload.feedback,
        ))
        try:
            attempt = await asyncio.shield(start)
        except asyncio.CancelledError:
            try:
                attempt = await asyncio.shield(start)
            except (Exception, asyncio.CancelledError):
                start.add_done_callback(lambda result: result.exception() if not result.cancelled() else None)
            else:
                await self._cancel_attempt(attempt)
            raise
        try:
            try:
                try:
                    settings = (await self.settings_repository.load()).model_copy(deep=True)
                except SettingsStorageError:
                    raise TaskError(503, "模型配置暂时不可用，请检查设置后重试") from None
                if not settings.model.strip():
                    raise TaskError(503, "请先在设置中配置并选择模型")
                content = await self.goal_client.generate(
                    settings, attempt.source_snapshot, attempt.previous_goal, attempt.feedback,
                )
                result = await self.repository.complete_generation(
                    task_id, attempt.generation_id, content, settings.model,
                )
            except TaskError as error:
                result = await self.repository.fail_generation(task_id, attempt.generation_id, error.detail)
                if result is None:
                    raise TaskError(409, "目标已更新，请重新确认") from None
                raise
            except Exception:
                # Never persist a raw provider/driver exception or return its text.
                error = TaskError(502, "目标生成失败，请稍后重试")
                result = await self.repository.fail_generation(task_id, attempt.generation_id, error.detail)
                if result is None:
                    raise TaskError(409, "目标已更新，请重新确认") from None
                raise error from None
            if result is None:
                raise TaskError(409, "目标已更新，请重新确认")
            return result
        except asyncio.CancelledError:
            await self._cancel_attempt(attempt)
            raise

    async def approve(self, task_id: UUID, payload: ApproveGoalRequest) -> TaskDetail:
        return await self.repository.approve(task_id, payload.expected_revision, payload.goal_version)
