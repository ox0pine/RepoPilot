from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from repopilot.domain.tasks import GoalContent, SourceSnapshot, TaskError
from repopilot.persistence.database import Database, TaskRow
from repopilot.persistence.tasks import TaskRepository


@pytest_asyncio.fixture
async def database():
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('TEST_DATABASE_URL must point to a dedicated test PostgreSQL')
    schema = 'task_test_' + uuid4().hex
    admin = create_async_engine(url, hide_parameters=True)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    database = Database.__new__(Database)
    database.engine = create_async_engine(url, hide_parameters=True, connect_args={'server_settings': {'search_path': schema}})
    database.sessions = async_sessionmaker(database.engine, expire_on_commit=False)
    try:
        await database.initialize()
        yield database
    finally:
        await database.close()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


def source(number=1):
    return SourceSnapshot(repository_url='https://github.com/example/repo', baseline_commit='a' * 40,
                          issue_number=number, issue_title=f'Issue {number}', issue_body='Fixed snapshot',
                          issue_url=f'https://github.com/example/repo/issues/{number}',
                          issue_updated_at=datetime.now(timezone.utc), fetched_at=datetime.now(timezone.utc))


def goal(summary='解决问题'):
    return GoalContent(summary=summary, scope=['修复 Issue'], non_goals=[], acceptance_criteria=['复现问题后验证修复'], plan=['分析来源'], open_questions=[])


async def ready(repo, task):
    attempt = await repo.start_generation(task.id, task.revision, 'generate')
    return await repo.complete_generation(task.id, attempt.generation_id, goal(), 'test-model')


async def test_version_approval_revocation_and_persistence(database):
    repo = TaskRepository(database)
    task = await ready(repo, await repo.create(source()))
    attempt = await repo.start_generation(task.id, task.revision, 'revise', '仅修复 Issue')
    task = await repo.complete_generation(task.id, attempt.generation_id, goal('收窄范围'), 'test-model')
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
    task = await repo.complete_generation(task.id, retry.generation_id, goal('不升级依赖'), 'test-model')
    restored = await TaskRepository(database).detail(task.id)
    assert restored.source_snapshot.issue_body == 'Fixed snapshot'
    assert [g.content.summary for g in restored.goals] == ['解决问题', '收窄范围', '不升级依赖']
    assert [m.text for m in restored.messages if m.kind == 'feedback'] == ['仅修复 Issue', '不升级依赖']
    assert len([m for m in restored.messages if m.kind == 'approval']) == 1


async def test_single_generation_and_expired_result_cannot_overwrite(database):
    repo = TaskRepository(database)
    task = await repo.create(source())
    results = await asyncio.gather(repo.start_generation(task.id, 1, 'generate'), repo.start_generation(task.id, 1, 'generate'), return_exceptions=True)
    attempts = [r for r in results if not isinstance(r, Exception)]
    errors = [r for r in results if isinstance(r, TaskError)]
    assert len(attempts) == 1 and len(errors) == 1 and errors[0].status_code == 409
    running = await repo.detail(task.id)
    with pytest.raises(TaskError):
        await repo.approve(task.id, running.revision, 1)
    async with database.session() as session, session.begin():
        await session.execute(update(TaskRow).where(TaskRow.id == task.id).values(generation_deadline=text("clock_timestamp() - interval '1 second'")))
    expired = await repo.detail(task.id)
    assert expired.status == 'generation_failed' and expired.last_error == '目标生成已中断，请重试'
    fresh = await repo.start_generation(task.id, expired.revision, 'retry')
    assert await repo.complete_generation(task.id, attempts[0].generation_id, goal('迟到内容'), 'test') is None
    assert await repo.fail_generation(task.id, attempts[0].generation_id, '迟到错误') is None
    completed = await repo.complete_generation(task.id, fresh.generation_id, goal('有效内容'), 'test')
    assert completed.current_goal_version == 1 and completed.goals[0].content.summary == '有效内容'


async def test_statistics_and_stable_keyset_pagination(database):
    repo = TaskRepository(database)
    assert (await repo.stats()).model_dump() == dict(total=0, draft=0, generating=0, awaiting_approval=0, approved=0, generation_failed=0)
    tasks = [await repo.create(source(i + 1)) for i in range(12)]
    async with database.session() as session, session.begin():
        await session.execute(update(TaskRow).values(updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc)))
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


