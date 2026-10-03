from __future__ import annotations

import asyncio
import os
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_runs import approved_task, request
from test_tasks import database as database
from test_tasks import ready
from test_workspace import TOKEN, command, real_docker, source, source_archive, source_transport

from repopilot.domain.tasks import TaskError
from repopilot.execution.engine import ExecutionEngine, ExecutionResult
from repopilot.execution.workspace import Workspace, WorkspaceError
from repopilot.persistence.database import Database, GitHubSettingsRow, ModelSettingsRow, TaskRow
from repopilot.persistence.runs import RunRepository, RunRow
from repopilot.persistence.settings import GitHubSettingsRepository, SettingsRepository
from repopilot.persistence.tasks import TaskRepository
from repopilot.worker import Worker

pytestmark = pytest.mark.asyncio
LOCK_KEY = 721804630


@pytest_asyncio.fixture
async def repositories(database):
    key = Fernet.generate_key()
    async with database.session() as session, session.begin():
        model = await session.get(ModelSettingsRow, 1)
        model.base_url = 'https://provider.invalid/v1'
        model.model = 'worker-fixture'
        model.api_key = 'worker-model-secret'
        github = await session.get(GitHubSettingsRow, 1)
        github.api_token_ciphertext = Fernet(key).encrypt(TOKEN.encode()).decode()
    return SettingsRepository(database), GitHubSettingsRepository(database, SecretStr(key.decode()))


async def eventually(operation, predicate, *, timeout=12):
    async with asyncio.timeout(timeout):
        while True:
            value = await operation()
            if predicate(value):
                return value
            await asyncio.sleep(0.025)


class ControlledEngine:
    """Only non-Docker orchestration tests use this cancellable external wait."""

    def __init__(self, *, cleanup_error=False, cleanup_wait=False, emit_event=False):
        self.started = asyncio.Queue()
        self.entered = asyncio.Event()
        self.release = asyncio.Queue()
        self.cancelled = asyncio.Event()
        self.cleanup_started = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        if not cleanup_wait:
            self.cleanup_release.set()
        self.cleanup_error = cleanup_error
        self.emit_event = emit_event
        self.calls = []
        self.cleanups = []
        self.effects = []
        self.waiting = asyncio.Queue()

    async def run(self, *, run_id, source, goal, model, github_token, image, emit):
        self.calls.append(run_id)
        self.started.put_nowait(run_id)
        self.entered.set()
        try:
            await emit('state', {
                'status': 'environment_ready', 'setup_command': 'automatic dependency setup',
                'check_command': 'python -m pytest',
            })
            await emit('check', {
                'phase': 'baseline', 'command': 'python -m pytest',
                'exit_code': 0, 'output': 'Baseline passed',
            })
            if self.emit_event:
                await emit('tool_start', {'call_id': 'effect-1', 'name': 'shell'})
                self.effects.append(run_id)
            self.waiting.put_nowait(run_id)
            await self.release.get()
            return ExecutionResult('completed', 'External check passed', '', [])
        except asyncio.CancelledError:
            self.cancelled.set()
            raise

    async def cleanup(self, run_id):
        self.cleanups.append(run_id)
        self.cleanup_started.set()
        await self.cleanup_release.wait()
        if self.cleanup_error:
            raise WorkspaceError('Injected cleanup failure')


@asynccontextmanager
async def running_worker(database, repositories, engine):
    stop = asyncio.Event()
    worker = Worker(database, *repositories, engine=engine)
    job = asyncio.create_task(worker.run(stop))
    try:
        yield stop, job
    finally:
        stop.set()
        engine.cleanup_release.set()
        await asyncio.wait_for(job, 12)


async def enqueue(database, number=1):
    task = await approved_task(database, number)
    run = await RunRepository(database).create(task.id, request(task))
    return task, run


