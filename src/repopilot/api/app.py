from __future__ import annotations

import asyncio
import logging
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
from repopilot.persistence.runs import RunRepository
from repopilot.integration.delivery import DeliveryService
from repopilot.persistence.deliveries import DeliveryRepository

from .routes.auth import build_router as build_auth_router
from .routes.models import build_router as build_models_router
from .routes.settings import build_router as build_settings_router
from .routes.tasks import build_router as build_tasks_router
from .routes.runs import build_router as build_runs_router

GENERATION_REAP_SECONDS = 5
logger = logging.getLogger(__name__)


async def reap_generation_leases(
    repository: TaskRepository, stop: asyncio.Event, interval: float,
) -> None:
    """Retire expired goal-generation leases until the API shuts down."""
    while not stop.is_set():
        try:
            await repository.recover_expired()
        except Exception:  # noqa: BLE001 - one failed sweep must not stop later ones
            logger.warning("Generation lease recovery failed; retrying")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


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
    task_repository = TaskRepository(database)
    task_service = TaskService(
        task_repository, model_repository, github_repository,
        GitHubSourceClient(), GoalClient(),
    )
    run_repository = RunRepository(database, default_image=runtime.execution_image)
    delivery_service = DeliveryService(DeliveryRepository(database), github_repository)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop_reaper = asyncio.Event()
        reaper: asyncio.Task | None = None
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
            reaper = asyncio.create_task(
                reap_generation_leases(task_repository, stop_reaper, GENERATION_REAP_SECONDS)
            )
            yield
        finally:
            stop_reaper.set()
            if reaper is not None:
                reaper.cancel()
                await asyncio.gather(reaper, return_exceptions=True)
            try:
                await redis.aclose()
            finally:
                await database.close()

    app = FastAPI(title="RepoPilot API", version="0.1.0", lifespan=lifespan)
    app.state.settings = runtime

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error: RequestValidationError) -> JSONResponse:
        items = []
        for entry in error.errors():
            msg = str(entry.get('msg', '')).removeprefix('Value error, ')
            if not any('\u4e00' <= char <= '\u9fff' for char in msg):
                msg = '请求参数无效'
            items.append({'loc': list(entry.get('loc', ())), 'msg': msg})
        return JSONResponse(status_code=422, content={'detail': items})

    app.include_router(build_auth_router(), prefix="/api")
    app.include_router(build_settings_router(service, github_service), prefix="/api")
    app.include_router(build_models_router(service), prefix="/api")
    app.include_router(build_tasks_router(task_service), prefix="/api")
    app.include_router(build_runs_router(run_repository, delivery_service, runtime.execution_image), prefix="/api")

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
