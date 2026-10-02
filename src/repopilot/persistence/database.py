from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgreSQLUUID, insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from repopilot.domain.settings import ModelSettings


class Base(DeclarativeBase):
    pass


class ModelSettingsRow(Base):
    __tablename__ = "model_settings"
    __table_args__ = (CheckConstraint("id = 1", name="single_model_settings_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)


class GitHubSettingsRow(Base):
    __tablename__ = "github_settings"
    __table_args__ = (CheckConstraint("id = 1", name="single_github_settings_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    private_key_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    api_token_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)


class TaskRow(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'generating', 'awaiting_approval', 'approved', 'generation_failed')",
            name="task_status",
        ),
        CheckConstraint("revision >= 1 AND current_goal_version >= 0", name="task_versions"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    repository_url: Mapped[str] = mapped_column(Text, nullable=False)
    baseline_commit: Mapped[str] = mapped_column(Text, nullable=False)
    issue_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("1"))
    current_goal_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    approved_goal_version: Mapped[int | None] = mapped_column(Integer)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generation_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    generation_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_feedback: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=sql_text("clock_timestamp()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=sql_text("clock_timestamp()"))


Index("tasks_recent", TaskRow.updated_at.desc(), TaskRow.id.desc())


class TaskGoalRow(Base):
    __tablename__ = "task_goals"

    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=sql_text("clock_timestamp()"))


class TaskMessageRow(Base):
    __tablename__ = "task_messages"
    __table_args__ = (
        CheckConstraint("kind IN ('source', 'feedback', 'goal', 'approval')", name="task_message_kind"),
        Index("task_messages_order", "task_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    goal_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=sql_text("clock_timestamp()"))


class Database:
    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(
            url, pool_pre_ping=True, hide_parameters=True, connect_args={"timeout": 10}
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def initialize(self) -> None:
        defaults = ModelSettings()
        async with self.engine.begin() as connection:
            # Serialize schema bootstrap across API workers sharing the database.
            await connection.execute(sql_text("SELECT pg_advisory_xact_lock(721804629)"))
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(
                insert(ModelSettingsRow)
                .values(id=1, base_url=defaults.base_url, model=defaults.model, api_key="")
                .on_conflict_do_nothing(index_elements=[ModelSettingsRow.id])
            )
            await connection.execute(
                insert(GitHubSettingsRow)
                .values(id=1, public_key="", private_key_ciphertext="", api_token_ciphertext="")
                .on_conflict_do_nothing(index_elements=[GitHubSettingsRow.id])
            )

    async def close(self) -> None:
        await self.engine.dispose()

    def session(self) -> AsyncSession:
        return self.sessions()
