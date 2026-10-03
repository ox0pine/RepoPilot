from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from repopilot.config import get_settings
from repopilot.domain.runs import ClaimedRun
from repopilot.domain.tasks import TaskError
from repopilot.execution.engine import EmissionError, ExecutionEngine, ExecutionResult
from repopilot.execution.git import GitHubGitCredentials
from repopilot.persistence.database import Database
from repopilot.persistence.runs import RunRepository
from repopilot.persistence.settings import (
    GitHubEncryptionUnavailableError,
    GitHubSettingsRepository,
    SettingsRepository,
    SettingsStorageError,
)

LOCK_KEY = 721804630
HEARTBEAT_SECONDS = 2
POLL_SECONDS = 1
CANCEL_POLL_SECONDS = 1
logger = logging.getLogger(__name__)


class _PersistenceFailure(Exception):
    """Leave the run recoverable rather than writing an unconfirmed outcome."""


class Worker:
    def __init__(self, database: Database, model_repository: SettingsRepository,
                 github_repository: GitHubSettingsRepository, *,
                 engine: ExecutionEngine | None = None) -> None:
        self.database = database
        self.model_repository = model_repository
        self.github_repository = github_repository
        self.engine = engine if engine is not None else ExecutionEngine()
        self.repository = RunRepository(database)

    async def _heartbeat(self, connection: AsyncConnection, lost: asyncio.Event) -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_SECONDS)
                # The connection is dedicated to the session lock, never returned
                # to the pool or used for run transactions while work is active.
                async with asyncio.timeout(HEARTBEAT_SECONDS):
                    await connection.execute(text('SELECT 1'))
        except asyncio.CancelledError:
            raise
        except Exception:
            lost.set()
            logger.error('Worker lock connection lost; stopping execution')

    async def _cleanup(self, run: ClaimedRun, secrets: tuple[str, ...] = ()) -> bool:
        try:
            await self.engine.cleanup(run.id)
            return True
        except Exception:
            # Never log exception text: provider/transport exceptions can contain
            # credentials, SQL parameters or untrusted repository output.
            logger.error('Run container cleanup failed; retaining running state')
            with suppress(Exception):
                await self.repository.record_cleanup_error(run.id, run.worker_token, secrets=secrets)
            return False

    async def _recover(self, stop: asyncio.Event, lost: asyncio.Event) -> bool:
        for summary in await self.repository.running():
            if lost.is_set():
                return False
            run = await self.repository.recover(summary.id)
            if run is None:
                continue
            if not await self._cleanup(run):
                return False
            if lost.is_set():
                return False
            finished = await self.repository.finish(
                run.id, run.worker_token, status='interrupted',
                error='执行进程已中断，请从基准版本重新执行', cleanup_confirmed=True,
            )
            if finished is None:
                return False
        return not stop.is_set() and not lost.is_set()

    async def _cancel_watch(self, run: ClaimedRun) -> None:
        while True:
            if await self.repository.cancellation_requested(run.id, run.worker_token):
                return
            await asyncio.sleep(CANCEL_POLL_SECONDS)

    async def _execute(self, run: ClaimedRun, stop: asyncio.Event, lost: asyncio.Event) -> bool:
        secrets: tuple[str, ...] = ()
        persistence_failed = False

        async def execute() -> ExecutionResult:
            nonlocal secrets, persistence_failed
            if lost.is_set() or stop.is_set():
                return ExecutionResult('cancelled', '', '', [])
            try:
                model = await self.model_repository.load()
                github = await self.github_repository.load()
            except SettingsStorageError:
                persistence_failed = True
                raise _PersistenceFailure from None
            token = github.api_token.get_secret_value()
            private_key = github.private_key.get_secret_value()
            secrets = (model.api_key, token, private_key)
            if not await self.repository.set_model(
                run.id, run.worker_token, endpoint=model.base_url, model=model.model,
            ):
                persistence_failed = True
                raise _PersistenceFailure
            if not model.model.strip() or not (private_key or token):
                return ExecutionResult('blocked', '执行需要已选择的模型和 GitHub 克隆凭据', '', [])
            git_auth_strategy = 'ssh_key' if private_key else 'https_token'

            async def emit(kind: str, payload: dict) -> None:
                nonlocal persistence_failed
                try:
                    if kind == 'state' and payload.get('image_id') is not None:
                        if not await self.repository.set_image_id(run.id, run.worker_token, payload['image_id']):
                            raise _PersistenceFailure
                    if kind == 'state' and payload.get('status') == 'environment_ready':
                        if not await self.repository.set_execution_commands(
                            run.id, run.worker_token,
                            setup_command=payload['setup_command'], check_command=payload['check_command'],
                        ):
                            raise _PersistenceFailure
                    if not await self.repository.append_event(
                        run.id, run.worker_token, kind, payload, secrets=secrets,
                    ):
                        raise _PersistenceFailure
                except Exception:
                    persistence_failed = True
                    raise _PersistenceFailure from None

            if lost.is_set() or stop.is_set():
                return ExecutionResult('cancelled', '', '', [])
            return await self.engine.run(
                run_id=run.id, source=run.source_snapshot, goal=run.goal_content,
                model=model,
                github_credentials=GitHubGitCredentials(api_token=token, private_key=private_key),
                git_auth_strategy=git_auth_strategy, image=run.image, emit=emit,
            )

        task = asyncio.create_task(execute())
        cancellation = asyncio.create_task(self._cancel_watch(run))
        stopping = asyncio.create_task(stop.wait())
        lock_loss = asyncio.create_task(lost.wait())
        result: ExecutionResult | None = None
        status = 'failed'
        error: str | None = None
        retain_running = False
        try:
            done, _ = await asyncio.wait(
                (task, cancellation, stopping, lock_loss), return_when=asyncio.FIRST_COMPLETED,
            )
            if lost.is_set():
                retain_running = True
            elif cancellation in done:
                # A failed cancellation read is a persistence failure, not a
                # request to cancel. Do not invent a terminal state.
                cancellation.result()
                status = 'cancelled'
            elif stop.is_set():
                status = 'cancelled'
            else:
                result = task.result()
                status = result.status
                if status in {'failed', 'blocked', 'exhausted'}:
                    error = result.report
        except (SettingsStorageError, _PersistenceFailure, EmissionError, TaskError):
            retain_running = True
        except GitHubEncryptionUnavailableError:
            status, error = 'blocked', '执行需要有效的 GitHub 凭据加密主密钥'
        except asyncio.CancelledError:
            # Also support cancellation by an embedding process: finish cleanup
            # but leave ownership for startup recovery, then propagate.
            retain_running = True
            raise
        except Exception:
            if cancellation.done() and not cancellation.cancelled() and cancellation.exception() is not None:
                retain_running = True
            else:
                status, error = 'failed', '执行失败；成果未能完整捕获，请查看已保存的执行记录'
        finally:
            if not task.done():
                task.cancel()
            # Await the engine's own cancellation/finally path before the
            # idempotent second cleanup, preventing a late container creation.
            await asyncio.gather(task, return_exceptions=True)
            for watcher in (cancellation, stopping, lock_loss):
                watcher.cancel()
            await asyncio.gather(cancellation, stopping, lock_loss, return_exceptions=True)
            cleaned = await self._cleanup(run, secrets)
        if not cleaned or retain_running or persistence_failed or lost.is_set():
            return False
        finished = await self.repository.finish(
            run.id, run.worker_token, status=status,
            report=result.report if result else '', patch=result.patch if result else '',
            checks=result.checks if result else (), error=error,
            cleanup_confirmed=True, secrets=secrets,
        )
        return finished is not None and not stop.is_set() and not lost.is_set()

    async def run(self, stop: asyncio.Event | None = None) -> None:
        stop = stop if stop is not None else asyncio.Event()
        lost = asyncio.Event()
        heartbeat: asyncio.Task | None = None
        acquired = False
        async with self.database.engine.connect() as connection:
            # Session locks require a live dedicated physical connection, not a
            # transaction held open across Docker/model operations.
            await connection.execution_options(isolation_level='AUTOCOMMIT')
            try:
                acquired = bool(await connection.scalar(text(f'SELECT pg_try_advisory_lock({LOCK_KEY})')))
                if not acquired:
                    logger.warning('Another worker already holds the execution lock; exiting')
                    return
                heartbeat = asyncio.create_task(self._heartbeat(connection, lost))
                if not await self._recover(stop, lost):
                    return
                while not stop.is_set() and not lost.is_set():
                    run = await self.repository.claim()
                    if run is not None:
                        if not await self._execute(run, stop, lost):
                            return
                    else:
                        waiters = [asyncio.create_task(stop.wait()), asyncio.create_task(lost.wait())]
                        try:
                            await asyncio.wait(waiters, timeout=POLL_SECONDS, return_when=asyncio.FIRST_COMPLETED)
                        finally:
                            for waiter in waiters:
                                waiter.cancel()
                            await asyncio.gather(*waiters, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error('Worker stopped safely; unfinished runs will require recovery')
            finally:
                if heartbeat is not None:
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)
                if acquired:
                    try:
                        if lost.is_set():
                            await connection.invalidate()
                        else:
                            await connection.execute(text(f'SELECT pg_advisory_unlock({LOCK_KEY})'))
                    except Exception:
                        # A pooled connection must never retain the session lock.
                        await connection.invalidate()


async def _main() -> None:
    settings = get_settings()
    database = Database(settings.database_url)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    try:
        await database.initialize()
        await Worker(
            database, SettingsRepository(database),
            GitHubSettingsRepository(database, settings.github_credentials_key),
        ).run(stop)
    finally:
        await database.close()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(signum)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
    asyncio.run(_main())


if __name__ == '__main__':
    main()