async def test_session_advisory_lock_refuses_second_worker(database, repositories):
    task, run = await enqueue(database)
    engine = ControlledEngine()
    async with database.engine.connect() as owner:
        assert await owner.scalar(text('SELECT pg_try_advisory_lock(:key)'), {'key': LOCK_KEY})
        try:
            await asyncio.wait_for(Worker(database, *repositories, engine=engine).run(), 3)
            assert (await RunRepository(database).detail(task.id, run.id)).status == 'queued'
            assert engine.calls == engine.cleanups == []
        finally:
            await owner.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': LOCK_KEY})


async def test_fifo_is_strictly_sequential_without_external_wait_row_locks(database, repositories):
    first, second = await enqueue(database, 1), await enqueue(database, 2)
    engine = ControlledEngine()
    runs = RunRepository(database)
    async with running_worker(database, repositories, engine) as (stop, job):
        assert await asyncio.wait_for(engine.started.get(), 5) == first[1].id
        assert (await runs.detail(second[0].id, second[1].id)).status == 'queued'
        assert await asyncio.wait_for(engine.waiting.get(), 5) == first[1].id
        # NOWAIT proves both ownership rows are released during the external wait.
        async with database.session() as session, session.begin():
            await session.execute(select(TaskRow).where(TaskRow.id == first[0].id).with_for_update(nowait=True))
            await session.execute(select(RunRow).where(RunRow.id == first[1].id).with_for_update(nowait=True))
        engine.release.put_nowait(None)
        assert await asyncio.wait_for(engine.started.get(), 5) == second[1].id
        assert (await runs.detail(first[0].id, first[1].id)).status == 'completed'
        assert first[1].id in engine.cleanups
        stop.set()
        await asyncio.wait_for(job, 5)
    assert engine.calls == [first[1].id, second[1].id]
    assert (await runs.detail(second[0].id, second[1].id)).status == 'cancelled'


@pytest.mark.parametrize('cleanup_error', [False, True])
async def test_cancel_waits_for_confirmed_cleanup_and_failure_stops_claims(database, repositories, cleanup_error):
    first, second = await enqueue(database, 1), await enqueue(database, 2)
    runs = RunRepository(database)
    engine = ControlledEngine(cleanup_wait=True, cleanup_error=cleanup_error)
    async with running_worker(database, repositories, engine) as (stop, job):
        assert await asyncio.wait_for(engine.started.get(), 5) == first[1].id
        await runs.cancel(first[0].id, first[1].id)
        await asyncio.wait_for(engine.cleanup_started.wait(), 5)
        during = await runs.detail(first[0].id, first[1].id)
        assert during.status == 'running' and during.finished_at is None
        assert engine.cancelled.is_set()
        stop.set()
        engine.cleanup_release.set()
        await asyncio.wait_for(job, 5)
    final = await runs.detail(first[0].id, first[1].id)
    assert final.status == ('running' if cleanup_error else 'cancelled')
    if cleanup_error:
        assert final.error and final.finished_at is None
    else:
        assert final.finished_at is not None
    assert (await runs.detail(second[0].id, second[1].id)).status == 'queued'
    assert engine.calls == [first[1].id]


async def test_preexisting_stop_never_claims_or_executes(database, repositories):
    task, run = await enqueue(database)
    stop = asyncio.Event()
    stop.set()
    engine = ControlledEngine()
    await asyncio.wait_for(Worker(database, *repositories, engine=engine).run(stop), 3)
    assert (await RunRepository(database).detail(task.id, run.id)).status == 'queued'
    assert engine.calls == []


