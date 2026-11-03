from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from factories import make_goal, make_source, ready
from sqlalchemy import text, update

from repopilot.api.app import reap_generation_leases
from repopilot.domain.tasks import TaskError
from repopilot.persistence.database import TaskRow
from repopilot.persistence.tasks import TaskRepository


async def test_version_approval_revocation_and_persistence(database):
    repo = TaskRepository(database)
    task = await ready(repo, await repo.create(make_source()))
    attempt = await repo.start_generation(task.id, task.revision, 'revise', '仅修复 Issue')
    task = await repo.complete_generation(task.id, attempt.generation_id, make_goal(summary='收窄范围'), 'test-model')
    with pytest.raises(TaskError) as stale:
        await repo.approve(task.id, task.revision, 1)
    assert stale.value.status_code == 409
    revision = task.revision
    task = await repo.approve(task.id, revision, 2)
    duplicate = await repo.approve(task.id, revision, 2)
    assert duplicate.revision == task.revision
    assert [m.kind for m in duplicate.messages] == ['source', 'goal', 'feedback', 'goal', 'approval']
    attempt = await repo.start_generation(task.id, task.revision, 'revise', '不升级依赖')
    failed = await repo.fail_generation(task.id, attempt.generation_id, '模型暂时不可用')
    assert failed.approved_at is None and failed.approved_goal_version is None
    with pytest.raises(TaskError) as rejected:
        await repo.approve(task.id, failed.revision, 2)
    assert rejected.value.status_code == 409
    retry = await repo.start_generation(task.id, failed.revision, 'retry')
    assert retry.feedback == '不升级依赖'
    task = await repo.complete_generation(task.id, retry.generation_id, make_goal(summary='不升级依赖'), 'test-model')
    restored = await TaskRepository(database).detail(task.id)
    assert restored.source_snapshot.issue_body == 'Fixed snapshot'
    assert [g.content.summary for g in restored.goals] == ['解决问题', '收窄范围', '不升级依赖']
    assert [m.text for m in restored.messages if m.kind == 'feedback'] == ['仅修复 Issue', '不升级依赖']
    assert len([m for m in restored.messages if m.kind == 'approval']) == 1


async def test_single_generation_and_expired_result_cannot_overwrite(database):
    repo = TaskRepository(database)
    task = await repo.create(make_source())
    results = await asyncio.gather(repo.start_generation(task.id, 1, 'generate'), repo.start_generation(task.id, 1, 'generate'), return_exceptions=True)
    attempts = [r for r in results if not isinstance(r, Exception)]
    errors = [r for r in results if isinstance(r, TaskError)]
    assert len(attempts) == 1 and len(errors) == 1 and errors[0].status_code == 409
    running = await repo.detail(task.id)
    with pytest.raises(TaskError):
        await repo.approve(task.id, running.revision, 1)
    async with database.session() as session, session.begin():
        await session.execute(update(TaskRow).where(TaskRow.id == task.id).values(generation_deadline=text("clock_timestamp() - interval '1 second'")))
    # Reads never write: the expired lease survives until the reaper retires it.
    stale = await repo.detail(task.id)
    assert (stale.status, stale.revision) == ('generating', running.revision)
    await repo.recover_expired(task.id)
    expired = await repo.detail(task.id)
    assert expired.status == 'generation_failed' and expired.last_error == '目标生成已中断，请重试'
    assert expired.revision == stale.revision + 1
    fresh = await repo.start_generation(task.id, expired.revision, 'retry')
    assert await repo.complete_generation(task.id, attempts[0].generation_id, make_goal(summary='迟到内容'), 'test') is None
    assert await repo.fail_generation(task.id, attempts[0].generation_id, '迟到错误') is None
    completed = await repo.complete_generation(task.id, fresh.generation_id, make_goal(summary='有效内容'), 'test')
    assert completed.current_goal_version == 1 and completed.goals[0].content.summary == '有效内容'


