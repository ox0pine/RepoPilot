"""Shared fixtures: isolated database schema, Docker workspace, and API client."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from factories import make_goal, make_source
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import repopilot.api.app as app_module
from repopilot.config import AppSettings
from repopilot.domain.context import RepositoryContext
from repopilot.execution import workspace as workspace_module
from repopilot.execution.git import GitHubGitCredentials
from repopilot.execution.workspace import Workspace
from repopilot.integration.github import GitHubClient
from repopilot.integration.goals import GoalClient
from repopilot.integration.task_sources import GitHubSourceClient
from repopilot.persistence.database import Database, GitHubSettingsRow, ModelSettingsRow

DOCKER_ENABLED = os.environ.get('REPOPILOT_DOCKER_TESTS') == '1'
real_docker = pytest.mark.skipif(
    not DOCKER_ENABLED, reason='Set REPOPILOT_DOCKER_TESTS=1 for real Docker coverage',
)
WORKBENCH_TOKEN = 'workbench-test'
CONTAINER_TOKEN = 'container-fixture-secret-not-for-container'


def workspace_fixture_source():
    return make_source(
        repository_url='https://github.com/example/project',
        issue_number=7, issue_title='Fix addition', issue_body='Fix calc.py',
        issue_url='https://github.com/example/project/issues/7',
        repository_context=RepositoryContext(
            commit='a' * 40, tree=[], files=[],
            omissions=['Clone fixture is prepared separately'], tree_truncated=False,
        ),
    )


def schema_database(url: str, schema: str, *, pool_pre_ping: bool = False) -> Database:
    """Bind the real Database to one isolated schema without its default engine."""
    engine = create_async_engine(
        url, hide_parameters=True, pool_pre_ping=pool_pre_ping,
        connect_args={'server_settings': {'search_path': schema}},
    )
    isolated = Database.__new__(Database)
    isolated.engine = engine
    isolated.sessions = async_sessionmaker(engine, expire_on_commit=False)
    return isolated


@pytest_asyncio.fixture
async def database():
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('TEST_DATABASE_URL must point to a dedicated test PostgreSQL')
    schema = 'test_' + uuid4().hex
    admin = create_async_engine(url, hide_parameters=True)
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        isolated = schema_database(url, schema, pool_pre_ping=True)
        try:
            await isolated.initialize()
            yield isolated
        finally:
            await isolated.close()
    finally:
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


@pytest_asyncio.fixture
async def docker_workspace(monkeypatch):
    """Factory: async with docker_workspace(files=None, setup=False) as (ws, image)."""
    if not DOCKER_ENABLED:
        pytest.skip('Set REPOPILOT_DOCKER_TESTS=1 for real Docker coverage')
    assert shutil.which('docker'), 'Docker coverage enabled but the Docker CLI is unavailable'
    probe = await asyncio.create_subprocess_exec(
        'docker', 'info', '--format', '{{.ServerVersion}}',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await probe.communicate()
    assert probe.returncode == 0, (
        f'Docker coverage enabled but the Docker daemon is unavailable: {output.decode()}'
    )
    image = os.environ.get('REPOPILOT_TEST_IMAGE', 'repopilot-dev:local')

    @asynccontextmanager
    async def open_workspace(files: dict[str, str] | None = None, *, setup: bool = False):
        content = {'calc.py': 'def add(a, b): return a - b\n'} if files is None else files

        async def clone_fixture(repository_url, commit, destination, credentials, *, strategy, timeout=90):
            destination.mkdir()
            (destination / '.git').mkdir()
            (destination / '.git/config').write_text('[core]\nrepositoryformatversion = 0\n')
            for name, value in content.items():
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(value)

        with monkeypatch.context() as patch:
            patch.setattr(workspace_module, 'clone_fixed_commit', clone_fixture)
            workspace = Workspace(uuid4())
            try:
                image_id = await workspace.prepare(
                    source=workspace_fixture_source(),
                    github_credentials=GitHubGitCredentials(api_token=CONTAINER_TOKEN, private_key=''),
                    git_auth_strategy='https_token', image=image,
                )
                if setup:
                    result = await workspace.setup()
                    assert result.exit_code == 0, result.output
                yield workspace, image_id
            finally:
                await workspace.cleanup()

    yield open_workspace


@pytest_asyncio.fixture
async def app_client(database, monkeypatch):
    """Factory: open(token=..., master_key=..., *transport=...) yields an API client."""
    redis_url = os.environ.get('TEST_REDIS_URL')
    if not redis_url:
        pytest.skip('TEST_REDIS_URL must point to a dedicated test Redis')

    @asynccontextmanager
    async def open_app(
        *, token: str = WORKBENCH_TOKEN, master_key: bytes | str | None = None,
        github_transport: httpx.AsyncBaseTransport | None = None,
        source_transport: httpx.AsyncBaseTransport | None = None,
        model_transport: httpx.AsyncBaseTransport | None = None,
    ):
        key = master_key.decode() if isinstance(master_key, bytes) else master_key
        runtime = AppSettings(
            _env_file=None, api_token=SecretStr(token),
            github_credentials_key=SecretStr(key) if key is not None else None,
            database_url=os.environ['TEST_DATABASE_URL'], redis_url=redis_url,
        )
        with monkeypatch.context() as patch:
            patch.setattr(app_module, 'Database', lambda url: database)
            if github_transport is not None:
                patch.setattr(
                    app_module, 'GitHubClient', lambda: GitHubClient(transport=github_transport),
                )
            if source_transport is not None:
                patch.setattr(
                    app_module, 'GitHubSourceClient',
                    lambda: GitHubSourceClient(transport=source_transport),
                )
            if model_transport is not None:
                patch.setattr(
                    app_module, 'GoalClient', lambda: GoalClient(transport=model_transport),
                )
            app = app_module.create_app(runtime)
            async with app.router.lifespan_context(app), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url='http://testserver',
                headers={'Authorization': f'Bearer {token}'},
            ) as client:
                yield client

    yield open_app


@asynccontextmanager
async def control_api(app_client, database):
    """Seed credentials and a controllable GitHub/model transport for API tests."""
    key = Fernet.generate_key()
    async with database.session() as session, session.begin():
        model = await session.get(ModelSettingsRow, 1)
        model.model = 'test-model'
        model.api_key = 'provider-secret-marker'
        github = await session.get(GitHubSettingsRow, 1)
        github.api_token_ciphertext = Fernet(key).encrypt(b'github-secret-marker').decode()
    calls = []
    controls = {'source_status': 200, 'model_status': 200, 'body': 'Snapshot body',
                'context_status': 200, 'bad_blob': False, 'invalid_tree': False}

    def source_transport(request):
        calls.append(str(request.url))
        if controls['source_status'] != 200:
            return httpx.Response(controls['source_status'], text='github-secret-marker')
        if '/commits/' in request.url.path:
            return httpx.Response(200, json={'sha': 'a' * 40})
        content = b'# Fixture repository\n'
        blob_sha = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
        if '/git/' in request.url.path and controls['context_status'] != 200:
            return httpx.Response(controls['context_status'], text='github-secret-marker')
        if '/git/trees/' in request.url.path:
            if controls['invalid_tree']:
                return httpx.Response(200, json={'tree': 'invalid'})
            return httpx.Response(200, json={
                'sha': 'b' * 40, 'truncated': False,
                'tree': [{'path': 'README.md', 'type': 'blob', 'mode': '100644',
                          'sha': blob_sha, 'size': len(content)}],
            })
        if '/git/blobs/' in request.url.path:
            return httpx.Response(200, content=b'wrong hash' if controls['bad_blob'] else content)
        return httpx.Response(200, json={
            'number': 1, 'title': 'Example issue', 'body': controls['body'],
            'html_url': 'https://github.com/example/repo/issues/1',
            'updated_at': '2026-01-01T00:00:00Z',
        })

    def model_transport(request):
        calls.append(str(request.url))
        if controls['model_status'] != 200:
            return httpx.Response(controls['model_status'], text='provider-secret-marker')
        payload = json.loads(request.content)
        content = make_goal(summary='模型实际输出').model_dump()
        if '不要升级依赖' in payload['messages'][-1]['content']:
            content['non_goals'] = ['依赖升级']
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(content, ensure_ascii=False)}}]})

    async with app_client(
        master_key=key, github_transport=httpx.MockTransport(source_transport),
        source_transport=httpx.MockTransport(source_transport),
        model_transport=httpx.MockTransport(model_transport),
    ) as client:
        yield client, calls, controls


@pytest_asyncio.fixture
async def api_client(app_client, database):
    async with control_api(app_client, database) as value:
        yield value
