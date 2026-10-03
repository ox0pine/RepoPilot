from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from repopilot.domain.runs import (
    EVENT_KINDS,
    TERMINAL_STATUSES,
    ClaimedRun,
    CreateRunRequest,
    RunCheck,
    RunDetail,
    RunEvent,
    RunSummary,
)
from repopilot.domain.settings import normalize_base_url
from repopilot.domain.tasks import GoalContent, SourceSnapshot, TaskError
from repopilot.persistence.database import Base, Database, TaskGoalRow, TaskRow

MAX_EVENTS = 256
MAX_EVENT_BYTES = 32 * 1024
MAX_REPORT_BYTES = 64 * 1024
MAX_PATCH_BYTES = 8 * 1024 * 1024
MAX_CHECKS_BYTES = 1024 * 1024
LIMIT_ERROR = '成果超过保存上限，未生成完整Patch'


class RunRow(Base):
    __tablename__ = 'runs'
    __table_args__ = (
        ForeignKeyConstraint(['task_id', 'goal_version'], ['task_goals.task_id', 'task_goals.version']),
        CheckConstraint("status IN ('queued','running','completed','failed','blocked','exhausted','cancelled','interrupted')", name='run_status'),
        CheckConstraint('goal_version >= 1', name='run_goal_version'),
        Index('runs_active_task', 'task_id', unique=True, postgresql_where=text("status IN ('queued','running')")),
        Index('runs_recent', 'task_id', 'created_at', 'id'),
        Index('runs_queue', 'created_at', 'id', postgresql_where=text("status = 'queued'")),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    task_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    goal_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    image: Mapped[str] = mapped_column(Text, nullable=False)
    image_id: Mapped[str | None] = mapped_column(Text)
    setup_command: Mapped[str] = mapped_column(Text, nullable=False)
    check_command: Mapped[str] = mapped_column(Text, nullable=False)
    source_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    goal_content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    model_endpoint: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(Text)
    worker_token: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    report: Mapped[str] = mapped_column(Text, nullable=False, default='')
    patch: Mapped[str] = mapped_column(Text, nullable=False, default='')
    checks: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)


class RunEventRow(Base):
    __tablename__ = 'run_events'
    __table_args__ = (
        CheckConstraint("kind IN ('context','assistant','tool_start','tool_result','check','artifact','state')", name='run_event_kind'),
        Index('run_events_order', 'run_id', 'id'),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey('runs.id'), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _redact(value, secrets: tuple[str, ...]):
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, '[REDACTED]')
        return value
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, dict):
        return {_redact(key, secrets): _redact(item, secrets) for key, item in value.items()}
    return value


def _secrets(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({value for value in values if value}, key=len, reverse=True))


def _json_size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))