async def test_statistics_and_stable_keyset_pagination(database):
    repo = TaskRepository(database)
    assert (await repo.stats()).model_dump() == dict(total=0, draft=0, generating=0, awaiting_approval=0, approved=0, generation_failed=0)
    tasks = [await repo.create(make_source(i + 1)) for i in range(12)]
    async with database.session() as session, session.begin():
        await session.execute(update(TaskRow).values(updated_at=datetime(2026, 1, 1, tzinfo=UTC)))
    first = await repo.list(5)
    second = await repo.list(5, first.next_cursor)
    third = await repo.list(5, second.next_cursor)
    ids = [t.id for page in [first, second, third] for t in page.items]
    assert ids == sorted([t.id for t in tasks], reverse=True) and third.next_cursor is None
    approved = await ready(repo, tasks[0])
    await repo.approve(approved.id, approved.revision, 1)
    await ready(repo, tasks[1])
    generation = await repo.start_generation(tasks[2].id, 1, 'generate')
    await repo.fail_generation(tasks[2].id, generation.generation_id, '安全错误')
    await repo.start_generation(tasks[3].id, 1, 'generate')
    assert (await repo.stats()).model_dump() == dict(total=12, draft=8, generating=1, awaiting_approval=1, approved=1, generation_failed=1)
    with pytest.raises(TaskError) as invalid:
        await repo.list(cursor='not-a-cursor')
    assert invalid.value.status_code == 422
    with pytest.raises(TaskError) as missing:
        await repo.detail(uuid4())
    assert missing.value.status_code == 404


async def test_list_filters_by_status(database):
    repo = TaskRepository(database)
    tasks = [await repo.create(make_source(i + 1)) for i in range(5)]
    awaiting = await ready(repo, tasks[0])
    generation = await repo.start_generation(tasks[1].id, 1, 'generate')
    await repo.fail_generation(tasks[1].id, generation.generation_id, '安全错误')
    approved = await ready(repo, tasks[2])
    await repo.approve(approved.id, approved.revision, 1)

    approved_page = await repo.list(10, status='approved')
    assert [item.id for item in approved_page.items] == [tasks[2].id]
    failed_page = await repo.list(10, status='generation_failed')
    assert [item.id for item in failed_page.items] == [tasks[1].id]
    assert [item.id for item in (await repo.list(10, status='awaiting_approval')).items] == [awaiting.id]
    assert {item.id for item in (await repo.list(10, status='draft')).items} == {tasks[3].id, tasks[4].id}

    # The filter narrows before the keyset boundary, so paging stays consistent.
    first = await repo.list(1, status='draft')
    assert first.next_cursor is not None
    second = await repo.list(1, first.next_cursor, status='draft')
    assert {first.items[0].id, second.items[0].id} == {tasks[3].id, tasks[4].id}
    assert second.next_cursor is None
    assert len((await repo.list(10)).items) == 5


@pytest.mark.parametrize('field,value', [
    ('repository_url', 'git@github.com:example/repo.git'),
    ('repository_url', 'https://other.example/example/repo'),
    ('repository_url', 'https://user:pass@github.com/example/repo'),
    ('repository_url', 'https://github.com:8443/example/repo'),
    ('repository_url', 'https://github.com/example/repo/tree/main'),
    ('repository_url', 'https://github.com/example/repo?x=1'),
    ('repository_url', 'https://github.com/example/repo#readme'),
    ('issue_url', 'https://github.com/example/other/issues/1'),
    ('issue_url', 'https://github.com/example/repo/pull/1'),
    ('issue_url', 'https://github.com/example/repo/issues/0'),
    ('baseline_commit', 'abc123'),
    ('baseline_commit', 'g' * 40),
])
def test_invalid_source_input(field, value):
    from pydantic import ValidationError

    from repopilot.domain.tasks import CreateTask
    payload = dict(repository_url='https://github.com/example/repo', baseline_commit='a' * 40, issue_url='https://github.com/example/repo/issues/1')
    payload[field] = value
    with pytest.raises(ValidationError):
        CreateTask(**payload)


