from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Text,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from repopilot.domain.runs import RunDelivery
from repopilot.domain.tasks import SourceSnapshot, TaskError
from repopilot.persistence.database import Base, Database
from repopilot.persistence.runs import RunRow

LEASE_DURATION = timedelta(minutes=30)


class RunDeliveryRow(Base):
    __tablename__ = 'run_deliveries'
    __table_args__ = (
        CheckConstraint("status IN ('delivering','failed','completed')", name='run_delivery_status'),
    )

    run_id: Mapped[UUID] = mapped_column(ForeignKey('runs.id'), primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    branch: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    attempt_token: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(Text)
    branch_url: Mapped[str | None] = mapped_column(Text)
    compare_url: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


@dataclass(frozen=True)
class DeliveryClaim:
    run_id: UUID
    token: UUID
    branch: str
    patch: str
    source: SourceSnapshot
    finished_at: datetime


class DeliveryRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def public(row: RunDeliveryRow | None) -> RunDelivery | None:
        if row is None or row.status != 'completed':
            return None
        if row.commit_sha is None or row.branch_url is None or row.compare_url is None:
            raise TaskError(500, '交付记录不完整')
        return RunDelivery(
            status='completed', branch=row.branch, commit_sha=row.commit_sha,
            branch_url=row.branch_url, compare_url=row.compare_url, created_at=row.created_at,
        )

    @staticmethod
    async def _run(session: AsyncSession, task_id: UUID, run_id: UUID) -> RunRow:
        row = await session.scalar(select(RunRow).where(
            RunRow.id == run_id, RunRow.task_id == task_id,
        ).with_for_update())
        if row is None:
            raise TaskError(404, '执行记录不存在')
        return row

    async def claim(self, task_id: UUID, run_id: UUID) -> DeliveryClaim | RunDelivery:
        try:
            async with self.database.session() as session, session.begin():
                run = await self._run(session, task_id, run_id)
                if run.status != 'completed':
                    raise TaskError(409, '只能交付检查通过且已完成的 Run')
                if not run.patch.strip():
                    raise TaskError(409, 'Run 没有可交付的代码 Patch')
                if run.finished_at is None:
                    raise TaskError(409, 'Run 尚未完整结束')
                now = await session.scalar(select(func.clock_timestamp()))
                row = await session.scalar(select(RunDeliveryRow).where(
                    RunDeliveryRow.run_id == run_id,
                ).with_for_update())
                if row is not None and row.status == 'completed':
                    public = self.public(row)
                    assert public is not None
                    return public
                if row is not None and row.status == 'delivering' and row.lease_expires_at > now:
                    raise TaskError(409, '该 Run 正在交付，请等待后重新读取；不会并发重复提交')
                token = uuid4()
                branch = f'repopilot/run-{run.id}'
                if row is None:
                    row = RunDeliveryRow(
                        run_id=run.id, status='delivering', branch=branch, attempt_token=token,
                        lease_expires_at=now + LEASE_DURATION, created_at=now, updated_at=now,
                    )
                    session.add(row)
                else:
                    row.status, row.attempt_token = 'delivering', token
                    row.lease_expires_at, row.updated_at, row.error = now + LEASE_DURATION, now, None
                return DeliveryClaim(
                    run_id=run.id, token=token, branch=branch, patch=run.patch,
                    source=SourceSnapshot.model_validate(run.source_snapshot), finished_at=run.finished_at,
                )
        except TaskError:
            raise
        except SQLAlchemyError:
            raise TaskError(500, '交付数据暂时无法读写，请稍后重试') from None

    async def complete(
        self, claim: DeliveryClaim, *, commit_sha: str, branch_url: str, compare_url: str,
    ) -> RunDelivery:
        try:
            async with self.database.session() as session, session.begin():
                row = await session.scalar(select(RunDeliveryRow).where(
                    RunDeliveryRow.run_id == claim.run_id,
                ).with_for_update())
                if row is None or row.status != 'delivering' or row.attempt_token != claim.token:
                    raise TaskError(409, '交付租约已失效，请重新读取结果')
                now = await session.scalar(select(func.clock_timestamp()))
                row.status, row.commit_sha = 'completed', commit_sha
                row.branch_url, row.compare_url = branch_url, compare_url
                row.updated_at, row.lease_expires_at = now, now
                public = self.public(row)
                assert public is not None
                return public
        except TaskError:
            raise
        except SQLAlchemyError:
            raise TaskError(500, '交付结果暂时无法保存；重新提交将按同一分支核对，不会覆盖') from None

    async def fail(self, claim: DeliveryClaim, detail: str) -> None:
        try:
            async with self.database.session() as session, session.begin():
                row = await session.scalar(select(RunDeliveryRow).where(
                    RunDeliveryRow.run_id == claim.run_id,
                ).with_for_update())
                if row is not None and row.status == 'delivering' and row.attempt_token == claim.token:
                    now = await session.scalar(select(func.clock_timestamp()))
                    row.status, row.error, row.updated_at, row.lease_expires_at = 'failed', detail[:4096], now, now
        except SQLAlchemyError:
            return