@pytest_asyncio.fixture
async def api_client(database, monkeypatch):
    import httpx
    from cryptography.fernet import Fernet
    from pydantic import SecretStr
    import repopilot.api.app as app_module
    from repopilot.config import AppSettings
    from repopilot.integration.task_sources import GitHubSourceClient
    from repopilot.integration.goals import GoalClient
    from repopilot.persistence.database import ModelSettingsRow, GitHubSettingsRow
    redis_url = os.environ.get('TEST_REDIS_URL')
    if not redis_url:
        pytest.skip('TEST_REDIS_URL must point to dedicated Redis')
    key = Fernet.generate_key()
    async with database.session() as session, session.begin():
        model = await session.get(ModelSettingsRow, 1)
        model.model = 'test-model'
        model.api_key = 'provider-secret-marker'
        github = await session.get(GitHubSettingsRow, 1)
        github.api_token_ciphertext = Fernet(key).encrypt(b'github-secret-marker').decode()
    calls = []
    controls = {'source_status': 200, 'model_status': 200, 'body': 'Snapshot body'}
    def source_transport(request):
        calls.append(str(request.url))
        if controls['source_status'] != 200:
            return httpx.Response(controls['source_status'], text='github-secret-marker')
        if '/commits/' in request.url.path:
            return httpx.Response(200, json={'sha':'a' * 40})
        return httpx.Response(200, json={'number':1, 'title':'Example issue', 'body':controls['body'], 'html_url':'https://github.com/example/repo/issues/1', 'updated_at':'2026-01-01T00:00:00Z'})
    def model_transport(request):
        import json
        calls.append(str(request.url))
        if controls['model_status'] != 200:
            return httpx.Response(controls['model_status'], text='provider-secret-marker')
        payload = json.loads(request.content)
        content = goal('模型实际输出').model_dump()
        if '不要升级依赖' in payload['messages'][-1]['content']:
            content['non_goals'] = ['依赖升级']
        return httpx.Response(200, json={'choices':[{'message':{'content':json.dumps(content, ensure_ascii=False)}}]})
    monkeypatch.setattr(app_module, 'Database', lambda _: database)
    monkeypatch.setattr(app_module, 'GitHubSourceClient', lambda: GitHubSourceClient(transport=httpx.MockTransport(source_transport)))
    monkeypatch.setattr(app_module, 'GoalClient', lambda: GoalClient(transport=httpx.MockTransport(model_transport)))
    runtime = AppSettings(_env_file=None, api_token=SecretStr('workbench-test'), github_credentials_key=SecretStr(key.decode()), database_url=os.environ['TEST_DATABASE_URL'], redis_url=redis_url)
    app = app_module.create_app(runtime)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test', headers={'Authorization':'Bearer workbench-test'}) as client:
        yield client, calls, controls


async def test_authenticated_api_goal_lifecycle(api_client):
    client, calls, controls = api_client
    payload = {'repository_url':'https://github.com/example/repo','baseline_commit':'a' * 40,'issue_url':'https://github.com/example/repo/issues/1'}
    assert (await client.get('/api/tasks/stats')).json()['total'] == 0
    response = await client.post('/api/tasks', json=payload)
    assert response.status_code == 201
    task = response.json()
    path = '/api/tasks/' + task['id']
    task = (await client.post(path + '/goal', json={'expected_revision':1,'action':'generate'})).json()
    assert task['goals'][0]['content']['summary'] == '模型实际输出'
    task = (await client.post(path + '/goal', json={'expected_revision':task['revision'],'action':'revise','feedback':'不要升级依赖'})).json()
    assert task['goals'][1]['content']['non_goals'] == ['依赖升级']
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
    from repopilot.persistence.settings import SettingsRepository, GitHubSettingsRepository
    from repopilot.persistence.database import ModelSettingsRow
    async with database.session() as session, session.begin():
        model = await session.get(ModelSettingsRow, 1)
        model.model = 'cancel-model'
    entered = asyncio.Event()
    async def transport(request):
        entered.set()
        await asyncio.Event().wait()
    repo = TaskRepository(database)
    task = await repo.create(source())
    service = TaskService(repo, SettingsRepository(database), GitHubSettingsRepository(database, None), GitHubSourceClient(), GoalClient(transport=httpx.MockTransport(transport)))
    pending = asyncio.create_task(service.generate(task.id, GenerateGoalRequest(expected_revision=1, action='generate')))
    await asyncio.wait_for(entered.wait(), 2)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    restored = await repo.detail(task.id)
    assert restored.status == 'generation_failed' and restored.goals == []
    assert restored.generation_deadline is None
