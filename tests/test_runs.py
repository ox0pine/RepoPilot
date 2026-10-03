from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text, update
from test_tasks import api_client as api_client
from test_tasks import database as database
from test_tasks import goal, ready, source

from repopilot.domain.runs import CreateRunRequest
from repopilot.domain.tasks import TaskError
from repopilot.persistence.database import TaskRow
from repopilot.persistence.runs import RunRepository
from repopilot.persistence.tasks import TaskRepository


def request(task, **changes):
    values = dict(expected_revision=task.revision, goal_version=task.current_goal_version)
    values.update(changes)
    return CreateRunRequest(**values)


async def approved_task(database, number=1):
    tasks = TaskRepository(database)
    task = await ready(tasks, await tasks.create(source(number)))
    return await tasks.approve(task.id, task.revision, task.current_goal_version)


@pytest.mark.parametrize('changes', [
    {'expected_revision': 0}, {'expected_revision': True},
    {'goal_version': 0}, {'goal_version': True}, {'goal_version': '1'},
    {'image': 'repopilot-dev:local'}, {'setup_command': ''}, {'check_command': 'python -m pytest'},
    {'host_path': '/tmp'}, {'environment': {'TOKEN': 'secret'}}, {'docker_args': ['--privileged']},
])
def test_run_request_rejects_invalid_or_privileged_input(changes):
    payload = dict(expected_revision=1, goal_version=1)
    payload.update(changes)
    with pytest.raises(ValidationError):
        CreateRunRequest(**payload)


def test_run_request_contains_only_approval_versions():
    assert CreateRunRequest(expected_revision=1, goal_version=1).model_dump() == {
        'expected_revision': 1, 'goal_version': 1,
    }


async def test_start_requires_latest_approved_goal_and_revision(database):
    tasks, runs = TaskRepository(database), RunRepository(database)
    task = await tasks.create(source())
    with pytest.raises(TaskError) as missing_goal:
        await runs.create(task.id, request(task, goal_version=1))
    assert missing_goal.value.status_code == 409
    task = await ready(tasks, task)
    with pytest.raises(TaskError) as unapproved:
        await runs.create(task.id, request(task))
    assert unapproved.value.status_code == 409
    task = await tasks.approve(task.id, task.revision, 1)
    with pytest.raises(TaskError) as stale:
        await runs.create(task.id, request(task, expected_revision=task.revision - 1))
    assert stale.value.status_code == 409
    with pytest.raises(TaskError) as mismatch:
        await runs.create(task.id, request(task, goal_version=2))
    assert mismatch.value.status_code == 409
    assert await runs.list(task.id) == []
    attempt = await tasks.start_generation(task.id, task.revision, 'revise', 'Narrow the goal')
    task = await tasks.complete_generation(task.id, attempt.generation_id, goal('Revised'), 'test-model')
    task = await tasks.approve(task.id, task.revision, 2)
    with pytest.raises(TaskError) as old_goal:
        await runs.create(task.id, request(task, goal_version=1))
    assert old_goal.value.status_code == 409
    run = await runs.create(task.id, request(task))
    assert run.goal_version == 2 and run.status == 'queued'
    with pytest.raises(TaskError) as missing_task:
        await runs.create(uuid4(), request(task))
    assert missing_task.value.status_code == 404


async def test_concurrent_starts_enqueue_once_and_guard_goal_generation(database):
    tasks, runs = TaskRepository(database), RunRepository(database)
    task = await approved_task(database)
    results = await asyncio.gather(runs.create(task.id, request(task)),
                                   runs.create(task.id, request(task)), return_exceptions=True)
    accepted = [result for result in results if not isinstance(result, Exception)]
    rejected = [result for result in results if isinstance(result, TaskError)]
    assert len(accepted) == len(rejected) == 1
    assert rejected[0].status_code == 409
    current = await tasks.detail(task.id)
    assert current.revision == task.revision + 1
    assert [run.id for run in await runs.list(task.id)] == [accepted[0].id]
    with pytest.raises(TaskError) as active:
        await tasks.start_generation(task.id, current.revision, 'revise', 'Different scope')
    assert active.value.status_code == 409
    unchanged = await tasks.detail(task.id)
    assert unchanged.revision == current.revision and unchanged.approved_goal_version == 1
    assert unchanged.goals == current.goals
    other = await approved_task(database, 2)
    assert (await runs.create(other.id, request(other))).status == 'queued'
    await runs.cancel(task.id, accepted[0].id)
    with pytest.raises(TaskError) as fresh_duplicate:
        await runs.create(other.id, request(await tasks.detail(other.id)))
    assert fresh_duplicate.value.status_code == 409
    await tasks.start_generation(task.id, current.revision, 'revise', 'Different scope')
    resumed = await tasks.detail(task.id)
    assert resumed.status == 'generating'
    assert resumed.approved_goal_version is None and resumed.approved_at is None


