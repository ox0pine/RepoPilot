from __future__ import annotations

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from repopilot.domain.settings import ModelSettings
from repopilot.persistence.database import Database, ModelSettingsRow


class StoredModelSettings(ModelSettings):
    api_key: str = Field(default="", repr=False)


class SettingsStorageError(Exception):
    pass


class SettingsRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def load(self) -> StoredModelSettings:
        try:
            async with self.database.session() as session:
                row = await session.scalar(select(ModelSettingsRow).where(ModelSettingsRow.id == 1))
        except (SQLAlchemyError, OSError):
            raise SettingsStorageError("Unable to read saved model settings") from None
        if row is None:
            raise SettingsStorageError("Saved model settings are unavailable")
        try:
            return StoredModelSettings(base_url=row.base_url, model=row.model, api_key=row.api_key)
        except ValueError:
            raise SettingsStorageError("Saved model settings are invalid") from None

    async def save(self, settings: ModelSettings, api_key: str | None) -> StoredModelSettings:
        try:
            async with self.database.session() as session:
                async with session.begin():
                    row = await session.scalar(
                        select(ModelSettingsRow).where(ModelSettingsRow.id == 1).with_for_update()
                    )
                    if row is None:
                        raise SettingsStorageError("Saved model settings are unavailable")
                    if api_key is not None:
                        token = api_key.strip()
                    else:
                        token = row.api_key if settings.base_url == row.base_url else ""
                    row.base_url = settings.base_url
                    row.model = settings.model
                    row.api_key = token
                    saved = StoredModelSettings(**settings.model_dump(), api_key=token)
                return saved
        except (SQLAlchemyError, OSError):
            raise SettingsStorageError("Unable to save model settings") from None