@pytest.mark.parametrize('cleanup_error', [False, True])
async def test_startup_recovery_fences_old_owner_without_tool_replay(database, repositories, cleanup_error):
    task, run = await enqueue(database)
    runs = RunRepository(database)
    stale = await runs.claim()
    await runs.append_event(run.id, stale.worker_token, 'tool_start', {'call_id': 'unknown-action', 'name': 'shell'})
    engine = ControlledEngine(cleanup_wait=True, cleanup_error=cleanup_error)
    async with running_worker(database, repositories, engine) as (stop, job):
        await asyncio.wait_for(engine.cleanup_started.wait(), 5)
        assert not await runs.append_event(run.id, stale.worker_token, 'tool_result', {'call_id': 'unknown-action'})
        assert await runs.finish(run.id, stale.worker_token, status='completed', cleanup_confirmed=True) is None
        assert not await runs.set_image_id(run.id, stale.worker_token, 'sha256:stale')
        assert await runs.cancellation_requested(run.id, stale.worker_token)
        # Recovery cleanup must also happen outside a row transaction.
        async with database.session() as session, session.begin():
            await session.execute(select(RunRow).where(RunRow.id == run.id).with_for_update(nowait=True))
        stop.set()
        engine.cleanup_release.set()
        await asyncio.wait_for(job, 5)
    detail = await runs.detail(task.id, run.id)
    assert detail.status == ('running' if cleanup_error else 'interrupted')
    assert detail.finished_at is None if cleanup_error else detail.finished_at is not None
    assert [event.kind for event in detail.events].count('tool_start') == 1
    assert all(event.kind != 'tool_result' for event in detail.events)
    assert engine.calls == [] and engine.cleanups == [run.id]


@pytest.mark.parametrize('failure', ['commands', 'event', 'cancel_read', 'finish'])
async def test_database_failure_stops_effects_and_preserves_recoverable_run(database, repositories, monkeypatch, failure):
    first, second = await enqueue(database, 1), await enqueue(database, 2)
    engine = ControlledEngine(emit_event=failure == 'event')
    method = {'commands': 'set_execution_commands', 'event': 'append_event',
              'cancel_read': 'cancellation_requested', 'finish': 'finish'}[failure]
    async def unavailable(*args, **kwargs):
        if failure == 'cancel_read':
            await engine.entered.wait()
        raise TaskError(500, '执行数据暂时不可用')
    monkeypatch.setattr(RunRepository, method, unavailable)
    async with running_worker(database, repositories, engine) as (stop, job):
        assert await asyncio.wait_for(engine.started.get(), 5) == first[1].id
        if failure == 'finish':
            engine.release.put_nowait(None)
        await asyncio.wait_for(job, 6)
    runs = RunRepository(database)
    assert (await runs.detail(first[0].id, first[1].id)).status == 'running'
    assert (await runs.detail(second[0].id, second[1].id)).status == 'queued'
    assert engine.calls == [first[1].id] and first[1].id in engine.cleanups
    assert engine.effects == []


async def test_automatic_commands_are_saved_before_baseline(database, repositories, monkeypatch):
    task, run = await enqueue(database)
    engine = ControlledEngine()
    append = RunRepository.append_event
    baseline_saved = asyncio.Event()

    async def inspect(self, run_id, worker_token, kind, payload, **kwargs):
        if kind == 'check' and payload.get('phase') == 'baseline':
            detail = await self.detail(task.id, run_id)
            assert detail.setup_command == 'automatic dependency setup'
            assert detail.check_command == payload['command'] == 'python -m pytest'
            baseline_saved.set()
        return await append(self, run_id, worker_token, kind, payload, **kwargs)

    monkeypatch.setattr(RunRepository, 'append_event', inspect)
    async with running_worker(database, repositories, engine) as (stop, job):
        await asyncio.wait_for(baseline_saved.wait(), 5)
        engine.release.put_nowait(None)
        detail = await eventually(lambda: RunRepository(database).detail(task.id, run.id),
                                  lambda value: value.status == 'completed')
        assert detail.setup_command == 'automatic dependency setup'
        assert detail.check_command == 'python -m pytest'
        assert detail.checks[0].phase == 'baseline'
        stop.set()
        await asyncio.wait_for(job, 5)