async def test_queued_cancel_is_terminal_idempotent_and_task_scoped(database):
    tasks, runs = TaskRepository(database), RunRepository(database)
    task, other = await approved_task(database), await approved_task(database, 2)
    run = await runs.create(task.id, request(task))
    for operation in (runs.detail, runs.cancel):
        with pytest.raises(TaskError) as hidden:
            await operation(other.id, run.id)
        assert hidden.value.status_code == 404
        with pytest.raises(TaskError) as absent:
            await operation(task.id, uuid4())
        assert absent.value.status_code == 404
    cancelled = await runs.cancel(task.id, run.id)
    assert cancelled.status == 'cancelled'
    assert cancelled.started_at is None and cancelled.finished_at is not None
    repeated = await runs.cancel(task.id, run.id)
    assert repeated.model_dump() == cancelled.model_dump()
    assert (await runs.detail(task.id, run.id)).model_dump() == cancelled.model_dump()
    current = await tasks.detail(task.id)
    next_run = await runs.create(task.id, request(current))
    assert next_run.id != run.id and next_run.status == 'queued'
    assert [item.id for item in await runs.list(task.id)] == [next_run.id, run.id]


async def test_authenticated_run_api_and_secret_nonstorage(api_client, database):
    client, calls, controls = api_client
    payload = dict(repository_url='https://github.com/example/repo', baseline_commit='a' * 40,
                   issue_url='https://github.com/example/repo/issues/1')
    created = await client.post('/api/tasks', json=payload)
    assert created.status_code == 201
    task = created.json()
    path = '/api/tasks/' + task['id']
    task = (await client.post(path + '/goal', json=dict(expected_revision=task['revision'], action='generate'))).json()
    task = (await client.post(path + '/approve', json=dict(expected_revision=task['revision'], goal_version=1))).json()
    assert (await client.get(path + '/runs')).json() == []
    execution = await client.get('/api/execution')
    assert execution.status_code == 200
    body = dict(expected_revision=task['revision'], goal_version=1)
    for former_input in ({'image': 'user-image:latest'}, {'setup_command': ''}, {'check_command': 'true'}):
        assert (await client.post(path + '/runs', json=body | former_input)).status_code == 422
    count = len(calls)
    response = await client.post(path + '/runs', json=body)
    assert response.status_code == 202
    run = response.json()
    run_path = path + '/runs/' + run['id']
    assert run['status'] == 'queued' and run['task_id'] == task['id']
    assert run['image_id'] is None and run['started_at'] is None and run['finished_at'] is None
    assert run['image'] == execution.json()['default_image']
    assert run['setup_command'] == run['check_command'] == ''
    assert run['report'] == run['patch'] == '' and run['checks'] == []
    assert len(calls) == count
    assert (await client.post(path + '/runs', json=body)).status_code == 409
    assert (await client.get(run_path)).json() == run
    listed = (await client.get(path + '/runs')).json()
    assert [item['id'] for item in listed] == [run['id']]
    assert 'setup_command' not in listed[0] and 'events' not in listed[0]
    latest = (await client.get(path)).json()
    assert latest['revision'] == task['revision'] + 1
    assert (await client.post(path + '/goal', json=dict(expected_revision=latest['revision'], action='revise', feedback='Change scope'))).status_code == 409
    # Inspect only Run-owned tables: settings legitimately retain the configured credentials.
    async with database.session() as session:
        for table in ('runs', 'run_events'):
            persisted = (await session.execute(text(f'SELECT row_to_json(value)::text FROM {table} AS value'))).scalars().all()
            serialized = '\n'.join(persisted)
            assert 'provider-secret-marker' not in serialized and 'github-secret-marker' not in serialized
    public = json.dumps(run)
    assert 'provider-secret-marker' not in public and 'github-secret-marker' not in public
    assert 'source_snapshot' not in run and 'goal_content' not in run and 'api_key' not in run
    cancelled = await client.post(run_path + '/cancel')
    assert cancelled.status_code == 200 and cancelled.json()['status'] == 'cancelled'
    assert (await client.post(run_path + '/cancel')).json() == cancelled.json()
    client.headers.clear()
    for method, route, data in [
        ('GET', '/api/execution', None), ('POST', path + '/runs', body),
        ('GET', path + '/runs', None), ('GET', run_path, None),
        ('POST', run_path + '/cancel', None),
    ]:
        assert (await client.request(method, route, json=data)).status_code == 401


