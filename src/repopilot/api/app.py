from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from repopilot.application.settings import SettingsService
from repopilot.config import AppSettings, get_settings
from repopilot.integration.cache import ModelCache
from repopilot.persistence.database import Database
from repopilot.persistence.settings import SettingsRepository, SettingsStorageError

from .routes.auth import build_router as build_auth_router
from .routes.models import build_router as build_models_router
from .routes.settings import build_router as build_settings_router


def create_app(settings: AppSettings | None = None) -> FastAPI:
    runtime = settings or get_settings()
    try:
        database = Database(runtime.database_url)
        redis = Redis.from_url(
            runtime.redis_url, decode_responses=True, socket_timeout=5, socket_connect_timeout=5
        )
    except (ArgumentError, ValueError):
        raise RuntimeError("Invalid PostgreSQL or Redis connection configuration") from None
    service = SettingsService(SettingsRepository(database), ModelCache(redis))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            try:
                await database.initialize()
                await service.public()
            except (SQLAlchemyError, OSError, SettingsStorageError):
                raise RuntimeError("Unable to initialize PostgreSQL settings storage") from None
            try:
                await redis.ping()
            except (RedisError, OSError):
                raise RuntimeError("Unable to connect to Redis model cache") from None
            yield
        finally:
            try:
                await redis.aclose()
            finally:
                await database.close()

    app = FastAPI(title="RepoPilot API", version="0.1.0", lifespan=lifespan)
    app.state.settings = runtime
    app.state.database = database
    app.state.redis = redis

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "Invalid request payload"})

    app.include_router(build_auth_router(), prefix="/api")
    app.include_router(build_settings_router(service), prefix="/api")
    app.include_router(build_models_router(service), prefix="/api")

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
