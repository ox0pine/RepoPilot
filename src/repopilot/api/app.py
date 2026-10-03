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
from repopilot.application.settings import GitHubSettingsService
from repopilot.integration.github import GitHubClient
from repopilot.persistence.settings import GitHubSettingsRepository
from repopilot.application.tasks import TaskService
from repopilot.integration.goals import GoalClient
from repopilot.integration.task_sources import GitHubSourceClient
from repopilot.persistence.tasks import TaskRepository
from repopilot.application.runs import RunService
from repopilot.persistence.runs import RunRepository

from .routes.auth import build_router as build_auth_router
from .routes.models import build_router as build_models_router
from .routes.settings import build_router as build_settings_router
from .routes.tasks import build_router as build_tasks_router
from .routes.runs import build_router as build_runs_router


def create_app(settings: AppSettings | None = None) -> FastAPI:
    runtime = settings or get_settings()
    try:
        database = Database(runtime.database_url)
        redis = Redis.from_url(
            runtime.redis_url, decode_responses=True, socket_timeout=5, socket_connect_timeout=5
        )
    except (ArgumentError, ValueError):
        raise RuntimeError("Invalid PostgreSQL or Redis connection configuration") from None
    model_repository = SettingsRepository(database)
    github_repository = GitHubSettingsRepository(database, runtime.github_credentials_key)
    service = SettingsService(model_repository, ModelCache(redis))
    github_service = GitHubSettingsService(github_repository, GitHubClient())
    task_service = TaskService(
        TaskRepository(database), model_repository, github_repository,
        GitHubSourceClient(), GoalClient(),
    )
    run_service = RunService(RunRepository(database, default_image=runtime.execution_image))

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
        detail = "请求参数无效，请检查来源链接、完整 SHA 或操作版本" if request.url.path.startswith('/api/tasks') else "Invalid request payload"
        return JSONResponse(status_code=422, content={"detail": detail})

    app.include_router(build_auth_router(), prefix="/api")
    app.include_router(build_settings_router(service, github_service), prefix="/api")
    app.include_router(build_models_router(service), prefix="/api")
    app.include_router(build_tasks_router(task_service), prefix="/api")
    app.include_router(build_runs_router(run_service, runtime.execution_image), prefix="/api")

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