async def test_run_uses_system_image_and_empty_initial_commands(database):
    task = await approved_task(database)
    runs = RunRepository(database, default_image='system-image:configured')
    run = await runs.create(task.id, request(task))
    assert run.image == 'system-image:configured'
    assert run.setup_command == run.check_command == ''
    claimed = await runs.claim()
    assert claimed.image == run.image


async def test_execution_commands_are_fenced_and_frozen_after_resolution(database):
    runs = RunRepository(database)
    task = await approved_task(database)
    run = await runs.create(task.id, request(task))
    assert not await runs.set_execution_commands(
        run.id, uuid4(), setup_command='stale setup', check_command='stale check',
    )
    claimed = await runs.claim()
    assert await runs.set_execution_commands(
        run.id, claimed.worker_token, setup_command='preparing', check_command='',
    )
    commands = dict(setup_command='python -m pip install -r requirements.txt', check_command='python -m pytest')
    assert await runs.set_execution_commands(run.id, claimed.worker_token, **commands)
    assert await runs.set_execution_commands(run.id, claimed.worker_token, **commands)
    for changes in ({'setup_command': 'different'}, {'check_command': 'different'}, {'check_command': ''}):
        with pytest.raises(TaskError) as frozen:
            await runs.set_execution_commands(run.id, claimed.worker_token, **(commands | changes))
        assert frozen.value.status_code == 409
    recovered = await runs.recover(run.id)
    assert not await runs.set_execution_commands(
        run.id, claimed.worker_token, setup_command='late', check_command='late',
    )
    assert await runs.set_execution_commands(run.id, recovered.worker_token, **commands)
    detail = await runs.detail(task.id, run.id)
    assert detail.setup_command == commands['setup_command'] and detail.check_command == commands['check_command']
    await runs.finish(run.id, recovered.worker_token, status='interrupted', cleanup_confirmed=True)
    assert not await runs.set_execution_commands(run.id, recovered.worker_token, **commands)


@pytest.mark.parametrize('approval_change', [
    {'approved_goal_version': None, 'approved_at': None},
    {'current_goal_version': 2},
    {'approved_goal_version': 2},
])
async def test_claim_fifo_skips_cancelled_and_invalid_approval(database, approval_change):
    tasks, runs = TaskRepository(database), RunRepository(database)
    first, second, third = [await approved_task(database, number) for number in (1, 2, 3)]
    cancelled = await runs.create(first.id, request(first))
    invalid = await runs.create(second.id, request(second))
    valid = await runs.create(third.id, request(third))
    await runs.cancel(first.id, cancelled.id)
    async with database.session() as session, session.begin():
        await session.execute(update(TaskRow).where(TaskRow.id == second.id).values(**approval_change))
    claimed = await runs.claim()
    assert claimed.id == valid.id
    assert (await runs.detail(second.id, invalid.id)).status == 'blocked'
    assert (await runs.detail(first.id, cancelled.id)).started_at is None
    detail = await runs.detail(third.id, valid.id)
    assert detail.status == 'running' and detail.started_at is not None
    assert claimed.source_snapshot == third.source_snapshot
    assert claimed.goal_content == third.goals[-1].content
    assert await runs.claim() is None
    assert [item.id for item in await runs.running()] == [valid.id]
    current = await tasks.detail(third.id)
    with pytest.raises(TaskError) as guarded:
        await tasks.start_generation(third.id, current.revision, 'revise', 'Change scope')
    assert guarded.value.status_code == 409