async def test_api_list_filters_by_status(api_client):
    client, _, _ = api_client
    payload = {'repository_url':'https://github.com/example/repo','baseline_commit':'a' * 40,'issue_url':'https://github.com/example/repo/issues/1'}
    created = (await client.post('/api/tasks', json=payload)).json()
    draft = await client.get('/api/tasks', params={'status':'draft'})
    assert draft.status_code == 200 and [item['id'] for item in draft.json()['items']] == [created['id']]
    assert (await client.get('/api/tasks', params={'status':'approved'})).json()['items'] == []
    invalid = await client.get('/api/tasks', params={'status':'not-a-status'})
    assert invalid.status_code == 422


async def test_authenticated_api_goal_lifecycle(api_client):
    client, calls, controls = api_client
    payload = {'repository_url':'https://github.com/example/repo','baseline_commit':'a' * 40,'issue_url':'https://github.com/example/repo/issues/1'}
    assert (await client.get('/api/tasks/stats')).json()['total'] == 0
    response = await client.post('/api/tasks', json=payload)
    assert response.status_code == 201
    task = response.json()
    path = '/api/tasks/' + task['id']
    snapshot = task['source_snapshot']
    assert snapshot['repository_context']['files'][0]['content'] == '# Fixture repository\n'
    source_calls = [url for url in calls if 'api.github.com' in url]
    task = (await client.post(path + '/goal', json={'expected_revision':1,'action':'generate'})).json()
    assert task['goals'][0]['content']['summary'] == '模型实际输出'
    task = (await client.post(path + '/goal', json={'expected_revision':task['revision'],'action':'revise','feedback':'不要升级依赖'})).json()
    assert task['goals'][1]['content']['non_goals'] == ['依赖升级']
    assert task['source_snapshot'] == snapshot
    assert [url for url in calls if 'api.github.com' in url] == source_calls
    assert (await client.post(path + '/approve', json={'expected_revision':task['revision'],'goal_version':1})).status_code == 409
    count = len(calls)
    response = await client.post(path + '/approve', json={'expected_revision':task['revision'],'goal_version':2})
    assert response.status_code == 200 and response.json()['status'] == 'approved'
    assert len(calls) == count
    assert (await client.get('/api/tasks/stats')).json()['approved'] == 1
    assert (await client.get('/api/tasks?cursor=garbage')).status_code == 422
    assert (await client.get('/api/tasks?limit=51')).status_code == 422
    assert (await client.get('/api/tasks/' + str(uuid4()))).status_code == 404
    client.headers.clear()
    for method, route, body in [('GET','/api/tasks',None),('GET','/api/tasks/stats',None),('GET',path,None),('POST','/api/tasks',payload),('POST',path+'/goal',{'expected_revision':1,'action':'generate'}),('POST',path+'/approve',{'expected_revision':1,'goal_version':1})]:
        assert (await client.request(method, route, json=body)).status_code == 401


async def test_upstream_failures_never_authenticate_or_create_partial_tasks(api_client):
    client, calls, controls = api_client
    payload = {'repository_url':'https://github.com/example/repo','baseline_commit':'a' * 40,'issue_url':'https://github.com/example/repo/issues/1'}
    controls['source_status'] = 403
    response = await client.post('/api/tasks', json=payload)
    assert response.status_code == 422 and 'github-secret-marker' not in response.text
    assert (await client.get('/api/tasks/stats')).json()['total'] == 0
    controls['source_status'] = 200
    task = (await client.post('/api/tasks', json=payload)).json()
    controls['body'] = 'Remote issue changed later'
    controls['model_status'] = 401
    response = await client.post('/api/tasks/' + task['id'] + '/goal', json={'expected_revision':1,'action':'generate'})
    assert response.status_code == 502 and 'provider-secret-marker' not in response.text
    detail = (await client.get('/api/tasks/' + task['id'])).json()
    assert detail['status'] == 'generation_failed' and detail['goals'] == []
    assert detail['source_snapshot']['issue_body'] == 'Snapshot body'
    assert (await client.get('/api/tasks/stats')).status_code == 200


