from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import AsyncIterator
from uuid import UUID, uuid4

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from repopilot.domain.tasks import (
    GenerationAction,
    GoalContent,
    TaskGoal,
    SourceSnapshot,
    TaskDetail,
    TaskError,
    TaskList,
    TaskMessage,
    TaskStats,
    TaskSummary,
    decode_cursor,
    encode_cursor,
)
from repopilot.persistence.database import Database, TaskGoalRow, TaskMessageRow, TaskRow


INTERRUPTED_ERROR = "目标生成已中断，请重试"


@dataclass(frozen=True)
class GenerationAttempt:
    task_id: UUID
    generation_id: UUID
    revision: int
    feedback: str | None
    source_snapshot: SourceSnapshot
    previous_goal: GoalContent | None


class TaskRepository:
    """Short database-only transactions; generation happens after start returns."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncSession]:
        # A semantic rejection can follow lease recovery under the row lock.
        # Commit that recovery rather than restoring a permanently generating row.
        rejection: TaskError | None = None
        try:
            async with self.database.session() as session:
                async with session.begin():
                    try:
                        yield session
                    except TaskError as error:
                        rejection = error
        except SQLAlchemyError:
            raise TaskError(500, "对话数据暂时无法读写，请稍后重试") from None
        if rejection is not None:
            raise rejection

    @staticmethod
    def _summary(row: TaskRow) -> TaskSummary:
        return TaskSummary(
            id=row.id,
            title=row.title,
            repository_url=row.repository_url,
            baseline_commit=row.baseline_commit,
            issue_url=row.issue_url,
            status=row.status,
            revision=row.revision,
            current_goal_version=row.current_goal_version,
            approved_goal_version=row.approved_goal_version,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def _detail(self, session: AsyncSession, row: TaskRow) -> TaskDetail:
        goals = (await session.scalars(
            select(TaskGoalRow).where(TaskGoalRow.task_id == row.id).order_by(TaskGoalRow.version)
        )).all()
        messages = (await session.scalars(
            select(TaskMessageRow).where(TaskMessageRow.task_id == row.id).order_by(TaskMessageRow.id)
        )).all()
        return TaskDetail(
            **self._summary(row).model_dump(),
            source_snapshot=SourceSnapshot.model_validate(row.source_snapshot),
            goals=[TaskGoal(version=goal.version, content=goal.content, model=goal.model,
                            created_at=goal.created_at) for goal in goals],
            messages=[TaskMessage(id=message.id, kind=message.kind, text=message.text,
                                  goal_version=message.goal_version, created_at=message.created_at)
                      for message in messages],
            approved_at=row.approved_at,
            last_error=row.last_error,
            generation_deadline=row.generation_deadline,
        )

    @staticmethod
    def _fail_expired(row: TaskRow, now: datetime) -> None:
        row.status = "generation_failed"
        row.last_error = INTERRUPTED_ERROR
        row.generation_id = None
        row.generation_deadline = None
        row.revision += 1
        row.updated_at = now

    async def _locked(self, session: AsyncSession, task_id: UUID) -> tuple[TaskRow, datetime]:
        row = await session.scalar(select(TaskRow).where(TaskRow.id == task_id).with_for_update())
        if row is None:
            raise TaskError(404, "对话不存在")
        # clock_timestamp(), unlike now(), advances while waiting for a row lock.
        now = await session.scalar(select(func.clock_timestamp()))
        if row.status == "generating" and row.generation_deadline is not None and row.generation_deadline <= now:
            self._fail_expired(row, now)
        return row, now

    @staticmethod
    def _expect_revision(row: TaskRow, expected_revision: int) -> None:
        if row.revision != expected_revision:
            raise TaskError(409, "目标已更新，请重新确认")

    async def recover_expired(self, task_id: UUID | None = None) -> int:
        """Conditionally retire expired attempts using the database clock."""
        async with self._transaction() as session:
            statement = update(TaskRow).where(
                TaskRow.status == "generating",
                TaskRow.generation_deadline <= func.clock_timestamp(),
            )
            if task_id is not None:
                statement = statement.where(TaskRow.id == task_id)
            result = await session.execute(statement.values(
                status="generation_failed",
                last_error=INTERRUPTED_ERROR,
                generation_id=None,
                generation_deadline=None,
                revision=TaskRow.revision + 1,
                updated_at=func.clock_timestamp(),
            ))
            return result.rowcount

    async def create(self, source_snapshot: SourceSnapshot) -> TaskDetail:
        """Persist a verified immutable source and its initial source message."""
        async with self._transaction() as session:
            now = await session.scalar(select(func.clock_timestamp()))
            row = TaskRow(
                id=uuid4(), title=source_snapshot.issue_title,
                repository_url=source_snapshot.repository_url,
                baseline_commit=source_snapshot.baseline_commit,
                issue_url=source_snapshot.issue_url,
                source_snapshot=source_snapshot.model_dump(mode="json"),
                status="draft", revision=1, current_goal_version=0,
                created_at=now, updated_at=now,
            )
            session.add(row)
            await session.flush()
            session.add(TaskMessageRow(
                task_id=row.id, kind="source",
                text=f"{source_snapshot.issue_title}\n{source_snapshot.issue_url}\n基准 commit：{source_snapshot.baseline_commit}",
                created_at=now,
            ))
            await session.flush()
            return await self._detail(session, row)

    async def detail(self, task_id: UUID) -> TaskDetail:
        await self.recover_expired(task_id)
        async with self._transaction() as session:
            # Lock briefly for a coherent revision, goals and message snapshot.
            row, _ = await self._locked(session, task_id)
            return await self._detail(session, row)

    async def list(self, limit: int = 10, cursor: str | None = None) -> TaskList:
        if not 1 <= limit <= 50:
            raise TaskError(422, "每页对话数量必须为 1 到 50")
        boundary = decode_cursor(cursor) if cursor is not None else None
        await self.recover_expired()
        async with self._transaction() as session:
            statement = select(TaskRow).order_by(TaskRow.updated_at.desc(), TaskRow.id.desc()).limit(limit + 1)
            if boundary is not None:
                statement = statement.where(tuple_(TaskRow.updated_at, TaskRow.id) < boundary)
            rows = (await session.scalars(statement)).all()
            page = rows[:limit]
            next_cursor = encode_cursor(page[-1].updated_at, page[-1].id) if len(rows) > limit else None
            return TaskList(items=[self._summary(row) for row in page], next_cursor=next_cursor)

    async def stats(self) -> TaskStats:
        await self.recover_expired()
        async with self._transaction() as session:
            counts = dict((await session.execute(
                select(TaskRow.status, func.count()).group_by(TaskRow.status)
            )).all())
            return TaskStats(total=sum(counts.values()), **counts)

    async def start_generation(
        self, task_id: UUID, expected_revision: int, action: GenerationAction,
        feedback: str | None = None,
    ) -> GenerationAttempt:
        feedback = feedback.strip() if feedback is not None else None
        if feedback is not None and len(feedback) > 8000:
            raise TaskError(422, "修改意见不能超过 8000 个字符")
        await self.recover_expired(task_id)
        async with self._transaction() as session:
            row, now = await self._locked(session, task_id)
            self._expect_revision(row, expected_revision)
            if action == "generate":
                if row.status != "draft":
                    raise TaskError(409, "当前对话不能首次生成目标")
            elif action == "revise":
                if row.status not in {"awaiting_approval", "approved", "generation_failed"} or row.current_goal_version < 1:
                    raise TaskError(409, "当前对话不能修改目标")
                if not feedback:
                    raise TaskError(422, "请填写修改意见")
            elif action == "retry":
                if row.status != "generation_failed":
                    raise TaskError(409, "当前对话不能重试生成")
                feedback = row.last_feedback
            else:
                raise TaskError(422, "无效的目标生成操作")
            previous = None
            if row.current_goal_version:
                previous_row = await session.get(TaskGoalRow, (row.id, row.current_goal_version))
                previous = GoalContent.model_validate(previous_row.content)
            generation_id = uuid4()
            row.status = "generating"
            row.generation_id = generation_id
            row.generation_deadline = now + timedelta(seconds=90)
            row.last_feedback = feedback
            row.last_error = None
            row.approved_goal_version = None
            row.approved_at = None
            row.revision += 1
            row.updated_at = now
            if action != "retry" and feedback:
                session.add(TaskMessageRow(task_id=row.id, kind="feedback", text=feedback, created_at=now))
            return GenerationAttempt(
                task_id=row.id, generation_id=generation_id, revision=row.revision,
                feedback=feedback, source_snapshot=SourceSnapshot.model_validate(row.source_snapshot),
                previous_goal=previous,
            )

    async def complete_generation(
        self, task_id: UUID, generation_id: UUID, content: GoalContent, model: str,
    ) -> TaskDetail | None:
        """Return None for expired or superseded results; never overwrite them."""
        await self.recover_expired(task_id)
        async with self._transaction() as session:
            row, now = await self._locked(session, task_id)
            if row.status != "generating" or row.generation_id != generation_id:
                return None
            version = row.current_goal_version + 1
            session.add(TaskGoalRow(task_id=row.id, version=version,
                                    content=content.model_dump(mode="json"), model=model, created_at=now))
            session.add(TaskMessageRow(task_id=row.id, kind="goal", text=f"目标版本 {version}",
                                       goal_version=version, created_at=now))
            row.current_goal_version = version
            row.status = "awaiting_approval"
            row.generation_id = None
            row.generation_deadline = None
            row.last_error = None
            row.revision += 1
            row.updated_at = now
            await session.flush()
            return await self._detail(session, row)

    async def fail_generation(self, task_id: UUID, generation_id: UUID, detail: str) -> TaskDetail | None:
        """Persist only a service-sanitized error for the still-current attempt."""
        await self.recover_expired(task_id)
        async with self._transaction() as session:
            row, now = await self._locked(session, task_id)
            if row.status != "generating" or row.generation_id != generation_id:
                return None
            row.status = "generation_failed"
            row.last_error = detail
            row.generation_id = None
            row.generation_deadline = None
            row.revision += 1
            row.updated_at = now
            await session.flush()
            return await self._detail(session, row)

    async def approve(self, task_id: UUID, expected_revision: int, goal_version: int) -> TaskDetail:
        await self.recover_expired(task_id)
        async with self._transaction() as session:
            row, now = await self._locked(session, task_id)
            # Repeating the same approval is idempotent even with its original revision.
            if row.status == "approved" and row.approved_goal_version == goal_version == row.current_goal_version:
                return await self._detail(session, row)
            self._expect_revision(row, expected_revision)
            if row.status != "awaiting_approval" or goal_version != row.current_goal_version or goal_version < 1:
                raise TaskError(409, "只能批准最新的待批准目标")
            row.status = "approved"
            row.approved_goal_version = goal_version
            row.approved_at = now
            row.revision += 1
            row.updated_at = now
            session.add(TaskMessageRow(task_id=row.id, kind="approval", text="目标已批准，代码执行尚未接入",
                                       goal_version=goal_version, created_at=now))
            await session.flush()
            return await self._detail(session, row)