async def test_running_cancel_requires_cleanup_and_wins_completion_race(database):
    runs = RunRepository(database)
    task = await approved_task(database)
    run = await runs.create(task.id, request(task))
    claimed = await runs.claim()
    stopping = await runs.cancel(task.id, run.id)
    assert stopping.status == 'running' and stopping.cancel_requested
    assert stopping.finished_at is None
    assert (await runs.cancel(task.id, run.id)).model_dump() == stopping.model_dump()
    with pytest.raises(TaskError) as unclean:
        await runs.finish(run.id, claimed.worker_token, status='completed', report='Done')
    assert unclean.value.status_code == 409
    assert (await runs.detail(task.id, run.id)).status == 'running'
    ended = await runs.finish(run.id, claimed.worker_token, status='completed', cleanup_confirmed=True)
    assert ended.status == 'cancelled' and ended.finished_at is not None
    assert (await runs.cancel(task.id, run.id)).model_dump() == ended.model_dump()
    assert await runs.finish(run.id, claimed.worker_token, status='failed', error='Late error', cleanup_confirmed=True) is None
    assert not await runs.append_event(run.id, claimed.worker_token, 'assistant', {'text': 'Late result'})
    assert (await runs.detail(task.id, run.id)).model_dump() == ended.model_dump()


async def test_recovery_replaces_worker_token_and_requires_cleanup(database):
    runs = RunRepository(database)
    task = await approved_task(database)
    run = await runs.create(task.id, request(task))
    old = await runs.claim()
    assert await runs.append_event(run.id, old.worker_token, 'assistant', {'text': 'Saved evidence'})
    recovered = await runs.recover(run.id)
    assert recovered.worker_token != old.worker_token
    assert not await runs.set_model(run.id, old.worker_token, endpoint='https://old.example/v1', model='old')
    assert not await runs.append_event(run.id, old.worker_token, 'assistant', {'text': 'Stale evidence'})
    assert await runs.finish(run.id, old.worker_token, status='completed', cleanup_confirmed=True) is None
    with pytest.raises(TaskError) as unclean:
        await runs.finish(run.id, recovered.worker_token, status='interrupted')
    assert unclean.value.status_code == 409
    assert (await runs.detail(task.id, run.id)).status == 'running'
    interrupted = await runs.finish(run.id, recovered.worker_token, status='interrupted',
                                   error='Worker stopped', cleanup_confirmed=True)
    assert interrupted.status == 'interrupted'
    texts = [event.payload.get('text') for event in interrupted.events]
    assert 'Saved evidence' in texts and 'Stale evidence' not in texts
    assert await runs.recover(run.id) is None


async def test_execution_evidence_redacts_credentials_and_preserves_patch(database):
    from repopilot.domain.runs import RunCheck
    from repopilot.persistence.runs import RunRow
    runs = RunRepository(database)
    task = await approved_task(database)
    run = await runs.create(task.id, request(task))
    claimed = await runs.claim()
    secrets = ('provider-secret-marker', 'github-secret-marker')
    assert await runs.set_model(run.id, claimed.worker_token,
                               endpoint='https://model.example/v1', model='execution-model')
    assert await runs.append_event(run.id, claimed.worker_token, 'tool_result',
                                  {'call_id': 'call-1', 'output': 'provider-secret-marker',
                                   'nested': {'text': 'github-secret-marker'}}, secrets=secrets)
    patch = 'diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-return a-b\n+return a+b\n'
    checks = [RunCheck(phase='baseline', command='python -m pytest', exit_code=1,
                       output='github-secret-marker failed', truncated=False),
              RunCheck(phase='final', command='python -m pytest', exit_code=0,
                       output='passed', truncated=False)]
    ended = await runs.finish(run.id, claimed.worker_token, status='completed',
                             report='provider-secret-marker fixed', patch=patch,
                             checks=checks, cleanup_confirmed=True, secrets=secrets)
    assert ended.status == 'completed' and ended.patch == patch
    assert [(check.phase, check.exit_code) for check in ended.checks] == [('baseline', 1), ('final', 0)]
    encoded = ended.model_dump_json()
    assert all(secret not in encoded for secret in secrets)
    async with database.session() as session:
        row = await session.get(RunRow, run.id)
        assert row.model_endpoint == 'https://model.example/v1' and row.model_name == 'execution-model'
        persisted = (await session.execute(text('SELECT row_to_json(value)::text FROM runs AS value'))).scalars().all()
        assert all(secret not in '\n'.join(persisted) for secret in secrets)
    restored = await RunRepository(database).detail(task.id, run.id)
    assert restored.model_dump() == ended.model_dump()