async def test_missing_github_and_model_are_actionable(api_client, database):
    from repopilot.persistence.database import GitHubSettingsRow, ModelSettingsRow
    client, calls, controls = api_client
    payload = {'repository_url':'https://github.com/example/repo','baseline_commit':'a' * 40,'issue_url':'https://github.com/example/repo/issues/1'}
    task = (await client.post('/api/tasks', json=payload)).json()
    async with database.session() as session, session.begin():
        github = await session.get(GitHubSettingsRow, 1)
        github.api_token_ciphertext = ''
        model = await session.get(ModelSettingsRow, 1)
        model.model = ''
    count = len(calls)
    assert (await client.post('/api/tasks', json=payload)).status_code == 503
    assert (await client.post('/api/tasks/' + task['id'] + '/goal', json={'expected_revision':1,'action':'generate'})).status_code == 503
    assert len(calls) == count
    assert (await client.get('/api/tasks/' + task['id'])).json()['status'] == 'generation_failed'


async def test_cancelled_service_attempt_is_persisted_as_failed(database):
    import httpx

    from repopilot.application.tasks import TaskService
    from repopilot.domain.tasks import GenerateGoalRequest
    from repopilot.integration.goals import GoalClient
    from repopilot.integration.task_sources import GitHubSourceClient
    from repopilot.persistence.database import ModelSettingsRow
    from repopilot.persistence.settings import GitHubSettingsRepository, SettingsRepository
    async with database.session() as session, session.begin():
        model = await session.get(ModelSettingsRow, 1)
        model.model = 'cancel-model'
    entered = asyncio.Event()
    async def transport(request):
        entered.set()
        await asyncio.Event().wait()
    repo = TaskRepository(database)
    task = await repo.create(make_source())
    service = TaskService(repo, SettingsRepository(database), GitHubSettingsRepository(database, None), GitHubSourceClient(), GoalClient(transport=httpx.MockTransport(transport)))
    pending = asyncio.create_task(service.generate(task.id, GenerateGoalRequest(expected_revision=1, action='generate')))
    await asyncio.wait_for(entered.wait(), 2)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    restored = await repo.detail(task.id)
    assert restored.status == 'generation_failed' and restored.goals == []
    assert restored.generation_deadline is None


@pytest.mark.parametrize('control,expected', [
    ('context_status', 422), ('bad_blob', 502), ('invalid_tree', 502),
])
async def test_context_failure_does_not_persist_partial_task(api_client, control, expected):
    client, calls, controls = api_client
    controls[control] = 403 if control == 'context_status' else True
    payload = {'repository_url': 'https://github.com/example/repo',
               'baseline_commit': 'a' * 40,
               'issue_url': 'https://github.com/example/repo/issues/1'}
    response = await client.post('/api/tasks', json=payload)
    assert response.status_code == expected
    assert 'github-secret-marker' not in response.text
    assert (await client.get('/api/tasks/stats')).json()['total'] == 0
    assert (await client.get('/api/tasks')).json()['items'] == []


class _CountingReaper:
    """Repository stub: each sweep records a call and may stop the loop."""

    def __init__(self, stop_after: int, *, fail_first: bool = False) -> None:
        self.calls = 0
        self.stop_after = stop_after
        self.fail_first = fail_first
        self.stop = asyncio.Event()

    async def recover_expired(self) -> None:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise RuntimeError('transient database failure')
        if self.calls >= self.stop_after:
            self.stop.set()


async def test_reaper_loop_sweeps_until_stop():
    fake = _CountingReaper(1)
    await asyncio.wait_for(reap_generation_leases(fake, fake.stop, 5), 1)
    assert fake.calls == 1


async def test_reaper_loop_survives_a_failed_sweep():
    fake = _CountingReaper(2, fail_first=True)
    # A failed sweep still waits for the next tick, so the interval must be short here.
    await asyncio.wait_for(reap_generation_leases(fake, fake.stop, 0.01), 5)
    assert fake.calls == 2