class RunRepository:
    def __init__(self, database: Database, default_image: str = 'repopilot-dev:local') -> None:
        self.database = database
        self.default_image = default_image

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncSession]:
        try:
            async with self.database.session() as session, session.begin():
                yield session
        except SQLAlchemyError:
            raise TaskError(500, '执行数据暂时无法读写，请稍后重试') from None

    @staticmethod
    def _summary(row: RunRow) -> RunSummary:
        return RunSummary(**{key: getattr(row, key) for key in RunSummary.model_fields})

    async def _detail(self, session: AsyncSession, row: RunRow) -> RunDetail:
        events = (await session.scalars(select(RunEventRow).where(
            RunEventRow.run_id == row.id).order_by(RunEventRow.id).limit(MAX_EVENTS))).all()
        return RunDetail(
            **self._summary(row).model_dump(), image=row.image, image_id=row.image_id,
            setup_command=row.setup_command, check_command=row.check_command,
            report=row.report, patch=row.patch, checks=row.checks,
            events=[RunEvent(id=e.id, kind=e.kind, payload=e.payload, created_at=e.created_at) for e in events],
        )

    @staticmethod
    async def _locked(session: AsyncSession, run_id: UUID, task_id: UUID | None = None) -> RunRow:
        query = select(RunRow).where(RunRow.id == run_id)
        if task_id is not None:
            query = query.where(RunRow.task_id == task_id)
        row = await session.scalar(query.with_for_update())
        if row is None:
            raise TaskError(404, '执行记录不存在')
        return row

    @staticmethod
    def _claimed(row: RunRow) -> ClaimedRun:
        return ClaimedRun(
            id=row.id, task_id=row.task_id, worker_token=row.worker_token,
            source_snapshot=SourceSnapshot.model_validate(row.source_snapshot),
            goal_content=GoalContent.model_validate(row.goal_content), image=row.image,
        )

    @staticmethod
    def _owned(row: RunRow, worker_token: UUID) -> bool:
        return row.status == 'running' and row.worker_token == worker_token

    async def create(self, task_id: UUID, payload: CreateRunRequest) -> RunDetail:
        async with self._transaction() as session:
            task = await session.scalar(select(TaskRow).where(TaskRow.id == task_id).with_for_update())
            if task is None:
                raise TaskError(404, '对话不存在')
            if task.revision != payload.expected_revision:
                raise TaskError(409, '目标已更新，请重新确认')
            if task.status != 'approved' or task.current_goal_version != payload.goal_version or task.approved_goal_version != payload.goal_version:
                raise TaskError(409, '只能执行最新的已批准目标')
            active = await session.scalar(select(RunRow.id).where(RunRow.task_id == task_id, RunRow.status.in_(['queued', 'running'])))
            if active is not None:
                raise TaskError(409, '执行尚未结束，请先取消或等待完成')
            goal = await session.get(TaskGoalRow, (task_id, payload.goal_version))
            if goal is None:
                raise TaskError(409, '已批准目标不存在，请重新确认')
            now = await session.scalar(select(func.clock_timestamp()))
            row = RunRow(
                id=uuid4(), task_id=task_id, goal_version=payload.goal_version, status='queued',
                cancel_requested=False, created_at=now, image=self.default_image,
                setup_command='', check_command='',
                source_snapshot=task.source_snapshot, goal_content=goal.content,
                report='', patch='', checks=[],
            )
            session.add(row)
            task.revision += 1
            task.updated_at = now
            await session.flush()
            return await self._detail(session, row)

    async def list(self, task_id: UUID) -> list[RunSummary]:
        async with self._transaction() as session:
            if await session.get(TaskRow, task_id) is None:
                raise TaskError(404, '对话不存在')
            rows = (await session.scalars(select(RunRow).where(RunRow.task_id == task_id)
                .order_by(RunRow.created_at.desc(), RunRow.id.desc()).limit(50))).all()
            return [self._summary(row) for row in rows]

    async def detail(self, task_id: UUID, run_id: UUID) -> RunDetail:
        async with self._transaction() as session:
            return await self._detail(session, await self._locked(session, run_id, task_id))

    async def cancel(self, task_id: UUID, run_id: UUID) -> RunDetail:
        async with self._transaction() as session:
            row = await self._locked(session, run_id, task_id)
            if row.status in {'queued', 'running'}:
                row.cancel_requested = True
                if row.status == 'queued':
                    row.status = 'cancelled'
                    row.finished_at = await session.scalar(select(func.clock_timestamp()))
                    session.add(RunEventRow(run_id=row.id, kind='state', payload={'status': 'cancelled'}, created_at=row.finished_at))
                await session.flush()
            return await self._detail(session, row)

    async def claim(self) -> ClaimedRun | None:
        """FIFO short transactions, never hold locks across external execution."""
        while True:
            async with self._transaction() as session:
                candidate = (await session.execute(select(RunRow.id, RunRow.task_id).where(
                    RunRow.status == 'queued').order_by(RunRow.created_at, RunRow.id).limit(1))).first()
                if candidate is None:
                    return None
                task = await session.scalar(select(TaskRow).where(TaskRow.id == candidate.task_id).with_for_update())
                row = await self._locked(session, candidate.id)
                if row.status != 'queued':
                    continue
                now = await session.scalar(select(func.clock_timestamp()))
                if task is None or task.status != 'approved' or task.current_goal_version != row.goal_version or task.approved_goal_version != row.goal_version:
                    row.status = 'blocked'
                    row.error = '目标已变更或未批准，请重新确认后执行'
                    row.finished_at = now
                    session.add(RunEventRow(run_id=row.id, kind='state', payload={'status': 'blocked', 'error': row.error}, created_at=now))
                    continue
                row.status = 'running'
                row.started_at = now
                row.worker_token = uuid4()
                await session.flush()
                return self._claimed(row)

    async def running(self) -> list[RunSummary]:
        async with self._transaction() as session:
            rows = (await session.scalars(select(RunRow).where(RunRow.status == 'running').order_by(RunRow.created_at, RunRow.id))).all()
            return [self._summary(row) for row in rows]

    async def recover(self, run_id: UUID) -> ClaimedRun | None:
        """Fence the previous worker before attempting orphan container cleanup."""
        async with self._transaction() as session:
            row = await self._locked(session, run_id)
            if row.status != 'running':
                return None
            row.worker_token = uuid4()
            await session.flush()
            return self._claimed(row)

    async def cancellation_requested(self, run_id: UUID, worker_token: UUID) -> bool:
        async with self._transaction() as session:
            row = await session.get(RunRow, run_id)
            return row is None or not self._owned(row, worker_token) or row.cancel_requested

    async def set_model(self, run_id: UUID, worker_token: UUID, *, endpoint: str, model: str) -> bool:
        endpoint = normalize_base_url(endpoint)
        async with self._transaction() as session:
            row = await self._locked(session, run_id)
            if not self._owned(row, worker_token):
                return False
            if row.model_endpoint is not None or row.model_name is not None:
                if row.model_endpoint != endpoint or row.model_name != model:
                    raise TaskError(409, '执行模型已固定，不能更改')
            else:
                row.model_endpoint, row.model_name = endpoint, model
            return True

    async def set_image_id(self, run_id: UUID, worker_token: UUID, image_id: str) -> bool:
        async with self._transaction() as session:
            row = await self._locked(session, run_id)
            if not self._owned(row, worker_token):
                return False
            if row.image_id is not None and row.image_id != image_id:
                raise TaskError(409, '执行镜像已固定，不能更改')
            row.image_id = image_id
            return True

    async def set_execution_commands(
        self, run_id: UUID, worker_token: UUID, *, setup_command: str, check_command: str,
    ) -> bool:
        async with self._transaction() as session:
            row = await self._locked(session, run_id)
            if not self._owned(row, worker_token):
                return False
            if row.check_command:
                if row.setup_command != setup_command or row.check_command != check_command:
                    raise TaskError(409, '执行命令已固定，不能更改')
            else:
                row.setup_command, row.check_command = setup_command, check_command
            return True

    async def append_event(self, run_id: UUID, worker_token: UUID, kind: str, payload: dict, *, secrets: Iterable[str] = ()) -> bool:
        if kind not in EVENT_KINDS or not isinstance(payload, dict):
            raise TaskError(422, '执行事件格式无效')
        payload = _redact(payload, _secrets(secrets))
        try:
            size = _json_size(payload)
        except (ValueError, TypeError):
            raise TaskError(422, '执行事件格式无效') from None
        if size > MAX_EVENT_BYTES:
            raise TaskError(409, '执行事件超过保存上限')
        async with self._transaction() as session:
            row = await self._locked(session, run_id)
            if not self._owned(row, worker_token):
                return False
            count = await session.scalar(select(func.count()).select_from(RunEventRow).where(RunEventRow.run_id == run_id))
            if count >= MAX_EVENTS - 1:
                raise TaskError(409, '执行事件数量达到上限')
            if kind == 'check':
                try:
                    check = RunCheck.model_validate(payload).model_dump(mode='json')
                except ValueError:
                    raise TaskError(422, '执行检查记录格式无效') from None
                accumulated = [*row.checks, check]
                if _json_size(accumulated) > MAX_CHECKS_BYTES:
                    raise TaskError(409, '执行检查记录达到保存上限')
                row.checks = accumulated
            now = await session.scalar(select(func.clock_timestamp()))
            session.add(RunEventRow(run_id=run_id, kind=kind, payload=payload, created_at=now))
            return True

    async def record_cleanup_error(self, run_id: UUID, worker_token: UUID, *, secrets: Iterable[str] = ()) -> bool:
        """Keep running on cleanup failure; a later worker must retry recovery."""
        async with self._transaction() as session:
            row = await self._locked(session, run_id)
            if not self._owned(row, worker_token):
                return False
            row.error = '执行容器清理失败，尚未确认停止，请检查执行环境'
            return True

    async def finish(
        self, run_id: UUID, worker_token: UUID, *, status: str, report: str = '',
        patch: str = '', checks: Iterable[RunCheck | dict] = (), error: str | None = None,
        cleanup_confirmed: bool = False, secrets: Iterable[str] = (),
    ) -> RunDetail | None:
        if status not in TERMINAL_STATUSES:
            raise TaskError(422, '无效的执行终态')
        known = _secrets(secrets)
        report = _redact(report, known)
        error = _redact(error, known)
        stored_checks = _redact([RunCheck.model_validate(check).model_dump(mode='json') for check in checks], known)
        oversized = (len(report.encode('utf-8')) > MAX_REPORT_BYTES or len(patch.encode('utf-8')) > MAX_PATCH_BYTES or _json_size(stored_checks) > MAX_CHECKS_BYTES)
        if oversized:
            status, error, patch = 'failed', LIMIT_ERROR, ''
            if len(report.encode('utf-8')) > MAX_REPORT_BYTES:
                report = '执行报告超过保存上限，未保存完整报告'
            if _json_size(stored_checks) > MAX_CHECKS_BYTES:
                stored_checks = []
        if error is not None and len(error.encode('utf-8')) > MAX_REPORT_BYTES:
            error = '执行失败，错误信息超过保存上限'
        async with self._transaction() as session:
            row = await self._locked(session, run_id)
            if not self._owned(row, worker_token):
                return None
            if row.cancel_requested or status in {'cancelled', 'interrupted'}:
                if not cleanup_confirmed:
                    raise TaskError(409, '尚未确认执行停止和容器清理，不能结束执行')
                if row.cancel_requested:
                    status = 'cancelled'
            row.status, row.report, row.patch, row.checks, row.error = status, report, patch, stored_checks or row.checks, error
            row.finished_at = await session.scalar(select(func.clock_timestamp()))
            session.add(RunEventRow(run_id=run_id, kind='state', payload={'status': status}, created_at=row.finished_at))
            await session.flush()
            return await self._detail(session, row)