@pytest.mark.parametrize('field,limit', [('report', 64 * 1024), ('patch', 8 * 1024 * 1024)])
async def test_oversized_results_fail_without_publishing_partial_patch(database, field, limit):
    runs = RunRepository(database)
    task = await approved_task(database)
    run = await runs.create(task.id, request(task))
    claimed = await runs.claim()
    assert await runs.append_event(run.id, claimed.worker_token, 'assistant', {'text': 'Earlier evidence'})
    values = {'report': 'Report', 'patch': 'Complete small patch'}
    values[field] = '界' * (limit // 3 + 1)
    ended = await runs.finish(run.id, claimed.worker_token, status='completed',
                             cleanup_confirmed=True, **values)
    assert ended.status == 'failed' and ended.error
    assert len(ended.report.encode()) <= 64 * 1024
    if field == 'patch':
        assert ended.patch == ''
    assert any(event.payload.get('text') == 'Earlier evidence' for event in ended.events)


async def test_event_size_count_limits_reserve_terminal_and_keep_existing_logs(database):
    runs = RunRepository(database)
    task = await approved_task(database)
    run = await runs.create(task.id, request(task))
    claimed = await runs.claim()
    with pytest.raises(TaskError) as too_large:
        await runs.append_event(run.id, claimed.worker_token, 'assistant', {'text': '界' * (32 * 1024)})
    assert too_large.value.status_code == 409
    # Fill the documented normal-event quota, including any lifecycle events already saved.
    existing = len((await runs.detail(task.id, run.id)).events)
    for index in range(255 - existing):
        assert await runs.append_event(run.id, claimed.worker_token, 'assistant', {'sequence': index})
    with pytest.raises(TaskError) as too_many:
        await runs.append_event(run.id, claimed.worker_token, 'assistant', {'text': 'Not saved'})
    assert too_many.value.status_code == 409
    ended = await runs.finish(run.id, claimed.worker_token, status='exhausted',
                             error='Event limit reached', cleanup_confirmed=True)
    assert ended.status == 'exhausted' and len(ended.events) == 256
    assert ended.events[-1].kind == 'state'
    assert [event.id for event in ended.events] == sorted({event.id for event in ended.events})
    assert not any(event.payload.get('text') == 'Not saved' for event in ended.events)


async def test_run_history_returns_latest_fifty_without_cross_task_records(database):
    tasks, runs = TaskRepository(database), RunRepository(database)
    task, other = await approved_task(database), await approved_task(database, 2)
    history = []
    for _ in range(51):
        current = await tasks.detail(task.id)
        run = await runs.create(task.id, request(current))
        history.append(run.id)
        await runs.cancel(task.id, run.id)
    other_run = await runs.create(other.id, request(other))
    listed = await runs.list(task.id)
    assert [run.id for run in listed] == list(reversed(history[-50:]))
    assert other_run.id not in {run.id for run in listed}
    assert (await runs.detail(task.id, history[0])).status == 'cancelled'
    with pytest.raises(TaskError) as missing:
        await runs.list(uuid4())
    assert missing.value.status_code == 404


async def test_claim_uses_immutable_source_and_goal_snapshots(database):
    from repopilot.persistence.database import TaskGoalRow
    runs = RunRepository(database)
    task = await approved_task(database)
    run = await runs.create(task.id, request(task))
    changed = task.source_snapshot.model_copy(update={'issue_body': 'Later issue text'})
    async with database.session() as session, session.begin():
        await session.execute(update(TaskRow).where(TaskRow.id == task.id).values(source_snapshot=changed.model_dump(mode='json')))
        await session.execute(update(TaskGoalRow).where(TaskGoalRow.task_id == task.id, TaskGoalRow.version == 1).values(content=goal('Later goal text').model_dump(mode='json')))
    claimed = await runs.claim()
    assert claimed.id == run.id
    assert claimed.source_snapshot.issue_body == task.source_snapshot.issue_body
    assert claimed.goal_content.summary == task.goals[-1].content.summary


async def test_runtime_configuration_is_fixed_and_cleanup_failure_stays_running(database):
    runs = RunRepository(database)
    task = await approved_task(database)
    run = await runs.create(task.id, request(task))
    claimed = await runs.claim()
    assert not await runs.cancellation_requested(run.id, claimed.worker_token)
    assert await runs.set_image_id(run.id, claimed.worker_token, 'sha256:fixed')
    assert await runs.set_model(run.id, claimed.worker_token, endpoint='https://model.example/v1', model='fixed-model')
    with pytest.raises(TaskError) as changed_image:
        await runs.set_image_id(run.id, claimed.worker_token, 'sha256:other')
    assert changed_image.value.status_code == 409
    with pytest.raises(TaskError) as changed_model:
        await runs.set_model(run.id, claimed.worker_token, endpoint='https://model.example/v1', model='other-model')
    assert changed_model.value.status_code == 409
    await runs.cancel(task.id, run.id)
    assert await runs.cancellation_requested(run.id, claimed.worker_token)
    assert await runs.record_cleanup_error(run.id, claimed.worker_token)
    detail = await runs.detail(task.id, run.id)
    assert detail.status == 'running' and detail.finished_at is None and detail.error
    assert detail.image_id == 'sha256:fixed'
    replacement = await runs.recover(run.id)
    assert await runs.cancellation_requested(run.id, claimed.worker_token)
    assert not await runs.record_cleanup_error(run.id, claimed.worker_token)
    assert not await runs.set_image_id(run.id, claimed.worker_token, 'sha256:late')
    ended = await runs.finish(run.id, replacement.worker_token, status='cancelled', cleanup_confirmed=True)
    assert ended.status == 'cancelled'


async def test_oversized_checks_fail_without_publishing_partial_results(database):
    from repopilot.domain.runs import RunCheck
    runs = RunRepository(database)
    task = await approved_task(database)
    run = await runs.create(task.id, request(task))
    claimed = await runs.claim()
    oversized = RunCheck(phase='final', command='python -m pytest', exit_code=0,
                         output='x' * (1024 * 1024 + 1), truncated=False)
    ended = await runs.finish(run.id, claimed.worker_token, status='completed',
                             patch='Small patch', checks=[oversized], cleanup_confirmed=True)
    assert ended.status == 'failed' and ended.error
    assert ended.patch == '' and ended.checks == []


async def test_live_check_evidence_survives_cancel_and_recovery(database):
    from repopilot.domain.runs import RunCheck
    from repopilot.execution.engine import _bounded_payload

    runs = RunRepository(database)
    task = await approved_task(database)
    run = await runs.create(task.id, request(task))
    claimed = await runs.claim()
    payload = _bounded_payload(dict(phase='baseline', command='python -m pytest',
                                   exit_code=1, output='失败\\n' * 20000, truncated=False))
    expected = RunCheck.model_validate(payload)
    assert expected.truncated and expected.exit_code == 1
    await runs.append_event(run.id, claimed.worker_token, 'check', payload)
    live = await runs.detail(task.id, run.id)
    assert live.status == 'running' and live.checks == [expected]
    await runs.cancel(task.id, run.id)
    recovered = await runs.recover(run.id)
    ended = await runs.finish(run.id, recovered.worker_token,
                              status='interrupted', cleanup_confirmed=True)
    assert ended.status == 'cancelled' and ended.checks == [expected]