async def test_lost_advisory_connection_cancels_and_cleans_without_new_claim(database, repositories):
    first, second = await enqueue(database, 1), await enqueue(database, 2)
    engine = ControlledEngine()
    async with running_worker(database, repositories, engine) as (stop, job):
        assert await asyncio.wait_for(engine.started.get(), 5) == first[1].id
        async with database.engine.connect() as connection:
            pids = (await connection.execute(text('''
                SELECT pid FROM pg_locks WHERE locktype = 'advisory'
                AND classid = 0 AND objid = :key AND granted
                AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
            '''), {'key': LOCK_KEY})).scalars().all()
            assert len(pids) == 1
            assert await connection.scalar(text('SELECT pg_terminate_backend(:pid)'), {'pid': pids[0]})
        await asyncio.wait_for(engine.cleanup_started.wait(), 4)
        await asyncio.wait_for(job, 5)
    assert engine.cancelled.is_set() and engine.cleanups == [first[1].id]
    assert engine.calls == [first[1].id]
    assert (await RunRepository(database).detail(second[0].id, second[1].id)).status == 'queued'


class DockerSleepEngine:
    """Actual Workspace/Docker, controlled source HTTP only; no model is needed to sleep."""

    async def run(self, *, run_id, source, goal, model, github_token, image, emit):
        workspace = Workspace(run_id, source_transport=source_transport(source_archive()))
        try:
            image_id = await workspace.prepare(source=source, github_token=github_token, image=image)
            await emit('state', {'image_id': image_id})
            await emit('state', {
                'status': 'environment_ready', 'setup_command': '', 'check_command': 'true',
            })
            await emit('tool_start', {'call_id': 'sleep-real', 'name': 'shell'})
            await workspace.shell('sleep 50 & wait')
            return ExecutionResult('completed', 'Sleep finished', '', [])
        finally:
            await workspace.cleanup()

    async def cleanup(self, run_id):
        await ExecutionEngine().cleanup(run_id)


async def docker_run(database):
    tasks = TaskRepository(database)
    task = await ready(tasks, await tasks.create(source()))
    task = await tasks.approve(task.id, task.revision, task.current_goal_version)
    return task, await RunRepository(database).create(task.id, request(task))


async def sleeping(run_id):
    code, output = await command('docker', 'top', f'repopilot-run-{run_id}', '-eo', 'pid,args')
    return code == 0 and any(line.split(maxsplit=1)[1:] == ['sleep 50'] for line in output.splitlines())


async def absent(run_id):
    code, _ = await command('docker', 'inspect', f'repopilot-run-{run_id}')
    return code != 0


@real_docker
async def test_real_docker_sleep_cancel_removes_container_before_terminal(database, repositories):
    task, run = await docker_run(database)
    stop = asyncio.Event()
    job = asyncio.create_task(Worker(database, *repositories, engine=DockerSleepEngine()).run(stop))
    try:
        await eventually(lambda: sleeping(run.id), bool, timeout=30)
        await RunRepository(database).cancel(task.id, run.id)
        detail = await eventually(lambda: RunRepository(database).detail(task.id, run.id), lambda value: value.status == 'cancelled', timeout=30)
        assert detail.finished_at is not None
        assert await absent(run.id)
    finally:
        stop.set()
        await asyncio.wait_for(job, 30)
        await ExecutionEngine().cleanup(run.id)


async def subprocess_worker(url, schema, key):
    # This child uses only the per-test schema and disposable encrypted fixture settings.
    db = Database.__new__(Database)
    db.engine = create_async_engine(url, hide_parameters=True, connect_args={'server_settings': {'search_path': schema}})
    db.sessions = async_sessionmaker(db.engine, expire_on_commit=False)
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    try:
        await Worker(db, SettingsRepository(db), GitHubSettingsRepository(db, SecretStr(key)), engine=DockerSleepEngine()).run(stop)
    finally:
        await db.close()


async def launch_child(database, repositories):
    async with database.engine.connect() as connection:
        schema = await connection.scalar(text('SELECT current_schema()'))
    environment = dict(os.environ)
    environment['PYTHONPATH'] = os.pathsep.join([str(Path(__file__).parent), str(Path(__file__).parents[1] / 'src'), environment.get('PYTHONPATH', '')])
    script = 'import asyncio, sys; from test_worker import subprocess_worker; asyncio.run(subprocess_worker(*sys.argv[1:]))'
    return await asyncio.create_subprocess_exec(sys.executable, '-c', script, os.environ['TEST_DATABASE_URL'], schema,
        repositories[1].encryption_key.get_secret_value(), env=environment,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)


