from __future__ import annotations

from sqlalchemy import CheckConstraint, Integer, Text, text
from sqlalchemy.dialects.postgresql import insert
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
            await connection.execute(text("SELECT pg_advisory_xact_lock(721804629)"))
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