@real_docker
@pytest.mark.parametrize('termination', ['kill', 'sigterm'])
async def test_real_worker_subprocess_exit_and_restart(database, repositories, termination):
    task, run = await docker_run(database)
    other, queued = await enqueue(database, 2)
    child = await launch_child(database, repositories)
    restart = None
    try:
        await eventually(lambda: sleeping(run.id), bool, timeout=30)
        child.kill() if termination == 'kill' else child.terminate()
        await asyncio.wait_for(child.wait(), 30)
        runs = RunRepository(database)
        before = await runs.detail(task.id, run.id)
        assert (await runs.detail(other.id, queued.id)).status == 'queued'
        if termination == 'sigterm':
            assert before.status == 'cancelled' and await absent(run.id)
        else:
            assert before.status == 'running'
            assert not await absent(run.id)
            # Cancel queued work so recovery can be observed without any tool replay.
            await runs.cancel(other.id, queued.id)
            restart = await launch_child(database, repositories)
            after = await eventually(lambda: runs.detail(task.id, run.id), lambda value: value.status == 'interrupted', timeout=30)
            assert await absent(run.id)
            assert [event.payload.get('call_id') for event in after.events if event.kind == 'tool_start'] == ['sleep-real']
    finally:
        for process in (child, restart):
            if process is not None and process.returncode is None:
                process.terminate()
                await asyncio.wait_for(process.wait(), 30)
        await ExecutionEngine().cleanup(run.id)


@real_docker
async def test_real_container_cleanup_failure_remains_running_until_recovery(database, repositories, monkeypatch):
    task, run = await docker_run(database)
    other, queued = await enqueue(database, 2)
    stop = asyncio.Event()
    engine = DockerSleepEngine()
    job = asyncio.create_task(Worker(database, *repositories, engine=engine).run(stop))
    try:
        await eventually(lambda: sleeping(run.id), bool, timeout=30)
        # Fault injection begins only AFTER Docker proves the real child is running.
        # No simulated Docker results are used as evidence of cleanup success.
        async def refused_cleanup(self):
            raise WorkspaceError('Injected Docker cleanup refusal')
        with monkeypatch.context() as fault:
            fault.setattr(Workspace, 'cleanup', refused_cleanup)
            await RunRepository(database).cancel(task.id, run.id)
            await asyncio.wait_for(job, 15)
        detail = await RunRepository(database).detail(task.id, run.id)
        assert detail.status == 'running' and detail.finished_at is None and detail.error
        assert not await absent(run.id)
        assert (await RunRepository(database).detail(other.id, queued.id)).status == 'queued'
        await RunRepository(database).cancel(other.id, queued.id)
        recovery_stop = asyncio.Event()
        recovery = asyncio.create_task(Worker(database, *repositories, engine=ExecutionEngine()).run(recovery_stop))
        try:
            await eventually(lambda: RunRepository(database).detail(task.id, run.id), lambda value: value.status == 'cancelled', timeout=30)
            assert await absent(run.id)
        finally:
            recovery_stop.set()
            await asyncio.wait_for(recovery, 15)
    finally:
        stop.set()
        await asyncio.wait_for(job, 30)
        await ExecutionEngine().cleanup(run.id)



async def test_configured_keyless_model_executes_and_completes(database, repositories):
    async with database.session() as session, session.begin():
        model = await session.get(ModelSettingsRow, 1)
        model.api_key = ''
    task, run = await enqueue(database)
    engine = ControlledEngine()
    async with running_worker(database, repositories, engine) as (stop, job):
        assert await asyncio.wait_for(engine.started.get(), 5) == run.id
        engine.release.put_nowait(None)
        completed = await eventually(lambda: RunRepository(database).detail(task.id, run.id), lambda value: value.status == 'completed')
        assert completed.report == 'External check passed'
        assert engine.cleanups == [run.id]
        stop.set()
        await asyncio.wait_for(job, 5)
